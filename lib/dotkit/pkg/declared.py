"""Where a tool is declared (PARITY.md, D), and which global tool supplies a command (X).

A file name of a declaration starts with dotkit: for the base lists.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path
from typing import List, Optional, Tuple

from ..core import lists, mise_toml, proc
from ..core.env import Env

LISTS = ("nix/packages.txt", "nix/brews.txt", "nix/casks.txt")
MISE = "config/mise.toml"
MAS = "nix/mas.txt"


def find(env: Env, name: str) -> List[Tuple[str, str]]:
    """(file, entry) for each declaration of name. For mise keys, a bare name also matches the last part of
    the key: hunk matches github:modem-dev/hunk, and @scope/pkg matches npm:@scope/pkg."""
    bare = name.split(":", 1)[1] if ":" in name else name
    out = []
    # App Store apps: by ID, in the list of the dotfiles repository only.
    if name.startswith("mas:"):
        mas = env.repo / MAS
        out += [(MAS, bare) for app_id, _ in lists.mas_apps(mas) if app_id == bare]
        return out
    for root, label in ((env.dotkit, "dotkit:"), (env.repo, "")):
        for rel in LISTS:
            out += [(label + rel, bare) for n in lists.names(root / rel) if n == bare]
        for key in mise_toml.tools(root / MISE):
            if key in (name, bare, "npm:" + name) or mise_toml.short(key) == bare:
                out.append((label + MISE, key))
    return out


def global_bin_dirs(home: Path) -> List[str]:
    """The directories of global tools: the Nix profile, the system, the user profile, and Homebrew."""
    user = pwd.getpwuid(os.geteuid()).pw_name
    return [f"/etc/profiles/per-user/{user}/bin", "/run/current-system/sw/bin", f"{home}/.nix-profile/bin",
            "/opt/homebrew/bin"]


def existing_command(env: Env, command: str) -> Optional[str]:
    """The path of command when a global tool supplies it: the directories, else the global mise config.
    The PATH of the caller does not count: it can have project tools."""
    for d in global_bin_dirs(env.home):
        path = f"{d}/{command}"
        if os.access(path, os.X_OK):
            return path
    r = proc.mise(env.home, ["which", command], stdout=proc.CAPTURE, stderr=proc.DEVNULL)
    return r.value if r.ok else None


def origin(path: str, home: Path) -> str:
    """The source of a command path, for example "nix ripgrep-15.2.0" (PARITY.md, X2)."""
    real = os.path.realpath(path) if os.path.exists(path) else path
    mise_dir = os.environ.get("MISE_DATA_DIR") or f"{home}/.local/share/mise"

    def two(rest: str) -> str:
        parts = rest.split("/")
        return f"{parts[0]} {parts[1] if len(parts) > 1 else rest}"

    if real.startswith("/nix/store/"):
        rest = real[len("/nix/store/"):]
        # ${real#/nix/store/*-}: after the first -, or the whole path when there is none.
        rest = rest[rest.index("-") + 1:] if "-" in rest else real
        return "nix " + rest.split("/", 1)[0]
    if real.startswith(f"{mise_dir}/installs/"):
        return "mise " + two(real[len(f"{mise_dir}/installs/"):])
    if real.startswith("/opt/homebrew/Cellar/"):
        return "brew " + two(real[len("/opt/homebrew/Cellar/"):])
    if real.startswith(("/opt/homebrew/Caskroom/", "/Applications/")):
        return "brew (cask)"
    return real
