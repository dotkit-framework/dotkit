"""Tools of cargo install and the Go bin directory: to mise, off by default.

mise builds these from source, with cargo and with go, so their items are
off unless you select them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import catalog
from .._compat import tomllib
from ..context import Context, read_json
from ..declared import Declared
from ..docs import Txn
from ..model import AdoptOptions, Choices, Facts, Page
from . import Migrator, config_files
from .tools import Package, adopt_tools, env_path, mise_commands, page, plan_tools, tilde_path


@dataclass
class BuiltFacts:
    cargo: List[Package] = field(default_factory=list)
    go: List[Package] = field(default_factory=list)
    has_cargo: bool = False
    has_go: bool = False


def facts_of(f: Facts) -> Optional[BuiltFacts]:
    return f.get(BuiltTools.key)


def _cargo(ctx: Context, b: BuiltFacts) -> None:
    cargo_home = env_path(ctx, "CARGO_HOME", ".cargo")
    b.has_cargo = (cargo_home / "bin/cargo").exists() or bool(ctx.runner.which("cargo"))
    installs: dict = {}
    data = read_json(cargo_home / ".crates2.json")
    if isinstance(data, dict):
        installs = data.get("installs") or {}
    else:
        try:
            installs = tomllib.loads((cargo_home / ".crates.toml").read_text(encoding="utf-8")).get("v1", {})
        except (OSError, ValueError):
            installs = {}
    for key in sorted(installs):
        m = re.match(r"^(\S+) (\S+) \((.+)\)$", key)
        if not m:
            continue
        name, version, source = m.groups()
        ok = source.startswith(("registry+", "sparse+"))
        # .crates2.json has {"bins": [...]} per crate, .crates.toml a list of the bins.
        info = installs[key]
        bins = info.get("bins") if isinstance(info, dict) else info
        commands = [str(x) for x in bins or [] if isinstance(x, str)] if isinstance(bins, list) else []
        b.cargo.append(Package(name, version, "cargo install", ok, "" if ok else f"installed from {source}",
                               commands))


def _go(ctx: Context, b: BuiltFacts) -> None:
    go = ctx.runner.which("go", ("/opt/homebrew/bin", "/usr/local/go/bin"))
    b.has_go = bool(go)
    gobin = ctx.env.get("GOBIN")
    d = Path(gobin) if gobin else env_path(ctx, "GOPATH", "go") / "bin"
    if not d.is_dir():
        return
    for p in sorted(d.iterdir()):
        if not p.is_file() or not os.access(p, os.X_OK):
            continue
        module, version = _go_module(ctx, p, go)
        if module:
            b.go.append(Package(module, version, tilde_path(ctx, p), True, f"the command {p.name}", [p.name]))
        else:
            b.go.append(Package(p.name, "", tilde_path(ctx, p), False, "no Go module information", [p.name]))


def _go_module(ctx: Context, p: Path, go: Optional[str]) -> Tuple[str, str]:
    text = ctx.runner.run([go, "version", "-m", str(p)], timeout=30) if go else None
    if text:
        m = re.search(r"(?m)^\s*path\s+(\S+)", text)
        v = re.search(r"(?m)^\s*mod\s+\S+\s+(\S+)", text)
        if m:
            return m.group(1), (v.group(1) if v else "")
    # Without go: Go writes the same lines into the binary.
    try:
        with open(p, "rb") as f:
            data = f.read(64_000_000)
    except OSError:
        return "", ""
    m = re.search(rb"\npath\t([^\t\n]+)\nmod\t[^\t\n]+\t([^\t\n]+)", b"\n" + data)
    if m:
        return m.group(1).decode(errors="replace"), m.group(2).decode(errors="replace")
    return "", ""


class BuiltTools(Migrator):
    key = "built_tools"
    title = "cargo and Go tools"
    sections = ("Tools that moved to mise", "Tools that are not in mise", "mise", "Not carried, by your choice")

    def manual_steps(self, ctx: Context) -> str:
        return ("None of your cargo install and Go tools is in config/mise.toml. They stay where they are, and "
                "mise does not install them. Add the ones you want with `mise use -g cargo:<crate>` or "
                "`mise use -g go:<module>`; mise builds them from source.")

    def scan(self, ctx: Context) -> BuiltFacts:
        b = BuiltFacts()
        _cargo(ctx, b)
        _go(ctx, b)
        return b

    def pages(self, f: Facts) -> List[Page]:
        b = facts_of(f)
        if b is None:
            return []
        return [
            page("cargo", "cargo install tools",
                 "Tools of cargo install. The selected ones move to mise; the old copies stay until you remove "
                 "them.", b.cargo, Declared.for_setup(f.base, f)),
            page("go", "Go tools",
                 "The tools in your Go bin directory. The selected ones move to mise; the old copies stay until "
                 "you remove them.", b.go, Declared.for_setup(f.base, f)),
        ]

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        b = facts_of(f)
        if b is None:
            return
        pages = self.pages(f)
        kept = config_files.mise_target(f, c) == "kept"
        if (plan_tools(f, c, txn, pages, "cargo", b.cargo, "# From cargo install, see MIGRATION.md.")
                and not b.has_cargo):
            _needs(txn, kept, "mise needs cargo for these: `mise use -g rust`.")
        if plan_tools(f, c, txn, pages, "go", b.go, "# From ~/go/bin, see MIGRATION.md.") and not b.has_go:
            _needs(txn, kept, "mise needs go for these: `mise use -g go`.")

    def adopt(self, f: Facts, declared: Declared, txn: Txn, opts: AdoptOptions) -> None:
        b = facts_of(f)
        if b is None:
            return
        left = adopt_tools(declared, txn, "cargo", b.cargo, opts.all_tools)
        left += adopt_tools(declared, txn, "go", b.go, opts.all_tools)
        if left:
            txn.report.todo("Tools that are not in mise",
                            f"{' and '.join(catalog.BUILT_FROM_SOURCE.values())} tools, not added: mise builds them "
                            "from source. Use --all to add them:", left)

    def mise_commands(self, f: Facts) -> Dict[str, Tuple[str, ...]]:
        b = facts_of(f)
        return {**mise_commands("cargo", b.cargo if b else []), **mise_commands("go", b.go if b else [])}


def _needs(txn: Txn, kept: bool, line: str) -> None:
    """A line next to the tools: in the "mise" block when your mise config is kept in place."""
    if kept:
        txn.report.todo("mise", config_files.KEPT_INTRO, [line])
    else:
        txn.report.todo("Tools that moved to mise", "", [line])
