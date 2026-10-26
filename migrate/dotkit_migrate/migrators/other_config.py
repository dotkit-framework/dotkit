"""Other config: files and directories in ~/.config and dotfiles in the home directory, off by default.

They are not part of dotkit and keep working in place. A selected one is
copied to config/ and linked back. Files with state, caches, logins, or a
secret are not offered.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .. import base as base_mod
from .. import secrets
from ..context import Context, is_managed, tilde
from ..docs import Txn
from ..model import Choices, Facts, Item, Page
from ..report import plural
from . import Migrator, config_files

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
class Entry:
    """A file or directory of other config, and whether it can go into the repository."""

    rel: str  # relative to the home directory, for example .config/nvim
    is_dir: bool
    adoptable: bool
    reason: str = ""  # why it cannot be adopted
    files: int = 0


@dataclass
class OtherFacts:
    found: List[Entry] = field(default_factory=list)  # in ~/.config, then the dotfiles


def facts_of(f: Facts) -> Optional[OtherFacts]:
    return f.get(OtherConfig.key)


def adopted(f: Facts, c: Choices, rel: str) -> bool:
    """Whether other config carries ~/<rel> into the repository: no when its facts are missing (section 7.3)."""
    return facts_of(f) is not None and c.selected("other", f"other:{rel}")


def _assess(ctx: Context, rel: str) -> Entry:
    p = ctx.home / rel
    is_dir = p.is_dir()
    if secrets.known_secret_file(rel):
        return Entry(rel, is_dir, False, "holds login tokens")
    if is_managed(p):
        return Entry(rel, is_dir, False, "a Home Manager link")
    if not is_dir:
        try:
            size = p.stat().st_size
        except OSError:
            return Entry(rel, False, False, "cannot be read")
        if size > 256_000:
            return Entry(rel, False, False, "large")
        if _STATE_NAMES.search(p.name):
            return Entry(rel, False, False, "state, not config")
        try:
            if secrets.scan_file(p):
                return Entry(rel, False, False, "has a secret")
        except secrets.Unscannable as e:
            return Entry(rel, False, False, f"cannot be checked for secrets: {e}")
        return Entry(rel, False, True, files=1)
    if any(f.startswith(rel + "/") for f in secrets.SECRET_FILES):
        return Entry(rel, True, False, "holds login tokens")
    count = size = 0
    for root, dirs, files in os.walk(p, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".DS_Store"]
        for d in dirs:
            if _STATE_NAMES.search(d):
                return Entry(rel, True, False, "holds state or caches")
        for f in files:
            if f == ".DS_Store":
                continue
            count += 1
            fp = Path(root, f)
            if _STATE_NAMES.search(f):
                return Entry(rel, True, False, "holds state or caches")
            try:
                st = fp.lstat()
            except OSError:
                return Entry(rel, True, False, "has a file that cannot be read")
            # A link is copied as a link, so its target does not go into the repository.
            if stat.S_ISLNK(st.st_mode):
                continue
            if not stat.S_ISREG(st.st_mode):
                return Entry(rel, True, False, "has a socket, a FIFO, or a device")
            size += st.st_size
            if count > _MAX_FILES or size > _MAX_BYTES:
                return Entry(rel, True, False, "large")
            try:
                if secrets.scan_file(fp):
                    return Entry(rel, True, False, "has a secret")
            except secrets.Unscannable as e:
                return Entry(rel, True, False, f"has a file that cannot be checked for secrets ({e})")
    if count == 0:
        return Entry(rel, True, False, "empty")
    return Entry(rel, True, True, files=count)


class OtherConfig(Migrator):
    key = "other_config"
    title = "Other config"
    sections = ("Other config", "Other config that stays out of the repository")

    def manual_steps(self, ctx: Context) -> str:
        return ("None of your other config in ~/.config and of your dotfiles in the home directory (such as "
                "~/.vimrc) is in the repository, and MIGRATION.md does not list them. They stay in place and keep "
                "working. To keep one in the repository, copy it to config/ and link it in nix/home.nix, for "
                "example `dotkit.links.\".config/nvim\" = \"config/nvim\";`. Keep files with secrets, state, "
                "or caches out of it.")

    def scan(self, ctx: Context) -> OtherFacts:
        out = OtherFacts()
        config = ctx.home / ".config"
        if config.is_dir():
            for p in sorted(config.iterdir(), key=lambda q: q.name.lower()):
                if p.name in base_mod.BASE_CONFIG_DIRS or p.name.startswith("."):
                    continue
                out.found.append(_assess(ctx, ".config/" + p.name))
        for name in HOME_DOTFILES:
            if (ctx.home / name).is_file():
                out.found.append(_assess(ctx, name))
        return out

    def pages(self, f: Facts) -> List[Page]:
        o = facts_of(f)
        if o is None:
            return []
        items = []
        for x in o.found:
            if x.adoptable:
                what = (f"a directory with {plural(x.files, 'file')}") if x.is_dir else "a file"
                items.append(Item(f"other:{x.rel}", f"~/{x.rel}",
                                  f"~/{x.rel}: {what}. It goes to config/ and is linked back.", default=False))
        return [Page("other", "Other config to keep in the repository", "config/",
                     "These are not part of dotkit. By default they stay in place and keep working. Select the "
                     "ones to copy into the repository.", "checklist", items)]

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        o, r = facts_of(f), txn.report
        if o is None:
            return
        home = f.identity.home
        adopted, left, skipped, taken = [], [], [], []
        for x in o.found:
            if c.selected("other", f"other:{x.rel}") and x.adoptable:
                name = x.rel.removeprefix(".config/").lstrip(".")
                dest = f"config/{name}" if x.rel.startswith(".config/") else f"config/home/{name}"
                if txn.copies.add(dest, Path(home, x.rel)):
                    txn.links().link(x.rel, dest)
                    adopted.append(f"~/{x.rel} -> {dest}")
                else:
                    how, where = txn.copies.taken(dest)
                    src = txn.copies.source(where)
                    has = tilde(src, home) if src else config_files.merged_sources(f, c).get(where, "another file")
                    keep = ("To keep it in the repository, copy it under another name and link it in "
                            "nix/home.nix.")
                    if how == "contains":
                        taken.append(f"~/{x.rel}: its place in the repository, {dest}, would contain {where}; "
                                     f"it stays in place. {keep}")
                    elif how == "inside":
                        taken.append(f"~/{x.rel}: its place in the repository, {dest}, is inside {where}, "
                                     f"which has {has} already. It stays in place. {keep}")
                    else:
                        taken.append(f"~/{x.rel}: its place in the repository, {dest}, has {has} already. It "
                                     f"stays in place. {keep}")
            elif x.adoptable:
                left.append(f"~/{x.rel}")
            elif x.reason not in ("empty", "a Home Manager link"):
                skipped.append(f"~/{x.rel}: {x.reason}")
        if adopted:
            r.carried(plural(len(adopted), "other config file or directory", "other config files and directories"),
                      "config/, linked with dotkit.links")
        if taken:
            r.todo("Other config", "", taken)
        if left:
            r.todo("Other config", "These stay in place and keep working. To keep one in the repository, copy "
                                   "it to config/ and link it in nix/home.nix, for example "
                                   "`dotkit.links.\".config/nvim\" = \"config/nvim\";`.", left)
        if skipped:
            r.todo("Other config that stays out of the repository",
                   "These stay in place. They hold state, caches, or logins, so they are not for Git.", skipped)
