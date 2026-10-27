"""What add and remove change: the kinds of entries, their files, and the prefixes of the arguments.

KINDS is the one table of kind -> file. An Entry is one line of a list (or
one mise key); a Declaration is an entry that a file of the base or of the
dotfiles repository has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple


@dataclass(frozen=True)
class Kind:
    name: str
    file: str

    @property
    def nix(self) -> bool:
        """The kinds that Nix installs from a list file; the other one is mise."""
        return self.name != "mise"


KINDS: Dict[str, Kind] = {k.name: k for k in (
    Kind("nix", "nix/packages.txt"),
    Kind("formula", "nix/brews.txt"),
    Kind("cask", "nix/casks.txt"),
    Kind("mas", "nix/mas.txt"),
    Kind("mise", "config/mise.toml"),
)}
NIX_FILES = tuple(k.file for k in KINDS.values() if k.nix)

# The kinds that a prefix of `dotkit remove` selects; a prefix that is not here is a mise backend.
PREFIX_KINDS: Dict[str, FrozenSet[str]] = {
    "nix": frozenset({"nix"}), "brew": frozenset({"formula", "cask"}), "cask": frozenset({"cask"}),
    "mas": frozenset({"mas"}),
}
ALL_BUT_MAS = frozenset({"nix", "formula", "cask", "mise"})


def split_prefix(arg: str) -> Tuple[str, str]:
    """("nix", "jq") for nix:jq; ("", arg) without a prefix. A scoped npm name (@scope/pkg) has none."""
    prefix, sep, rest = arg.partition(":")
    return (prefix, rest) if sep else ("", arg)


def kinds_of(arg: str) -> FrozenSet[str]:
    """The kinds that an argument of remove can be."""
    prefix, _ = split_prefix(arg)
    if not prefix:
        return ALL_BUT_MAS
    return PREFIX_KINDS.get(prefix, frozenset({"mise"}))


@dataclass(frozen=True)
class Entry:
    kind: str
    # The text in the file: a name, "ID label" for an App Store app, or a mise key.
    text: str

    @property
    def file(self) -> str:
        return KINDS[self.kind].file

    @property
    def nix(self) -> bool:
        return KINDS[self.kind].nix


@dataclass(frozen=True)
class Declaration:
    entry: Entry
    base: bool

    @property
    def label(self) -> str:
        """The file, with dotkit: for the base: dotkit:nix/packages.txt."""
        return ("dotkit:" if self.base else "") + self.entry.file
