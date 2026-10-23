"""A read-only inventory of this Mac: packages, tools, shell files, and config.

Nothing here writes a file or installs anything. Commands run with a
timeout; a command that fails gives an empty result.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import base as base_mod
from . import secrets
from .context import Context, is_managed

# Dotfiles in the home directory that a person may want in the repository.
HOME_DOTFILES = (
    ".bashrc", ".vimrc", ".tmux.conf", ".inputrc", ".ideavimrc", ".curlrc", ".wgetrc", ".psqlrc",
    ".irbrc", ".pryrc", ".gitignore_global", ".gitmessage", ".hushlogin",
)

# Files and directories in ~/.config that hold state or caches, not config.
_STATE_NAMES = re.compile(
    r"(\.(db|sqlite\d?|sqlite-(wal|shm)|log|pid|sock|lock|ldb)$|^(cache|caches|Cache|logs?|history|"
    r"sessions?|state|tmp|node_modules|\.git|IndexedDB|Local Storage|Session Storage|GPUCache|"
    r"Crashpad|blob_storage|Service Worker|Cookies.*|backups?|automatic_backups)$)"
)
_MAX_FILES = 300
_MAX_BYTES = 2_000_000


@dataclass
class OtherConfig:
    rel: str  # relative to the home directory, for example .config/nvim
    is_dir: bool
    adoptable: bool
    reason: str = ""  # why it cannot be adopted
    files: int = 0


@dataclass
class Scan:
    home: str
    other_config: list[OtherConfig] = field(default_factory=list)
    secret_files: list[str] = field(default_factory=list)
    # Scan steps that failed, with the error. The report lists them.
    warnings: list[str] = field(default_factory=list)


class Scanner:
    def __init__(self, ctx: Context):
        self.home = ctx.home
        self.base = ctx.base

    def h(self, rel: str) -> Path:
        return self.home / rel

    # --- mise and version managers

    def _assess(self, rel: str) -> OtherConfig:
        p = self.h(rel)
        is_dir = p.is_dir()
        if secrets.known_secret_file(rel):
            return OtherConfig(rel, is_dir, False, "holds login tokens")
        if is_managed(p):
            return OtherConfig(rel, is_dir, False, "a Home Manager link")
        if not is_dir:
            try:
                size = p.stat().st_size
            except OSError:
                return OtherConfig(rel, False, False, "cannot be read")
            if size > 256_000:
                return OtherConfig(rel, False, False, "large")
            if _STATE_NAMES.search(p.name):
                return OtherConfig(rel, False, False, "state, not config")
            try:
                if secrets.scan_file(p):
                    return OtherConfig(rel, False, False, "has a secret")
            except secrets.Unscannable as e:
                return OtherConfig(rel, False, False, f"cannot be checked for secrets: {e}")
            return OtherConfig(rel, False, True, files=1)
        if any(f.startswith(rel + "/") for f in secrets.SECRET_FILES):
            return OtherConfig(rel, True, False, "holds login tokens")
        count = size = 0
        for root, dirs, files in os.walk(p, followlinks=False):
            dirs[:] = [d for d in dirs if d != ".DS_Store"]
            for d in dirs:
                if _STATE_NAMES.search(d):
                    return OtherConfig(rel, True, False, "holds state or caches")
            for f in files:
                if f == ".DS_Store":
                    continue
                count += 1
                fp = Path(root, f)
                if _STATE_NAMES.search(f):
                    return OtherConfig(rel, True, False, "holds state or caches")
                try:
                    st = fp.lstat()
                except OSError:
                    return OtherConfig(rel, True, False, "has a file that cannot be read")
                # A link is copied as a link, so its target does not go into the repository.
                if stat.S_ISLNK(st.st_mode):
                    continue
                if not stat.S_ISREG(st.st_mode):
                    return OtherConfig(rel, True, False, "has a socket, a FIFO, or a device")
                size += st.st_size
                if count > _MAX_FILES or size > _MAX_BYTES:
                    return OtherConfig(rel, True, False, "large")
                try:
                    if secrets.scan_file(fp):
                        return OtherConfig(rel, True, False, "has a secret")
                except secrets.Unscannable as e:
                    return OtherConfig(rel, True, False, f"has a file that cannot be checked for secrets ({e})")
        if count == 0:
            return OtherConfig(rel, True, False, "empty")
        return OtherConfig(rel, True, True, files=count)

    def scan_other_config(self) -> None:
        config = self.h(".config")
        if config.is_dir():
            for p in sorted(config.iterdir(), key=lambda q: q.name.lower()):
                if p.name in base_mod.BASE_CONFIG_DIRS or p.name.startswith("."):
                    continue
                self.s.other_config.append(self._assess(".config/" + p.name))
        for name in HOME_DOTFILES:
            if self.h(name).is_file():
                self.s.other_config.append(self._assess(name))
        for rel in secrets.SECRET_FILES:
            if self.h(rel).exists():
                self.s.secret_files.append(rel)

    def run(self) -> Scan:
        self.s = Scan(home=str(self.home))
        for step in (
            self.scan_other_config,
        ):
            try:
                step()
            except Exception as e:  # noqa: BLE001 - one failed step must not stop the others
                self.s.warnings.append(f"{step.__name__.removeprefix('scan_')}: {type(e).__name__}: {e}")
        return self.s
