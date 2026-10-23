"""What the scan reads: the home directory, the environment, the commands, and the base.

Also the read-only file helpers and the path helpers that the migrators,
the Session, and the screens share. Nothing here writes a file.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import base as base_mod
from .proc import Runner


@dataclass
class Context:
    home: Path
    env: Mapping[str, str]
    runner: Runner
    base: base_mod.Base
    applications: Path
    brew_prefix: Path


def this_mac(home: Path, base: base_mod.Base) -> Context:
    """The context of a real run: this Mac's commands, environment, and directories."""
    return Context(home, os.environ, Runner(), base, Path("/Applications"), Path("/opt/homebrew"))


# --- Files, read-only


def is_managed(p: Path) -> bool:
    """A link that Home Manager made: it points into the Nix store."""
    try:
        return p.is_symlink() and os.readlink(p).startswith("/nix/store/")
    except OSError:
        return False


def read_text(p: Path, limit: int = 4_000_000) -> str:
    """The text of a regular file, or "": never opens a FIFO or a device."""
    try:
        if not stat.S_ISREG(os.stat(p).st_mode) or os.stat(p).st_size > limit:
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def version_key(v: str) -> list:
    return [int(x) if x.isdigit() else x for x in re.split(r"[.\-_+]", v.lstrip("v"))]


# --- Paths


def tilde(path: str | Path, home: str) -> str:
    p = str(path)
    if p == home:
        return "~"
    return "~/" + p[len(home) + 1:] if p.startswith(home + "/") else p


def shell_path(path: str) -> str:
    """A path for a command in the report: quoted for the shell, with a leading ~/ outside the quotes."""
    if path == "~":
        return path
    if path.startswith("~/"):
        return "~/" + shlex.quote(path[2:])
    return shlex.quote(path)


def expand(path: str, home: str) -> str:
    """An absolute path, with ~ for the home directory of the scan."""
    path = path.strip()
    if path == "~" or path.startswith("~/"):
        path = home + path[1:]
    return os.path.abspath(path)


def repo_error(repo: str, home: str) -> str | None:
    """Why a path cannot be the new repository, or None."""
    p = expand(repo, home)
    if not p.startswith(home + "/"):
        return f"Use a directory in {home}."
    # The path goes into nix/user.nix and into zsh lines in double quotes.
    if re.search(r'["$`\\\n]', p):
        return "Use a path without \", $, `, \\, or a line break."
    if os.path.lexists(p):
        return f"{tilde(p, home)} exists. Choose a new directory."
    if not os.path.isdir(os.path.dirname(p)):
        return f"{tilde(os.path.dirname(p), home)} does not exist."
    return None
