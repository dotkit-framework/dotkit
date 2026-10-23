"""Data: the version managers, the shell frameworks, and the rules for tools.

The scan finds the version managers and frameworks of this list, and rcparse
builds its rules from their words, variables, and PATH markers. A new version
manager or framework is a new entry here.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .context import expand, read_json, read_text

# Homebrew without its update, hints, and analytics: the scan only reads.
BREW_ENV = {
    "HOMEBREW_NO_AUTO_UPDATE": "1",
    "HOMEBREW_NO_ENV_HINTS": "1",
    "HOMEBREW_NO_ANALYTICS": "1",
    "HOMEBREW_NO_INSTALL_CLEANUP": "1",
}


@dataclass(frozen=True)
class VersionManagerSpec:
    name: str
    # Where it lives, relative to the home directory; the first that exists wins.
    # env_var, when it is set, wins over them (manager_dir).
    dirs: tuple[str, ...] = ()
    tool: str = ""  # the mise tool; "" for asdf, which has one tool per plugin
    versions: str = ""  # the directory of the versions, relative to its directory
    # The global version, read from its directory; "" when it has none.
    default: Callable[[Path], str] | None = None
    # The variable that moves its directory, as NVM_DIR.
    env_var: str = ""
    # mise can take over its versions. False: the report says what to do instead.
    mise_replaces: bool = False
    # Words of shell lines that use it: whole words, matched as they are.
    words: tuple[str, ...] = ()
    # Variables that only it reads: an assignment of one is not carried.
    extra_vars: tuple[str, ...] = ()
    # Regular expressions for a PATH directory of it: each follows a "/" and
    # comes before a "/" or the end of the directory.
    path_markers: tuple[str, ...] = ()
    # Where it keeps the global npm packages of each Node version: a glob
    # relative to its directory, and the index of the version in the parts
    # of a found path.
    npm_globs: tuple[tuple[str, int], ...] = ()
    # False: a line-only spec. The scan does not look for it, and rcparse
    # still knows its lines.
    detect: bool = True


def _first_line(rel: str) -> Callable[[Path], str]:
    return lambda root: read_text(root / rel).split("\n")[0].strip()


def _link_name(rel: str, index: int) -> Callable[[Path], str]:
    """A part of the target of a link: the global version of sdkman and fnm."""

    def read(root: Path) -> str:
        p = root / rel
        return os.readlink(p).rstrip("/").split("/")[index] if p.is_symlink() else ""

    return read


def _volta_default(root: Path) -> str:
    meta = read_json(root / "tools/user/platform.json") or {}
    return ((meta.get("node") or {}).get("runtime")) or ""


# In scan order.
VERSION_MANAGERS: tuple[VersionManagerSpec, ...] = (
    VersionManagerSpec(
        "nvm", (".nvm",), "node", "versions/node", _first_line("alias/default"),
        env_var="NVM_DIR", mise_replaces=True, words=("nvm", "NVM_DIR"), extra_vars=("NVM_DIR",),
        path_markers=(r"\.nvm",),
        npm_globs=(("versions/node/*/lib/node_modules", -3),)),
    VersionManagerSpec(
        "fnm", (".local/share/fnm", "Library/Application Support/fnm", ".fnm"), "node", "node-versions",
        _link_name("aliases/default", -2),
        env_var="FNM_DIR", mise_replaces=True, words=("fnm",), extra_vars=("FNM_DIR",), path_markers=(r"\.fnm", "fnm"),
        npm_globs=(("node-versions/*/installation/lib/node_modules", -4),)),
    VersionManagerSpec(
        "volta", (".volta",), "node", "tools/image/node", _volta_default,
        env_var="VOLTA_HOME", mise_replaces=True, words=("volta", "VOLTA_HOME"), extra_vars=("VOLTA_HOME",),
        path_markers=(r"\.volta",)),
    VersionManagerSpec(
        "pyenv", (".pyenv",), "python", "versions", _first_line("version"),
        env_var="PYENV_ROOT", mise_replaces=True, words=("pyenv", "PYENV_ROOT"), extra_vars=("PYENV_ROOT",),
        path_markers=(r"\.pyenv",)),
    VersionManagerSpec(
        "rbenv", (".rbenv",), "ruby", "versions", _first_line("version"),
        env_var="RBENV_ROOT", mise_replaces=True, words=("rbenv",), extra_vars=("RBENV_ROOT",),
        path_markers=(r"\.rbenv",)),
    VersionManagerSpec(
        "asdf", (".asdf",), "", "installs", env_var="ASDF_DATA_DIR",
        mise_replaces=True, words=("asdf",), extra_vars=("ASDF_DIR", "ASDF_DATA_DIR"), path_markers=(r"\.asdf",),
        npm_globs=(("installs/nodejs/*/lib/node_modules", -3),)),
    VersionManagerSpec(
        "sdkman", (".sdkman",), "java", "candidates/java", _link_name("candidates/java/current", -1),
        env_var="SDKMAN_DIR",
        words=("sdkman", "SDKMAN"), extra_vars=("SDKMAN_DIR",), path_markers=(r"\.sdkman",)),
    VersionManagerSpec(
        "jenv", (".jenv",), "java", "versions", _first_line("version"), env_var="JENV_ROOT",
        words=("jenv",), extra_vars=("JENV_ROOT",), path_markers=(r"\.jenv",)),
    VersionManagerSpec("rvm", words=("rvm",), path_markers=(r"\.rvm",), detect=False),
    VersionManagerSpec("nodenv", words=("nodenv",), extra_vars=("NODENV_ROOT",), path_markers=(r"\.nodenv",),
                       detect=False),
    VersionManagerSpec("goenv", words=("goenv",), extra_vars=("GOENV_ROOT",), path_markers=(r"\.goenv",),
                       detect=False),
    VersionManagerSpec("chruby", words=("chruby",), detect=False),
    VersionManagerSpec("tfenv", words=("tfenv",), path_markers=(r"\.tfenv",), detect=False),
    # Only a directory below anaconda3/ or miniconda3/, not the directory itself.
    VersionManagerSpec("conda", words=("conda",), path_markers=(r"(?:ana|mini)conda\d?(?=/)",), detect=False),
    VersionManagerSpec("mamba", words=("mamba",), detect=False),
    VersionManagerSpec("micromamba", words=("micromamba",), detect=False),
)

# asdf plugin names that mise names differently.
ASDF_TOOLS = {"nodejs": "node", "golang": "go"}


def version_manager(name: str) -> VersionManagerSpec:
    return next(spec for spec in VERSION_MANAGERS if spec.name == name)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def manager_dir(spec: VersionManagerSpec, home: Path, env: Mapping[str, str]) -> Path | None:
    """The directory of a version manager, or None when it has none.

    Its variable, when it is an absolute path or starts with ~ (the home of
    the scan), is the directory, and the usual places are not tried: a
    variable that points to nothing, to a file, or to the home or one of
    its parents means that the manager is not there. A blank or relative
    value counts as unset. Otherwise the first of its dirs that exists.
    """
    value = (env.get(spec.env_var) or "").strip() if spec.env_var else ""
    if value == "~" or value.startswith("~/") or value.startswith("/"):
        p = Path(expand(value, str(home)))
        # By file identity: also a link to the home, or its path in another case (APFS).
        if not p.is_dir() or any(_same_file(p, q) for q in (home, *home.parents)):
            return None
        return p
    return next((home / d for d in spec.dirs if (home / d).is_dir()), None)


def display_name(tool: str) -> str:
    """The name of a mise tool in report text: node -> Node."""
    return tool.capitalize()


@dataclass(frozen=True)
class FrameworkSpec:
    name: str
    # What the scan looks for, relative to the home directory; the first that exists wins.
    paths: tuple[str, ...]
    # Variables that only it reads: an assignment of one is not carried.
    vars: tuple[str, ...] = ()
    # Regular expressions for shell lines that use it.
    words: tuple[str, ...] = ()


# In scan order.
FRAMEWORKS: tuple[FrameworkSpec, ...] = (
    FrameworkSpec(
        "Oh My Zsh", (".oh-my-zsh",),
        vars=("ZSH", "ZSH_THEME", "ZSH_CUSTOM", "ZSH_CACHE_DIR", "plugins", "DISABLE_AUTO_UPDATE",
              "DISABLE_UPDATE_PROMPT", "UPDATE_ZSH_DAYS", "DISABLE_AUTO_TITLE", "ENABLE_CORRECTION",
              "COMPLETION_WAITING_DOTS", "HIST_STAMPS", "CASE_SENSITIVE", "HYPHEN_INSENSITIVE",
              "DISABLE_MAGIC_FUNCTIONS", "DISABLE_LS_COLORS", "DISABLE_UNTRACKED_FILES_DIRTY"),
        words=("oh-my-zsh", r"\$ZSH\b", r"\$\{ZSH\}", "ZSH_THEME", r"\bomz\b", r"\bplugins=\(")),
    FrameworkSpec("Prezto", (".zprezto",), words=("prezto", "zprezto")),
    FrameworkSpec("zinit", (".zinit", ".local/share/zinit"), vars=("ZINIT_HOME", "ZINIT"),
                  words=(r"\bzinit\b", r"zi\.zsh")),
    FrameworkSpec("Powerlevel10k", (".p10k.zsh", "powerlevel10k", ".powerlevel10k"),
                  words=("powerlevel10k", "powerlevel9k", "POWERLEVEL9K", r"\bp10k\b", "p10k-instant-prompt")),
    FrameworkSpec("antigen", (".antigen", "antigen.zsh"), words=("antigen",)),
    FrameworkSpec("antidote", (".antidote", ".cache/antidote"), vars=("ANTIDOTE_HOME",),
                  words=("antidote", "zsh_plugins")),
    FrameworkSpec("Zim", (".zim",), vars=("ZIM_HOME",), words=("zimfw", r"\.zim\b")),
    FrameworkSpec("zplug", (".zplug",), words=(r"\bzplug\b",)),
)

# Variables of zsh itself that frameworks set: an assignment is reported as
# a framework line, without a framework.
UNATTRIBUTED_FRAMEWORK_VARS = ("ZDOTDIR",)

# Global npm packages that do not go to mise, with what the report says.
NPM_NOT_IN_MISE = {
    "pnpm": "dotkit has no global pnpm; each project supplies its own, with mise or corepack. "
            "`npm uninstall -g pnpm` when you no longer need it.",
}

# mise backends that build their tools from source, with the name in report
# text: off in the wizard by default, and added by `dotkit migrate` only with --all.
BUILT_FROM_SOURCE = {"cargo": "cargo", "go": "Go"}

# The commands of mise tools whose commands are not only their name: the runtimes, and uv. Another mise
# tool has one command, its name (base.mise_short). For the leftovers that can shadow them
# (ARCHITECTURE.md, section 11).
MISE_COMMANDS = {
    "node": ("node", "npm", "npx"),
    "python": ("python3", "python", "pip3"),
    "ruby": ("ruby", "gem"),
    "uv": ("uv",),
}
