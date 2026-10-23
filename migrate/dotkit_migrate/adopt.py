"""`dotkit migrate`: on a Mac with dotkit, find the packages that were installed
by hand and that the lists do not have, and add them to the lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import base as base_mod
from . import catalog
from .plan import insert_tools, mas_lines, mask_lines, ruby_escape, tap_lines, toml_key
from .scan import Scan


@dataclass
class Missing:
    formulae: list[str] = field(default_factory=list)
    casks: list[str] = field(default_factory=list)
    taps: list[str] = field(default_factory=list)  # lines of nix/taps.txt
    mas: list[tuple[int, str]] = field(default_factory=list)
    mise: list[tuple[str, str]] = field(default_factory=list)  # (key, where it was installed)
    # cargo and Go tools: mise builds them from source, so, as in the setup
    # wizard, they are added only when asked (--all).
    built: list[tuple[str, str]] = field(default_factory=list)
    # Taps and App Store apps that cannot go into the lists: what to do by hand.
    tap_todos: list[str] = field(default_factory=list)
    mas_todos: list[str] = field(default_factory=list)
    # App Store apps above that got a label because another app has their name.
    mas_notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.formulae or self.casks or self.taps or self.mas or self.mise or self.built
                    or self.tap_todos or self.mas_todos)


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
    # The evaluated configuration, and the lines of nix/taps.txt and nix/mas.txt
    # as they are now.
    taps = set(declared.get("taps") or []) | set(base_mod.read_list(repo / "nix/taps.txt"))
    m.taps, m.tap_todos = tap_lines(scan.taps, taps)
    # The evaluated masApps has the names of nix/mas.txt escaped (nix/read-mas.nix)
    # and the names written by hand in nix/darwin.nix as they are. The escape is
    # one to one, so each key that is not an escaped name of the file is one
    # written by hand.
    listed = base_mod.read_mas(repo / "nix/mas.txt")
    evaluated = declared.get("masApps") or {}
    escaped = {ruby_escape(n) for n in listed}
    taken = dict(listed)
    for k, v in evaluated.items():
        if k not in escaped:
            taken.setdefault(k, v)
    ids = set(evaluated.values()) | set(listed.values())
    m.mas, m.mas_notes, m.mas_todos = mas_lines(scan.mas_apps, taken, ids)
    keys = set(base.mise) | set(repo_mise)
    for backend, pkgs in (("npm", scan.npm), ("pipx", scan.pipx), ("cargo", scan.cargo), ("go", scan.go)):
        for p in pkgs:
            key = f"{backend}:{p.name}"
            if not p.ok or p.name in catalog.NPM_NOT_IN_MISE or key in keys or has_tool(p.name):
                continue
            if backend in catalog.BUILT_FROM_SOURCE and not all_tools:
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
    if m.taps:
        out += ["Homebrew taps -> nix/taps.txt:"] + [f"  {t}" for t in m.taps]
    if m.mas:
        out += ["App Store apps -> nix/mas.txt:"] + [f"  {i} {n}" for i, n in m.mas] + \
            [f"  {n}" for n in m.mas_notes]
    if m.mise:
        out += ["Tools -> config/mise.toml:"] + [f"  {k} (from {w})" for k, w in m.mise]
    if m.built:
        out += [" and ".join(catalog.BUILT_FROM_SOURCE.values())
                + " tools, not added: mise builds them from source. Use --all to add them:"]
        out += [f"  {k} (from {w})" for k, w in m.built]
    if m.tap_todos:
        out += ["Homebrew taps, not added:"] + [f"  {t}" for t in m.tap_todos]
    if m.mas_todos:
        out += ["App Store apps, not added:"] + [f"  {t}" for t in m.mas_todos]
    # Nothing that it prints can show a secret.
    return mask_lines(out)


def apply(m: Missing, repo: Path) -> None:
    """Appends the formulae, casks, taps, App Store apps, and mise tools to the lists of the repository."""
    for rel, names in (("nix/brews.txt", m.formulae), ("nix/casks.txt", m.casks), ("nix/taps.txt", m.taps),
                       ("nix/mas.txt", [f"{i} {n}" for i, n in m.mas])):
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

