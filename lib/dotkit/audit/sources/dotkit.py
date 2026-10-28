"""dotkit itself: the Python packages in lib/dotkit/_vendor (PyPI), and the inputs of its flake.lock from
GitHub, by their locked commit: the zsh plugins, nix-darwin, Home Manager, and Nixpkgs."""

from __future__ import annotations

import json
import re
from typing import List

from ..match import HEX40
from ..model import Item, Part, package_query

NAME, LABEL, NOUNS = "dotkit", "dotkit", ("package of dotkit", "packages of dotkit")


def vendored(text: str) -> List[Item]:
    """The packages of vendor.txt: NAME==VERSION --hash=..."""
    out = []
    for line in text.split("\n"):
        m = re.match(r"\s*([A-Za-z0-9_.-]+)==([^\s;]+)", line)
        if m:
            out.append(Item(NAME, m[1], m[2], [package_query("PyPI", m[1], m[2])]))
    return out


def inputs(lock: dict) -> List[Item]:
    """The inputs of a flake.lock of type github, named "input NAME"."""
    out = []
    nodes = lock.get("nodes") if isinstance(lock, dict) else None
    for name, node in sorted((nodes or {}).items()):
        locked = node.get("locked") if isinstance(node, dict) else None
        if not isinstance(locked, dict) or locked.get("type") != "github":
            continue
        rev = locked.get("rev")
        if isinstance(rev, str) and HEX40.fullmatch(rev):
            out.append(Item(NAME, f"input {name}", rev[:12], [{"commit": rev}]))
    return out


def collect(ctx) -> Part:
    root = ctx.env.dotkit
    part = Part()
    try:
        part.items += vendored((root / "lib/dotkit/_vendor/vendor.txt").read_text(encoding="utf-8"))
    except OSError:
        pass
    try:
        part.items += inputs(json.loads((root / "flake.lock").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return part
