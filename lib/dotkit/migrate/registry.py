"""The migrators in their order, and the Session that runs them.

MIGRATORS is the one source of order: scan order, page order, plan order,
and the order of the "Carried over" rows. The Session scans once, asks each
migrator for its pages, and builds a fresh plan for each set of choices.
Core, not migrators: the identity scan, nix/user.nix, the keymap, the scan
warnings, the "Login shell" to-do, the problems, the checks, and the writer.

Each scan, pages, plan, and adopt of a migrator is isolated (ARCHITECTURE.md,
section 6): an exception there is a Failure, the migrator contributes
nothing, and the rest of the run goes on. Strict mode re-raises instead.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
import stat
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import checks, secrets, secrets_file, writer
from . import docs as docs_mod
from . import report as report_mod
from .context import Context, expand, tilde
from .declared import Declared
from .docs import nix_string
from .migrators import (Migrator, appstore, built_tools, config_files, frameworks, homebrew, leftovers, logins, node_tools,
                        other_config, python_tools, runtimes, shell)
from .model import AdoptOptions, AdoptResult, Choices, Facts, Failure, Identity, Page, Plan, warnings_of

MIGRATORS = (
    homebrew.Homebrew(),
    appstore.AppStore(),
    node_tools.NodeTools(),
    python_tools.PythonTools(),
    built_tools.BuiltTools(),
    runtimes.Runtimes(),
    shell.Shell(),
    frameworks.Frameworks(),
    config_files.ConfigFiles(),
    other_config.OtherConfig(),
    logins.Logins(),
    leftovers.Leftovers(),
)


class IdentityScan:
    """Who and where: the core scan, before the migrators. It is isolated like a migrator.

    If it fails, the user is $USER and the hostname is empty; an empty user
    name is a blocker, because nix/user.nix needs it.
    """

    key = "identity"
    title = "Your account and Git identity"

    def scan(self, ctx: Context) -> Identity:
        try:
            user = getpass.getuser()
        except (KeyError, OSError):
            user = ctx.env.get("USER", "")
        identity = Identity(home=str(ctx.home), user=user, hostname=socket.gethostname().split(".")[0])
        runner = ctx.runner
        git = runner.which("git", ("/usr/bin",)) or "/usr/bin/git"
        identity.git_name = (runner.run([git, "config", "--global", "--includes", "--get", "user.name"],
                                        timeout=30) or "").strip()
        identity.git_email = (runner.run([git, "config", "--global", "--includes", "--get", "user.email"],
                                         timeout=30) or "").strip()
        out = runner.run(["/usr/bin/dscl", ".", "-read", f"/Users/{identity.user}", "UserShell"], timeout=30) or ""
        m = re.search(r"UserShell:\s*(\S+)", out)
        identity.login_shell = m.group(1) if m else ctx.env.get("SHELL", "")
        p = ctx.home / ".secrets"
        if os.path.lexists(p):
            try:
                identity.secrets_mode = stat.S_IMODE(p.stat().st_mode)
                identity.secrets = secrets_file.read(p.read_text(encoding="utf-8", errors="replace"))
            except OSError as e:
                identity.secrets_error = e.strerror or str(e)
        return identity

    def manual_steps(self, ctx: Context) -> str:
        return "nix/user.nix uses the account `$USER`; check it, and your name and email in ~/.gitconfig.user."


IDENTITY = IdentityScan()

# The lists that `dotkit migrate` adds to, in the order of its output, with their headers.
ADOPT_LISTS = (("nix/brews.txt", "Homebrew formulae"), ("nix/casks.txt", "Homebrew casks"),
               ("nix/taps.txt", "Homebrew taps"), ("nix/mas.txt", "App Store apps"))

# --- Failure text: short, with the home as ~, and masked line by line (ARCHITECTURE.md, section 5)

PACKAGE = Path(__file__).resolve().parent
SECRET_LINE = "(this line has a secret; not shown)"
ERROR_LIMIT = 200


def masked_lines(text: str, home: str) -> List[str]:
    """The lines of a text with the home as ~, each line with a secret replaced."""
    if home:
        text = text.replace(home, "~")
    return [SECRET_LINE if secrets.line_secret(line) else line for line in text.splitlines()]


def error_text(e: BaseException, home: str) -> str:
    """`KeyError: 'versions'`: the type and the message on one line, at most ERROR_LIMIT characters."""
    text = " ".join(line.strip() for line in masked_lines(f"{type(e).__name__}: {e}", home) if line.strip())
    # The join can make a secret of a name on one line and a value on the next.
    if secrets.line_secret(text):
        text = SECRET_LINE
    return text if len(text) <= ERROR_LIMIT else text[:ERROR_LIMIT - 1] + "…"


def trace_text(e: BaseException, home: str) -> str:
    """The traceback for --debug, masked like the error."""
    return "\n".join(masked_lines("".join(traceback.format_exception(type(e), e, e.__traceback__)), home))


def where_of(e: BaseException) -> str:
    """The last frame of the traceback inside dotkit.migrate: "migrators/runtimes.py:88 in plan"."""
    for frame in reversed(traceback.extract_tb(e.__traceback__)):
        path = Path(frame.filename).resolve()
        if PACKAGE in path.parents:
            return f"{path.relative_to(PACKAGE).as_posix()}:{frame.lineno} in {frame.name}"
    return ""


def _short(text: str, limit: int = 60) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    more = "…" if len(first) > limit or "\n" in text.strip() else ""
    return first[:limit] + more


class Session:
    def __init__(self, ctx: Context, migrators: Optional[Sequence[Migrator]] = None, strict: bool = False,
                 check_syntax: Optional[Callable[[str, str], bool]] = None):
        self.ctx = ctx
        # None: MIGRATORS as it is now, so that a test that patches registry.MIGRATORS reaches every Session.
        self.migrators = tuple(MIGRATORS if migrators is None else migrators)
        # Strict: a crash, a conflict, or an unknown section raises where it happens (tests and CI).
        self.strict = strict
        # check_syntax(shell, text): True when the text parses. None: no check.
        self.check_syntax = check_syntax
        self._facts: Optional[Facts] = None
        self._pages: Optional[List[Page]] = None
        # The failures of the scan and of the pages, and the migrators that have no part in the plans.
        self.failures: List[Failure] = []
        self._out: Set[str] = set()
        self._traces: Dict[Tuple[str, str], str] = {}

    # --- Isolation (ARCHITECTURE.md, section 6)

    def _fail(self, m, phase: str, e: Exception, failures: List[Failure]) -> None:
        """Records the failure of a migrator in failures. Called in an except block: strict mode re-raises."""
        home = str(self.ctx.home)
        failures.append(Failure(m.key, m.title, phase, error_text(e, home), where_of(e), m.manual_steps(self.ctx)))
        self._traces[(m.key, phase)] = trace_text(e, home)
        if self.strict:
            raise  # noqa: PLE0704 - the exception of the except block that called this

    def _skip(self, out: Set[str], failures: List[Failure]) -> None:
        """The migrators whose needs failed or were skipped: they are out too, with a "skipped" failure."""
        titles = {m.key: m.title for m in self.migrators}
        changed = True
        while changed:
            changed = False
            for m in self.migrators:
                missing = [n for n in m.needs if n in out]
                if m.key in out or not missing:
                    continue
                out.add(m.key)
                changed = True
                failures.append(Failure(m.key, m.title, "skipped", "", "", m.manual_steps(self.ctx),
                                        reason=f"{titles.get(missing[0], missing[0])} failed"))

    def trace(self, f: Failure) -> str:
        """The masked traceback of a failure, for --debug; "" for a skipped part or a conflict."""
        return self._traces.get((f.migrator, f.phase), "")

    def scan_warning(self) -> str:
        """The warning line of the first screen: the parts without a screen. "" when there are none."""
        self.pages()
        return report_mod.scan_warning(self.failures) if self.failures else ""

    # --- The phases

    def scan(self) -> Facts:
        """Every scan, once: the identity first, then the migrators in order. A failed scan has no facts.

        After the scans, each migrator's mise_commands, from these facts, go
        into Facts.mise_commands. One that raises fails its migrator as its
        scan would: a Failure of the scan, and no facts.
        """
        if self._facts is None:
            ctx = self.ctx
            try:
                identity = IDENTITY.scan(ctx)
            except Exception as e:  # noqa: BLE001 - isolated: the defaults, and a Failure
                self._fail(IDENTITY, "scan", e, self.failures)
                identity = Identity(home=str(ctx.home), user=ctx.env.get("USER", ""))
            by_key = {}
            for m in self.migrators:
                try:
                    found = m.scan(ctx)
                except Exception as e:  # noqa: BLE001 - isolated: its facts and its warnings are gone
                    self._fail(m, "scan", e, self.failures)
                    self._out.add(m.key)
                    continue
                by_key[m.key] = found
            scanned = Facts(identity, ctx.base, self.check_syntax, by_key, warnings_of(by_key))
            commands = {}
            for m in self.migrators:
                if m.key not in by_key:
                    continue
                try:
                    commands[m.key] = {k: tuple(v) for k, v in m.mise_commands(scanned).items()}
                except Exception as e:  # noqa: BLE001 - isolated: as a failed scan
                    self._fail(m, "scan", e, self.failures)
                    self._out.add(m.key)
                    del by_key[m.key]
            self._facts = Facts(identity, ctx.base, self.check_syntax, by_key, warnings_of(by_key), commands)
        return self._facts

    def pages(self) -> List[Page]:
        """The questions, one page per source, in MIGRATORS order. Pages without items are left out.

        A migrator whose pages fail has no pages and plans nothing. The pages
        of the others are asked again without its facts, as if it were not in
        the registry.
        """
        if self._pages is None:
            facts = self.scan()
            passes = 0
            while True:
                passes += 1
                # Each migrator fails once at most: one pass more than there are migrators.
                assert passes <= len(self.migrators) + 1, "the pages started again too often"
                self._skip(self._out, self.failures)
                view = facts.without(self._out)
                pages: List[Page] = []
                failed = False
                for m in self.migrators:
                    if m.key in self._out:
                        continue
                    try:
                        pages += [p for p in m.pages(view) if p.items]
                    except Exception as e:  # noqa: BLE001 - isolated: no pages, and no plan
                        self._fail(m, "pages", e, self.failures)
                        self._out.add(m.key)
                        failed = True
                        break
                if not failed:
                    break
            self._pages = pages
        return self._pages

    def defaults(self, repo: str) -> Choices:
        return Choices.defaults(repo, self.pages())

    def _plans(self, facts: Facts, choices: Choices, docs: docs_mod.Docs, out: Set[str],
               failures: List[Failure]) -> bool:
        """Each migrator's plan in its own transaction. False when one failed: it is rolled back, and out."""
        for m in self.migrators:
            if m.key in out:
                continue
            txn = docs.begin(m.key)
            try:
                m.plan(facts, choices, txn)
                txn.commit()
            except Exception as e:  # noqa: BLE001 - isolated: it contributes nothing
                txn.rollback()
                self._fail(m, "plan", e, failures)
                out.add(m.key)
                return False
        return True

    def build(self, choices: Choices) -> Plan:
        """The plan for these choices, from fresh documents.

        A migrator whose plan fails is rolled back and left out. The others
        then plan again, from fresh documents and without its facts, so the
        plan is the one of the registry without it.
        """
        facts = self.scan()
        self.pages()
        ident = facts.identity
        repo = Path(expand(choices.repo, ident.home))
        out = set(self._out)
        failures = list(self.failures)
        check = self._check_once()
        passes = 0
        while True:
            passes += 1
            # Each migrator fails once at most: one pass more than there are migrators.
            assert passes <= len(self.migrators) + 1, "the build started again too often"
            view = facts.without(out)
            view.check_syntax = check
            docs = docs_mod.Docs("setup", facts.base.template, repo, home=ident.home, host=ident.hostname,
                                 strict=self.strict, secrets=ident.secrets)
            if self._plans(view, choices, docs, out, failures):
                break
            self._skip(out, failures)
        repo_rel = tilde(repo, ident.home)
        core = docs.begin("core")
        core.file("nix/user.nix").set(
            "# Your macOS account and this repository. dotkit's scripts/setup writes it.\n"
            "rec {\n"
            f"  name = {nix_string(ident.user)};\n"
            '  home = "/Users/${name}";\n'
            f'  dotfiles = "${{home}}/{nix_string(repo_rel[2:])[1:-1]}";\n'
            "}\n")
        if not ident.user:
            core.report.blocker("dotkit could not find the name of your macOS account. Set USER to it and run "
                                "setup again.")
        core.shell_nix().set_keymap(choices.keymap)
        if docs.secret_names() and ident.secrets_error:
            core.report.blocker(f"~/.secrets cannot be read ({ident.secrets_error}), so dotkit cannot add the "
                                "variables of your secrets to it. Fix it and run setup again.")
        if ident.secrets_mode is not None and ident.secrets_mode & 0o077:
            core.report.todo("Secrets", "", [f"~/.secrets can be read by other users (mode "
                                             f"{ident.secrets_mode:o}). Run `chmod 600 ~/.secrets`."])
        for w in view.warnings:
            core.report.todo("Scan warnings", "Part of the scan could not read everything. Check these by hand.",
                             [w])
        # It reads only the identity, so it stays when the shell scan fails.
        login_shell = ident.login_shell
        shell = os.path.basename(login_shell or "")
        if login_shell and not login_shell.endswith("/sw/bin/zsh") and shell != "zsh":
            core.report.todo("Login shell",
                             f"Your login shell is {login_shell}. dotkit makes the zsh of Nix the login shell, so "
                             f"the config of {shell} is not used in new terminals.", [])
        core.commit()
        failures += self._conflicts(docs)
        if failures:
            problems = docs.begin("core")
            problems.report.todo("Problems during the migration", "",
                                 [report_mod.problem_item(f) for f in failures])
            problems.commit()
        plan = writer.render(docs)
        plan.failures = failures
        checks.check(docs, plan)
        report = report_mod.render(docs.report, ident.hostname, repo_rel)
        plan.report = "".join(writer.mask_lines(report.splitlines(keepends=True)))
        plan.files["MIGRATION.md"] = plan.report
        plan.summary = writer.mask_lines(checks.summary(plan, docs.report, ident.git_name, ident.git_email,
                                                           config_files.mise_dest(view, choices)))
        return plan

    def _check_once(self) -> Optional[Callable[[str, str], bool]]:
        """check_syntax for one build: each (shell, text) is checked once, as more than one migrator asks."""
        check = self.check_syntax
        if check is None:
            return None
        done: Dict[Tuple[str, str], bool] = {}

        def cached(shell: str, text: str) -> bool:
            if (shell, text) not in done:
                done[(shell, text)] = check(shell, text)
            return done[(shell, text)]

        return cached

    def adopt(self, repo: Path, nix_declared: dict, opts: AdoptOptions) -> AdoptResult:
        """`dotkit migrate`: what the repository lacks, added by each migrator in its own transaction.

        A migrator whose adopt fails is rolled back and adds nothing; the
        others go on. The output comes from what the documents added and
        from the report, masked.
        """
        facts = self.scan()
        ident = facts.identity
        out = set(self._out)
        failures = list(self.failures)
        self._skip(out, failures)
        view = facts.without(out)
        declared = Declared.for_repo(facts.base, repo, nix_declared, view)
        docs = docs_mod.Docs("adopt", facts.base.template, repo, home=ident.home, host=ident.hostname,
                             strict=self.strict)
        for m in self.migrators:
            if m.key in out:
                continue
            txn = docs.begin(m.key)
            try:
                m.adopt(view, declared, txn, opts)
                txn.commit()
            except Exception as e:  # noqa: BLE001 - isolated: it adds nothing
                txn.rollback()
                self._fail(m, "adopt", e, failures)
        failures += self._conflicts(docs)
        files, secret_lines = docs.render()
        assert not secret_lines, "adopt writes nothing to ~/.secrets"
        added = []
        for rel, header in ADOPT_LISTS:
            d = docs.get(rel)
            added.append((f"{header} -> {rel}:", d.added() if d is not None and rel in files else []))
        mise = docs.get("config/mise.toml")
        where = dict(docs.report.carried_rows)
        added.append(("Tools -> config/mise.toml:",
                      [f"{k} (from {where[k]})" if k in where else k for k in mise.added()]
                      if mise is not None and "config/mise.toml" in files else []))
        output = writer.mask_lines(report_mod.adopt_lines(added, docs.report))
        return AdoptResult(repo, files, {rel: docs.read.get(rel) for rel in files}, output, failures)

    def _conflicts(self, docs: docs_mod.Docs) -> List[Failure]:
        """The conflicts of the documents, as failures of the migrator whose value was refused."""
        titles = {m.key: m.title for m in self.migrators}
        home = str(self.ctx.home)
        out = []
        for c in docs.conflicts:
            first, second = titles.get(c.first, c.first), titles.get(c.second, c.second)
            error = " ".join(masked_lines(f"{first} has {_short(c.kept)}, and {second} wanted {_short(c.refused)}",
                                          home))
            target = " ".join(masked_lines(_short(c.key), home))
            out.append(Failure(c.second, second, "conflict", error, c.path, "", reason=target))
        return out
