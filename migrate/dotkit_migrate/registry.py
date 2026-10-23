"""The migrators in their order, and the Session that runs them.

MIGRATORS is the one source of order: scan order, page order, plan order,
and the order of the "Carried over" rows. The Session scans once, asks each
migrator for its pages, and builds a fresh plan for each set of choices.
Core, not migrators: the identity scan, nix/user.nix, the keymap, the scan
warnings, the checks, and the writer.
"""

from __future__ import annotations

import getpass
import re
import socket
from collections.abc import Callable
from pathlib import Path
from typing import List, Optional

from . import checks, writer
from . import docs as docs_mod
from . import report as report_mod
from .context import Context, expand, tilde
from .docs import nix_string
from .migrators import _rest, homebrew
from .model import Choices, Facts, Identity, Page, Plan

MIGRATORS = (
    homebrew.Homebrew(),
    _rest.Rest(),
)


def scan_identity(ctx: Context, warnings: List[str]) -> Identity:
    """Who and where. A part that fails is a warning; the user and the hostname have defaults."""
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = ctx.env.get("USER", "")
    identity = Identity(home=str(ctx.home), user=user, hostname=socket.gethostname().split(".")[0])
    try:
        runner = ctx.runner
        git = runner.which("git", ("/usr/bin",)) or "/usr/bin/git"
        identity.git_name = (runner.run([git, "config", "--global", "--includes", "--get", "user.name"],
                                        timeout=30) or "").strip()
        identity.git_email = (runner.run([git, "config", "--global", "--includes", "--get", "user.email"],
                                         timeout=30) or "").strip()
        out = runner.run(["/usr/bin/dscl", ".", "-read", f"/Users/{identity.user}", "UserShell"], timeout=30) or ""
        m = re.search(r"UserShell:\s*(\S+)", out)
        identity.login_shell = m.group(1) if m else ctx.env.get("SHELL", "")
    except Exception as e:  # noqa: BLE001 - a failed part must not stop the scan
        warnings.append(f"identity: {type(e).__name__}: {e}")
    return identity


class Session:
    def __init__(self, ctx: Context, migrators=MIGRATORS, strict: bool = False,
                 check_syntax: Optional[Callable[[str, str], bool]] = None):
        self.ctx = ctx
        self.migrators = tuple(migrators)
        self.strict = strict
        # check_syntax(shell, text): True when the text parses. None: no check.
        self.check_syntax = check_syntax
        self._facts: Optional[Facts] = None
        self._pages: Optional[List[Page]] = None

    def scan(self) -> Facts:
        """Every scan, once: the identity first, then the migrators in order."""
        if self._facts is None:
            warnings: List[str] = []
            identity = scan_identity(self.ctx, warnings)
            by_key = {}
            for m in self.migrators:
                try:
                    found = m.scan(self.ctx)
                except Exception as e:  # noqa: BLE001 - one failed scan must not stop the others
                    warnings.append(f"{m.key}: {type(e).__name__}: {e}")
                    continue
                by_key[m.key] = found
                warnings += getattr(found, "warnings", [])
            self._facts = Facts(identity, self.ctx.base, self.check_syntax, by_key, warnings)
        return self._facts

    def pages(self) -> List[Page]:
        """The questions, one page per source, in MIGRATORS order. Pages without items are left out."""
        if self._pages is None:
            facts = self.scan()
            # A migrator whose scan failed has no pages and plans nothing.
            self._pages = [p for m in self.migrators if facts.get(m.key) is not None
                           for p in m.pages(facts) if p.items]
        return self._pages

    def defaults(self, repo: str) -> Choices:
        return Choices.defaults(repo, self.pages())

    def build(self, choices: Choices) -> Plan:
        """The plan for these choices, from fresh documents."""
        facts = self.scan()
        ident = facts.identity
        repo = Path(expand(choices.repo, ident.home))
        docs = docs_mod.Docs("setup", facts.base.template, repo, home=ident.home, host=ident.hostname,
                             strict=self.strict)
        for m in self.migrators:
            if facts.get(m.key) is None:
                continue
            txn = docs.begin(m.key)
            m.plan(facts, choices, txn)
            txn.commit()
        repo_rel = tilde(repo, ident.home)
        core = docs.begin("core")
        core.file("nix/user.nix").set(
            "# Your macOS account and this repository. dotkit's scripts/setup writes it.\n"
            "rec {\n"
            f"  name = {nix_string(ident.user)};\n"
            '  home = "/Users/${name}";\n'
            f'  dotfiles = "${{home}}/{nix_string(repo_rel[2:])[1:-1]}";\n'
            "}\n")
        core.shell_nix().set_keymap(choices.keymap)
        for w in facts.warnings:
            core.report.todo("Scan errors", "Part of the scan failed. Check these by hand.", [w])
        core.commit()
        plan = writer.render(docs)
        checks.check(docs, plan)
        report = report_mod.render(docs.report, ident.hostname, repo_rel)
        plan.report = "".join(writer.mask_lines(report.splitlines(keepends=True)))
        plan.files["MIGRATION.md"] = plan.report
        plan.summary = writer.mask_lines(checks.summary(plan, docs.report, ident.git_name, ident.git_email))
        return plan

