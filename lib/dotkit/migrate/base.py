"""What the dotkit base already has: its lists, its links, and its tool setup.

The lists and config files are read from the dotkit directory that this
package is part of (the flake source), so they match the release that setup
builds. tests/test_base.py checks the tables below against nix/home.nix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from ..core import lists, mise_toml
from ._compat import tomllib

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Link:
    """A file that the base links in the home directory."""

    target: str  # relative to the home directory
    source: str  # in the dotkit directory
    # The file of yours that the tool reads after the base file, or None
    # when the tool reads one file only (then yours can replace the link).
    layer: str | None = None
    # The file in the dotfiles repository for your copy.
    repo_file: str = ""


# The base links of nix/home.nix that a person may have a file for already.
# The shell files are read by the rc parser instead (see SHELL_LAYERS).
LINKS: tuple[Link, ...] = (
    Link(".config/git/config", "config/gitconfig", ".gitconfig", "config/gitconfig"),
    Link(".config/git/ignore", "config/gitignore", None, "config/git/ignore"),
    Link(".config/ghostty/config", "config/ghostty.conf", ".config/ghostty/config.local", "config/ghostty.conf"),
    Link(".config/herdr/config.toml", "config/herdr.toml", None, "config/herdr.toml"),
    Link(".config/starship.toml", "config/starship.toml", None, "config/starship.toml"),
    Link(".gemrc", "config/gemrc", None, "config/gemrc"),
    Link(".bunfig.toml", "config/bunfig.toml", None, "config/bunfig.toml"),
    Link(".editorconfig", "config/editorconfig", None, "config/editorconfig"),
    Link("Library/Preferences/pnpm/config.yaml", "config/pnpm.yaml", None, "config/pnpm.yaml"),
)

@dataclass(frozen=True)
class OwnFile:
    """A config file of yours that its tool reads after the dotkit settings, which the base puts elsewhere."""

    target: str  # relative to the home directory
    source: str  # the dotkit settings, in the dotkit directory
    base_place: str  # where the tool reads the dotkit settings
    repo_file: str  # the file in the dotfiles repository for your copy


# nix/home.nix sets NPM_CONFIG_GLOBALCONFIG; nix/darwin.nix writes /etc/uv/uv.toml.
OWN_FILES: tuple[OwnFile, ...] = (
    OwnFile(".npmrc", "config/npmrc", "the global config of npm", "config/npmrc"),
    OwnFile(".config/uv/uv.toml", "config/uv.toml", "/etc/uv/uv.toml", "config/uv.toml"),
)

# The shell files that the base links, the file of yours that each one reads
# after it, and the file in the dotfiles repository for the carried lines.
SHELL_LAYERS: dict[str, tuple[str, str]] = {
    ".zshenv": (".zshenv.local", "config/zsh/zshenv"),
    ".zprofile": (".zprofile.local", "config/zsh/zprofile"),
    ".zshrc": (".zshrc.local", "config/zsh/zshrc"),
    ".bash_profile": (".bash_profile.local", "config/bash/bash_profile"),
}

# dotkit.toolPath of nix/home.nix, in its order: the directories of DOTKIT_TOOL_PATH,
# with <user> for the account name and ~ for the home directory. They are on
# PATH before mise starts, and GUI apps have them before the mise shims
# (nix/darwin.nix). tests/test_base.py compares the two.
TOOL_PATH = (
    "/etc/profiles/per-user/<user>/bin",
    "/run/current-system/sw/bin",
    "~/.local/bin",
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
)


def tool_path(home: str, user: str) -> list[str]:
    """TOOL_PATH for one account: absolute directories."""
    return [home + d[1:] if d.startswith("~/") else d.replace("<user>", user) for d in TOOL_PATH]


# Every target that the base links. nix/home.nix has these and the herdr
# scripts; tests/test_base.py compares the two.
BASE_TARGETS = frozenset(
    [link.target for link in LINKS]
    + list(SHELL_LAYERS)
    + [
        ".config/herdr/git-status.py",
        ".config/herdr/git-status.sh",
        ".config/herdr/process-name.bash",
        ".config/herdr/process-name.sh",
        ".config/herdr/process-name.zsh",
        ".config/mise/conf.d/dotkit.toml",
        ".local/share/dotkit",
    ]
)

# ~/.config entries that the base manages a file in. Other entries are yours.
# Home Manager links files into direnv (nix-direnv) and zsh (the zsh setup).
BASE_CONFIG_DIRS = frozenset({
    "git", "ghostty", "herdr", "mise", "starship.toml", "uv", "zsh", "direnv", "home-manager", "nix",
})

# Homebrew formula names for tools of the base whose Nix or mise name differs.
BREW_ALIASES = {
    "yq": "yq-go",
    "tlrc": "tealdeer",
    "gpg": "gnupg",
    "gnupg@2": "gnupg",
    "node": "node",
    "nodejs": "node",
    "python": "python",
    "python3": "python",
    "zulu@17": "zulu17",
    "openjdk@17": "zulu17",
}

# Formulae that the base supplies in another way than its lists.
BREW_PROVIDED_OTHERWISE = {
    "direnv": "direnv (Home Manager)",
    "nix-direnv": "nix-direnv (Home Manager)",
    "zsh": "zsh (nix-darwin, the login shell)",
    "zsh-completions": "the dotkit zsh setup",
    "zsh-autosuggestions": "the dotkit zsh setup",
    "zsh-syntax-highlighting": "the dotkit zsh setup",
    "zsh-history-substring-search": "the dotkit zsh setup",
    "zsh-abbr": "the dotkit zsh setup",
    "font-jetbrains-mono": "the JetBrains Mono font (nix-darwin)",
    "font-jetbrains-mono-nerd-font": "the JetBrains Mono Nerd Font (nix-darwin)",
}


def read_list(path: Path) -> list[str]:
    """Names in a list file (core/lists.py, the rules of nix/read-list.nix); [] when it cannot be read."""
    try:
        return lists.names(path)
    except OSError:
        return []


def read_taps(path: Path) -> list[str]:
    """Tap names in nix/taps.txt (core/lists.py, the rules of nix/read-taps.nix); [] when it cannot be read."""
    try:
        return lists.taps(path)
    except OSError:
        return []


def read_mas(path: Path) -> dict[str, int]:
    """The apps in nix/mas.txt, name -> ID, as nix/read-mas.nix reads them before it escapes the names."""
    apps: dict[str, int] = {}
    try:
        for app_id, name in lists.mas_apps(path):
            apps.setdefault(name, int(app_id))
    except OSError:
        pass
    return apps


def _mise_table(path: Path, name: str) -> dict[str, object]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    table = data.get(name)
    return table if isinstance(table, dict) else {}


def mise_tools(path: Path) -> dict[str, object]:
    """The [tools] table of a mise config file (core/mise_toml.py), or {} when it cannot be read."""
    try:
        return mise_toml.tools(path)
    except (OSError, mise_toml.InvalidToml):
        return {}


mise_short = mise_toml.short


class Base:
    """The dotkit base at a directory (default: the one of this package)."""

    def __init__(self, root: Path = ROOT):
        self.root = root

    @cached_property
    def packages(self) -> list[str]:
        return read_list(self.root / "nix/packages.txt")

    @cached_property
    def brews(self) -> list[str]:
        return read_list(self.root / "nix/brews.txt")

    @cached_property
    def casks(self) -> list[str]:
        return read_list(self.root / "nix/casks.txt")

    @cached_property
    def mise(self) -> dict[str, object]:
        return mise_tools(self.root / "config/mise.toml")

    @cached_property
    def mise_settings(self) -> dict[str, object]:
        """The [settings] table of the base config/mise.toml."""
        return _mise_table(self.root / "config/mise.toml", "settings")

    @property
    def template(self) -> Path:
        return self.root / "templates/default"

    def file(self, source: str) -> Path:
        return self.root / source

    def provides_brew(self, name: str, kind: str = "formula") -> str | None:
        """What the base has instead of Homebrew formula or cask `name`, or None."""
        short = name.rsplit("/", 1)[-1]
        if kind == "cask":
            if short in self.casks:
                return f"{short} (dotkit cask)"
            if short in BREW_PROVIDED_OTHERWISE:
                return BREW_PROVIDED_OTHERWISE[short]
            return None
        if short in self.brews:
            return f"{short} (dotkit formula)"
        if short in BREW_PROVIDED_OTHERWISE:
            return BREW_PROVIDED_OTHERWISE[short]
        alias = BREW_ALIASES.get(short, short)
        if alias in self.packages:
            return f"{alias} (dotkit, from Nix)"
        mise_names = {mise_short(k): k for k in self.mise}
        if alias in mise_names:
            return f"{mise_names[alias]} (dotkit, from mise)"
        return None
