"""`dotkit migrate`: on a Mac with dotkit, find the packages that were installed
by hand and that the lists do not have, and add them to the lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import base as base_mod
from .plan import insert_tools, nix_name, nix_string, toml_key
from .scan import Scan


@dataclass
class Missing:
    formulae: list[str] = field(default_factory=list)
    casks: list[str] = field(default_factory=list)
    taps: list[str] = field(default_factory=list)
    mas: list[tuple[int, str]] = field(default_factory=list)
    mise: list[tuple[str, str]] = field(default_factory=list)  # (key, where it was installed)
    # cargo and Go tools: mise builds them from source, so, as in the setup
    # wizard, they are added only when asked (--all).
    built: list[tuple[str, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.formulae or self.casks or self.taps or self.mas or self.mise or self.built)


def missing(scan: Scan, repo: Path, base: base_mod.Base, declared: dict, all_tools: bool = False) -> Missing:
    """What is on the Mac and not in the lists of the base or of the repository.

    A Homebrew copy of a tool that the base or the repository has from Nix or
    mise is not missing: the lists have the tool already.
    """
    m = Missing()
    repo_packages = set(base_mod.read_list(repo / "nix/packages.txt"))
    repo_mise = base_mod.mise_tools(repo / "config/mise.toml")
    declared_tools = repo_packages | set(base.packages) | {base_mod.mise_short(k) for k in (*repo_mise, *base.mise)}

    def has_tool(name: str) -> bool:
        short_name = name.rsplit("/", 1)[-1]
        return short_name in declared_tools or base_mod.BREW_ALIASES.get(short_name, short_name) in declared_tools

    brews = set(base.brews) | set(base_mod.read_list(repo / "nix/brews.txt"))
    casks = set(base.casks) | set(base_mod.read_list(repo / "nix/casks.txt"))

    def short(n: str) -> str:
        return n.rsplit("/", 1)[-1]

    brews |= {short(n) for n in brews}
    casks |= {short(n) for n in casks}
    for f in scan.formulae:
        if f not in brews and short(f) not in brews and not base.provides_brew(f, "formula") and not has_tool(f):
            m.formulae.append(f)
    for c in scan.casks:
        if c not in casks and short(c) not in casks and not base.provides_brew(c, "cask"):
            m.casks.append(c)
    taps = set(declared.get("taps") or [])
    m.taps = [t.name for t in scan.taps if t.name not in taps]
    ids = set((declared.get("masApps") or {}).values())
    m.mas = [(i, n) for i, n in scan.mas_apps if i not in ids]
    keys = set(base.mise) | set(repo_mise)
    for backend, pkgs in (("npm", scan.npm), ("pipx", scan.pipx), ("cargo", scan.cargo), ("go", scan.go)):
        for p in pkgs:
            key = f"{backend}:{p.name}"
            if not p.ok or p.name == "pnpm" or key in keys or has_tool(p.name):
                continue
            if backend in ("cargo", "go") and not all_tools:
                m.built.append((key, p.where))
            else:
                m.mise.append((key, p.where))
    return m


def describe(m: Missing) -> list[str]:
    out = []
    if m.formulae:
        out += ["Homebrew formulae -> nix/brews.txt:"] + [f"  {n}" for n in m.formulae]
    if m.casks:
        out += ["Homebrew casks -> nix/casks.txt:"] + [f"  {n}" for n in m.casks]
    if m.mise:
        out += ["Tools -> config/mise.toml:"] + [f"  {k} (from {w})" for k, w in m.mise]
    if m.built:
        out += ["cargo and Go tools, not added: mise builds them from source. Use --all to add them:"]
        out += [f"  {k} (from {w})" for k, w in m.built]
    if m.taps:
        out += ["Homebrew taps: add them to homebrew.taps in nix/darwin.nix by hand:"]
        out += [f"  {nix_string(t)}" for t in m.taps]
    if m.mas:
        out += ["App Store apps: add them to homebrew.masApps in nix/darwin.nix by hand:"]
        out += [f"  {nix_name(n)} = {i};" for i, n in m.mas]
    return out


def apply(m: Missing, repo: Path) -> None:
    """Appends the formulae, casks, and mise tools to the lists of the repository."""
    for rel, names in (("nix/brews.txt", m.formulae), ("nix/casks.txt", m.casks)):
        if not names:
            continue
        p = repo / rel
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        if text and not text.endswith("\n"):
            text += "\n"
        p.write_text(text + "".join(n + "\n" for n in names), encoding="utf-8")
    if m.mise:
        p = repo / "config/mise.toml"
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        p.write_text(insert_tools(text, [f'{toml_key(k)} = "latest"' for k, _ in m.mise]), encoding="utf-8")

