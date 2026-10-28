"""The packages inside the installed mise tools: the gems of Ruby (also its default gems, such as uri and
cgi, which a Ruby security release often fixes), the npm packages of Node (npm itself and the global
packages) and of the npm tools with their dependencies, and the Python packages of Python and of the pipx
tools. The installed files say what they are; nothing runs.

They exist only where the tools are installed: in CI the part is skipped.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, Set, Tuple

from ..model import Item, Part, package_query

NAME, LABEL, NOUNS = "packages", "in mise", ("package in the mise tools", "packages in the mise tools")

# (OSV ecosystem, what the report calls it)
KINDS = {"gem": ("RubyGems", "gem"), "npm": ("npm", "npm package"), "python": ("PyPI", "Python package")}
_GEMSPEC = re.compile(r"(.+?)-(\d[^-]*)(?:-.+)?\.gemspec")
_DIST_INFO = re.compile(r"(.+)-([^-]+)\.dist-info")

Found = Tuple[str, str, str]  # (kind, name, version)


def _pypi_name(name: str) -> str:
    """The normalized name of a Python package (PEP 503): lowercase, runs of - _ . as one -."""
    return re.sub(r"[-_.]+", "-", name).lower()


def scan(root: Path) -> List[Found]:
    """The packages under an install directory, each one once: gemspec files in specifications/ (and
    specifications/default/), package.json of a directory in node_modules/ (also @scope/), and *.dist-info
    directories in site-packages/. Links are not followed."""
    out: List[Found] = []
    seen: Set[Found] = set()

    def add(f: Found) -> None:
        if f not in seen:
            seen.add(f)
            out.append(f)

    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        base, parent = os.path.basename(here), os.path.basename(os.path.dirname(here))
        if base == "specifications" or (base == "default" and parent == "specifications"):
            for f in sorted(files):
                m = _GEMSPEC.fullmatch(f)
                if m:
                    add(("gem", m[1], m[2]))
        if "package.json" in files and (parent == "node_modules" or (
                parent.startswith("@") and os.path.basename(os.path.dirname(os.path.dirname(here))) == "node_modules")):
            try:
                data = json.loads(Path(here, "package.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict) and isinstance(data.get("name"), str) and isinstance(data.get("version"), str):
                add(("npm", data["name"], data["version"]))
        if base == "site-packages":
            for d in dirs:
                m = _DIST_INFO.fullmatch(d)
                if m:
                    add(("python", _pypi_name(m[1]), m[2]))
    return out


def items(key: str, version: str, found: List[Found]) -> List[Item]:
    """The items of the packages of a tool, without the package of the tool itself (npm:cowsay has
    cowsay): the mise source checks it."""
    own = key.partition(":")[2].split("[", 1)[0] if ":" in key else ""
    out = []
    for kind, name, v in found:
        if name == own or (kind == "python" and name == _pypi_name(own)):
            continue
        ecosystem, label = KINDS[kind]
        out.append(Item(NAME, f"{key} > {name}", v, [package_query(ecosystem, name, v)],
                        where=f"{label} in {key} {version}", parent=key))
    return out


def collect(ctx) -> Part:
    if ctx.config_only:
        from . import CONFIG_ONLY
        return Part(skipped=CONFIG_ONLY)
    if ctx.mise_ls() is None:
        return Part(skipped="mise does not run here")
    part, installed = Part(), False
    for key, entries in ctx.mise_installed().items():
        for e in entries:
            path = e.get("install_path")
            if isinstance(path, str) and os.path.isdir(path):
                installed = True
                part.items += items(key, e["version"], scan(Path(path)))
    if not installed:
        part.skipped = "no mise tool is installed here"
    return part
