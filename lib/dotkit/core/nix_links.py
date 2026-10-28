"""The links of dotkit.links in the nix/ files of the dotfiles repository, as text.

nix_string() writes a Nix string. The migration writes nix/home.nix with it,
and dotkit own the lines that it adds.
"""

from __future__ import annotations


def nix_string(s: str) -> str:
    """A Nix string literal, with \\, ", and ${ escaped."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${") + '"'
