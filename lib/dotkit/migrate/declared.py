"""Declared: what the base, the repository, and your mise config provide already.

One rule for setup and for `dotkit migrate` (ARCHITECTURE.md, section 3.5):
what is declared is not added again. A Homebrew formula is skipped when
brew() names what provides it; an npm, pipx, cargo, or Go tool when tool()
is true, or when your mise config has its key (yours_mise_key; in adopt
also mise_key).

Always: the base lists and the base mise tools, and the tools of your
~/.config/mise/config.toml, carried as "layer" or kept in place (mise reads
it in both cases). For a repository also: its nix/packages.txt,
nix/brews.txt, nix/casks.txt, nix/taps.txt, nix/mas.txt, and
config/mise.toml, and the taps and App Store apps that `dotkit` evaluates
from the configuration (they include the two list files and any entries
written by hand into homebrew.taps and homebrew.masApps).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Set

from . import base as base_mod
from .docs import ruby_escape
from .migrators import config_files

# What brew() names when your mise config has a formula: "jq (your mise config)".
YOUR_MISE = "your mise config"


class Declared:
    def __init__(self, base: base_mod.Base):
        self.base = base
        # What the base and the repository have, by name or key -> "Nix", "Homebrew", or "mise".
        self._packages: Set[str] = set(base.packages)
        self._brews: Set[str] = set()  # formulae, with the short name of each tap formula too
        self._casks: Set[str] = set()
        for n in base.brews:
            self._add_brew(self._brews, n)
        for n in base.casks:
            self._add_brew(self._casks, n)
        self._mise: Set[str] = set(base.mise)  # the base's and the repository's mise keys
        self._yours: Set[str] = set()  # the mise keys of your mise config
        self._taps: Set[str] = set()
        self._mas_ids: Set[int] = set()
        self._mas_names: Set[str] = set()  # the raw names of nix/mas.txt
        self._mas_hand: Set[str] = set()  # the keys written by hand into homebrew.masApps

    @staticmethod
    def _add_brew(names: Set[str], name: str) -> None:
        names.add(name)
        names.add(name.rsplit("/", 1)[-1])

    def _add_yours(self, facts) -> None:
        cf = config_files.facts_of(facts)
        if cf is not None and cf.mise_config is not None:
            self._yours = set(cf.mise_tools)

    @classmethod
    def for_setup(cls, base: base_mod.Base, facts) -> "Declared":
        d = cls(base)
        d._add_yours(facts)
        return d

    @classmethod
    def for_repo(cls, base: base_mod.Base, repo: Path, nix_declared: dict, facts) -> "Declared":
        d = cls(base)
        d._add_yours(facts)
        d._packages |= set(base_mod.read_list(repo / "nix/packages.txt"))
        for n in base_mod.read_list(repo / "nix/brews.txt"):
            d._add_brew(d._brews, n)
        for n in base_mod.read_list(repo / "nix/casks.txt"):
            d._add_brew(d._casks, n)
        d._mise |= set(base_mod.mise_tools(repo / "config/mise.toml"))
        d._taps = set(nix_declared.get("taps") or []) | set(base_mod.read_taps(repo / "nix/taps.txt"))
        # The evaluated masApps has the names of nix/mas.txt escaped (nix/read-mas.nix), and the names
        # written by hand into homebrew.masApps as they are. The escape is one to one, so each key that is
        # not an escaped name of the file was written by hand.
        listed = base_mod.read_mas(repo / "nix/mas.txt")
        evaluated = nix_declared.get("masApps") or {}
        escaped = {ruby_escape(n) for n in listed}
        d._mas_names = set(listed)
        d._mas_hand = {k for k in evaluated if k not in escaped}
        d._mas_ids = set(listed.values()) | {v for v in evaluated.values() if isinstance(v, int)}
        return d

    def tool_source(self, name: str) -> Optional[str]:
        """What the base or the repository has for the command `name`: "bat (from Nix)"; None: nothing.

        A formula or package name counts by its last part, and through
        BREW_ALIASES; a mise key by its tool name (mise_short).
        """
        short = name.rsplit("/", 1)[-1]
        names = (short, base_mod.BREW_ALIASES.get(short, short))
        for n in names:
            if n in self._packages:
                return f"{n} (from Nix)"
        for n in names:
            if n in self._brews:
                return f"{n} (from Homebrew)"
        for key in sorted(self._mise):
            if base_mod.mise_short(key) in names:
                return f"{key} (from mise)"
        return None

    def brew(self, name: str, kind: str) -> Optional[str]:
        """What provides a Homebrew formula or cask, e.g. "ripgrep (dotkit, from Nix)" or
        "jq (your mise config)"; None: nothing does."""
        provided = self.base.provides_brew(name, kind)
        if provided:
            return provided
        short = name.rsplit("/", 1)[-1]
        if kind == "cask":
            return f"{short} (from Homebrew)" if name in self._casks or short in self._casks else None
        source = self.tool_source(name)
        if source:
            return source
        names = (short, base_mod.BREW_ALIASES.get(short, short))
        for key in sorted(self._yours):
            if base_mod.mise_short(key) in names:
                return f"{key} ({YOUR_MISE})"
        return None

    def mise_key(self, key: str) -> bool:
        """A mise key of the base, the repository, or your mise config."""
        return key in self._mise or key in self._yours

    def yours_mise_key(self, key: str) -> bool:
        """A mise key of your mise config."""
        return key in self._yours

    def tool(self, name: str) -> bool:
        """A command that the base or the repository has, from Nix, Homebrew, or mise."""
        return self.tool_source(name) is not None

    def tap(self, name: str) -> bool:
        return name in self._taps

    def mas_id(self, app_id: int) -> bool:
        return app_id in self._mas_ids

    def mas_name(self, name: str) -> bool:
        """A name that is taken: a raw name of nix/mas.txt, or one whose escaped form (nix/read-mas.nix) is a
        key written by hand into homebrew.masApps."""
        return name in self._mas_names or ruby_escape(name) in self._mas_hand
