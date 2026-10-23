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
from . import catalog
from . import secrets
from .context import Context, is_managed, read_text

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
class ConfigFile:
    target: str  # relative to the home directory
    path: Path
    secrets: list[secrets.Secret] = field(default_factory=list)
    same_as_base: bool = False


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
    mise_config: Path | None = None
    frameworks: dict[str, str] = field(default_factory=dict)
    config_files: list[ConfigFile] = field(default_factory=list)
    gitconfig: ConfigFile | None = None
    other_config: list[OtherConfig] = field(default_factory=list)
    secret_files: list[str] = field(default_factory=list)
    ghostty_app_support: bool = False
    # Scan steps that failed, with the error. The report lists them.
    warnings: list[str] = field(default_factory=list)


def _secrets_of(p: Path) -> list[secrets.Secret]:
    """The secrets of a file. A file that cannot be checked counts as one with a secret."""
    try:
        return secrets.scan_file(p)
    except secrets.Unscannable as e:
        return [secrets.Secret(0, f"a file that cannot be checked ({e})")]


class Scanner:
    def __init__(self, ctx: Context):
        self.home = ctx.home
        self.base = ctx.base

    def h(self, rel: str) -> Path:
        return self.home / rel

    # --- mise and version managers

    def scan_mise(self) -> None:
        p = self.h(".config/mise/config.toml")
        if p.is_file() and not is_managed(p):
            self.s.mise_config = p

    def scan_frameworks(self) -> None:
        for spec in catalog.FRAMEWORKS:
            for rel in spec.paths:
                p = self.h(rel)
                if p.exists() and not is_managed(p):
                    self.s.frameworks[spec.name] = "~/" + rel
                    break

    # --- Files in the home directory

    def scan_config_files(self) -> None:
        for link in base_mod.LINKS:
            p = self.h(link.target)
            if not p.is_file() or is_managed(p):
                continue
            base_file = self.base.file(link.source)
            same = read_text(p) == read_text(base_file) if base_file.is_file() else False
            self.s.config_files.append(ConfigFile(link.target, p, _secrets_of(p), same))
        p = self.h(".gitconfig")
        if p.is_file() and not is_managed(p):
            self.s.gitconfig = ConfigFile(".gitconfig", p, _secrets_of(p))
        self.s.ghostty_app_support = self.h("Library/Application Support/com.mitchellh.ghostty/config").is_file()

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
            self.scan_mise, self.scan_frameworks,
            self.scan_config_files, self.scan_other_config,
        ):
            try:
                step()
            except Exception as e:  # noqa: BLE001 - one failed step must not stop the others
                self.s.warnings.append(f"{step.__name__.removeprefix('scan_')}: {type(e).__name__}: {e}")
        return self.s
