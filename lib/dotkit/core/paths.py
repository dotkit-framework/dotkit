"""Paths for messages, and the links of Home Manager. The command and the migration wizard share them."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union


def tilde(path: Union[str, Path], home: Union[str, Path]) -> str:
    """The path with ~ for the home directory: ~/.npmrc."""
    p, h = str(path), str(home)
    if p == h:
        return "~"
    return "~/" + p[len(h) + 1:] if p.startswith(h + "/") else p


def display(path: str, repo: Path, dotkit: Path, home: Path) -> str:
    """A source path for a message: relative in the repository, "dotkit FILE" in the base, else with ~."""
    for root, label in ((repo, ""), (dotkit, "dotkit ")):
        if path.startswith(f"{root}/"):
            return label + path[len(str(root)) + 1:]
    return tilde(path, home)


def is_managed(p: Path) -> bool:
    """A link that Home Manager made: it points into the Nix store."""
    try:
        return p.is_symlink() and os.readlink(p).startswith("/nix/store/")
    except OSError:
        return False
