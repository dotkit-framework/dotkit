"""Tools of cargo install and the Go bin directory: to mise, off by default.

mise builds these from source, with cargo and with go, so their items are
off unless you select them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .._compat import tomllib
from ..context import Context, read_json
from ..docs import Txn
from ..model import Choices, Facts, Page
from . import Migrator
from .tools import Package, env_path, page, plan_tools, tilde_path


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
        b.cargo.append(Package(name, version, "cargo install", ok, "" if ok else f"installed from {source}"))


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
            b.go.append(Package(module, version, tilde_path(ctx, p), True, f"the command {p.name}"))
        else:
            b.go.append(Package(p.name, "", tilde_path(ctx, p), False, "no Go module information"))


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
    sections = ("Tools that moved to mise", "Tools that are not in mise", "Not carried, by your choice")

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
                 "them.", b.cargo),
            page("go", "Go tools",
                 "The tools in your Go bin directory. The selected ones move to mise; the old copies stay until "
                 "you remove them.", b.go),
        ]

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        b = facts_of(f)
        if b is None:
            return
        pages = self.pages(f)
        if plan_tools(f, c, txn, pages, "cargo", b.cargo, "# From cargo install, see MIGRATION.md.",
                      "cargo uninstall {name}") and not b.has_cargo:
            txn.report.todo("Tools that moved to mise", "", ["mise needs cargo for these: `mise use -g rust`."])
        if plan_tools(f, c, txn, pages, "go", b.go, "# From ~/go/bin, see MIGRATION.md.",
                      "rm {where}") and not b.has_go:
            txn.report.todo("Tools that moved to mise", "", ["mise needs go for these: `mise use -g go`."])
