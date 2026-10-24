"""Leftovers that can still take effect: report only (ARCHITECTURE.md, section 11).

The wizard removes nothing. Most leftovers are dead wood, but some still
act: a carried PATH line in config/zsh/zshrc that adds a new directory comes
before mise's tools in interactive shells; an old copy in a directory of
dotkit.toolPath comes before the mise shims in GUI apps and launchd agents;
and shell files that dotkit does not replace still run.

How mise orders PATH (mise 2026.10.3, activate_aggressive = false): a
directory that is added after `mise activate` stays in front of mise's tool
directories; one that was on PATH before goes behind them. Nested shells and
the order relative to direnv's hook were not checked, so no text here makes
a claim about them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .. import base as base_mod
from .. import catalog, rcparse
from ..base import mise_short
from ..context import Context, executables, is_managed, read_text, shell_path, tilde
from ..docs import Txn
from ..model import Choices, Facts
from . import Migrator, and_list, shell

SECTION = "Leftovers that can still take effect"

# Shell files that dotkit does not replace and that still run, with the shell that reads them.
STILL_RUN = ((".bashrc", "bash"), (".zlogin", "zsh"), (".zlogout", "zsh"), (".profile", "bash"))

# The repository files whose carried lines run before mise starts, by shell: their directories are on PATH
# before mise starts in that shell.
EARLY_ZSH = ("config/zsh/zshenv", "config/zsh/zprofile")
EARLY_BASH = ("config/bash/bash_profile",)

NEW_DIRS = ("These carried PATH lines in config/zsh/zshrc (~/.zshrc.local), which runs after mise starts, put a "
            "directory in front of PATH that was not on PATH before. In interactive shells, that directory comes "
            "before mise's tools, also for a command that you install there later. Drop a line that you no longer "
            "need, or move it to config/zsh/zprofile (~/.zprofile.local), which runs before mise starts.")
INTERACTIVE = ("In interactive shells, these commands in a directory of a carried PATH line come before the mise "
               "tool of the same name. In this order: drop the line from config/zsh/zshrc; move it to "
               "config/zsh/zprofile (~/.zprofile.local), which runs before mise starts; uninstall the old copy; or, "
               "as a last resort, set `activate_aggressive = true` in [settings] of config/mise.toml, which acts "
               "only on full hook-env runs and does not change GUI apps.")
GUI = ("GUI apps and launchd agents have a fixed PATH: dotkit's tool directories (the Nix profiles, ~/.local/bin, "
       "/opt/homebrew/bin, and /opt/homebrew/sbin), then the mise shims. So a command there with the name of a mise "
       "tool comes before its shim. Uninstall the old copy when the mise copy works; `activate_aggressive` does not "
       "change GUI apps.")
STILL_RUNS = ("dotkit does not replace these shell files, and they still run: ~/.bashrc in interactive bash, after "
              "mise starts; ~/.zlogin and ~/.zlogout, which zsh reads; ~/.profile in `sh -l`. These lines in them "
              "load a shell framework or a version manager, or add a directory to PATH. Remove the ones you no "
              "longer need.")


@dataclass
class LeftoverFacts:
    home: str
    brew_prefix: str
    # The executables of ~/.local/bin, <brew_prefix>/bin, and <brew_prefix>/sbin, by directory: name -> link
    # target ("" for a file that is not a link). Names and links only: no file is opened.
    executables: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # STILL_RUN files that exist, parsed in report-only mode: nothing of them is carried.
    still_run: Dict[str, rcparse.Result] = field(default_factory=dict)


def facts_of(f: Facts) -> Optional[LeftoverFacts]:
    return f.get(Leftovers.key)


@dataclass
class MiseTool:
    """A tool that mise has after setup: one that this run added to config/mise.toml, or one of the base."""

    key: str  # its mise key: npm:typescript, node
    commands: Set[str] = field(default_factory=set)
    base: bool = False  # the dotkit base has it, and this run did not add it


def mise_tools(f: Facts, added: List[str]) -> List[MiseTool]:
    """The tools that this run added to config/mise.toml (`added`, in that order), then the tools of the base,
    with their commands. A tool that a migrator can add has the commands it names (Facts.mise_commands, from
    Migrator.mise_commands); another tool those of catalog.MISE_COMMANDS, or its name."""
    packages = f.mise_commands
    out: List[MiseTool] = []
    for spec, base in [(k, False) for k in added] + [(k, True) for k in f.base.mise if k not in added]:
        if spec in packages:
            commands = set(packages[spec])
        else:
            commands = set(catalog.MISE_COMMANDS.get(spec, (mise_short(spec),)))
        if commands:
            out.append(MiseTool(spec, commands, base))
    return out


def homebrew_package(target: str) -> Optional[Tuple[str, str]]:
    """The Homebrew package a binary links into: ("formula", name) for Cellar/<name>/, ("cask", name) for
    Caskroom/<name>/; None otherwise."""
    parts = Path(target).parts if target else ()
    for i in range(len(parts) - 1):
        if parts[i] in ("Cellar", "Caskroom"):
            return ("formula" if parts[i] == "Cellar" else "cask", parts[i + 1])
    return None


def removal(target: str) -> str:
    """The command that removes the package a binary links into, from its link target; "" when it is not
    a link, or not into a package that this knows:
    Cellar/<formula>/, Caskroom/<cask>/, pipx/venvs/<package>/, uv/tools/<package>/, lib/node_modules/<package>/."""
    parts = Path(target).parts if target else ()
    for i in range(len(parts) - 1):
        nxt = parts[i + 1]
        if parts[i] == "Cellar":
            return f"`brew uninstall {nxt}`"
        if parts[i] == "Caskroom":
            return f"`brew uninstall --cask {nxt}`"
        if i + 2 < len(parts) and (parts[i], nxt) == ("pipx", "venvs"):
            return f"`pipx uninstall {parts[i + 2]}`"
        if i + 2 < len(parts) and (parts[i], nxt) == ("uv", "tools"):
            return f"`uv tool uninstall {parts[i + 2]}`"
        if i + 2 < len(parts) and (parts[i], nxt) == ("lib", "node_modules"):
            name = parts[i + 2]
            if name.startswith("@") and i + 3 < len(parts):
                name += "/" + parts[i + 3]
            # npm and corepack come with node: removing them breaks it.
            return "" if name in ("npm", "corepack") else f"`npm uninstall -g {name}`"
    return ""


def is_new(d: str, home: str, user: str, early: Set[str]) -> bool:
    """A directory that is not on PATH before mise starts: not in dotkit.toolPath, not one that rcparse leaves
    out (rcparse.path_done: dotkit's, the system's), and not one that a line before mise starts adds."""
    d = d.rstrip("/") or "/"
    return not (d in base_mod.tool_path(home, user) or rcparse.path_done(d, home) or d in early)


def _shadowed(names: Dict[str, str], tools: List[MiseTool]) -> List[Tuple[MiseTool, List[str]]]:
    """The tools that a command of `names` shadows, with those commands. A command shadows only the first tool
    that has it."""
    out, taken = [], set()
    for t in tools:
        cmds = sorted((t.commands & set(names)) - taken)
        if cmds:
            taken.update(cmds)
            out.append((t, cmds))
    return out


def _fix(d: str, names: Dict[str, str], cmds: List[str], home: str) -> str:
    """How to remove these commands of a directory: the removal of the package each one links into, or how to
    find out where it comes from."""
    out: List[str] = []
    for cmd in cmds:
        text = removal(names[cmd])
        text = (f"Old copy: {text}." if text else
                f"`ls -l {shell_path(tilde(str(Path(d) / cmd), home))}` shows where it comes from.")
        if text not in out:
            out.append(text)
    return " ".join(out)


def _kept(names: Dict[str, str], cmds: List[str], listed: Dict[str, Set[str]]) -> Dict[str, Tuple[str, str]]:
    """The commands of cmds from a Homebrew package that the lists of this repository keep: command -> (kind,
    name). The next apply would install such a package again, so `brew uninstall` is no fix for them."""
    out = {}
    for cmd in cmds:
        pkg = homebrew_package(names[cmd])
        if pkg and pkg[1] in listed[pkg[0]]:
            out[cmd] = pkg
    return out


def _kept_item(path: str, cmd: str, pkg: Tuple[str, str], who: str) -> str:
    """The item of a shadowing binary of a package in the lists: dotkit remove, or keep it."""
    kind, name = pkg
    rel, prefix = ("nix/brews.txt", "brew") if kind == "formula" else ("nix/casks.txt", "cask")
    depends = " (brew says when another formula depends on it)" if kind == "formula" else ""
    return (f"`{path}` comes from `{name}`, which is in {rel}. {who} run it instead of mise's {cmd}. To use "
            f"mise's: `dotkit remove {prefix}:{name}`, which takes it out of the list and uninstalls it{depends}. "
            f"Or keep it, if you need this {cmd}.")


def _commands(names: List[str]) -> str:
    return and_list([f"`{n}`" for n in names])


def _what(t: MiseTool) -> str:
    return f"{t.key}, of the dotkit base" if t.base else t.key


def _where(lines: Tuple[int, int]) -> str:
    a, b = lines
    return str(a) if a == b else f"{a}-{b}"


class Leftovers(Migrator):
    key = "leftovers"
    title = "Leftovers that can still take effect"
    sections = (SECTION,)

    def manual_steps(self, ctx: Context) -> str:
        return ("MIGRATION.md does not list the leftovers that can still take effect. Check by hand: carried PATH "
                "lines in config/zsh/zshrc that put a new directory in front of PATH, which comes before mise's "
                "tools in interactive shells; old copies of mise tools in ~/.local/bin, /opt/homebrew/bin, and "
                "/opt/homebrew/sbin, which GUI apps run before the mise shims; and ~/.bashrc, ~/.zlogin, "
                "~/.zlogout, and ~/.profile, which still run.")

    def scan(self, ctx: Context) -> LeftoverFacts:
        lf = LeftoverFacts(str(ctx.home), str(ctx.brew_prefix))
        for d in (ctx.home / ".local/bin", ctx.brew_prefix / "bin", ctx.brew_prefix / "sbin"):
            names = executables(d)
            if names:
                lf.executables[str(d)] = names
        for name, sh in STILL_RUN:
            p = ctx.home / name
            if p.is_file() and not is_managed(p):
                lf.still_run[name] = rcparse.Parser(str(ctx.home), sh).parse(name, read_text(p))
        return lf

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        lf = facts_of(f)
        if lf is None:
            return
        r = txn.report
        home, user = lf.home, f.identity.user
        tools = mise_tools(f, txn.mise().added())
        # The Homebrew packages of this repository's lists, as the plan renders them.
        listed = {"formula": set(txn.list("nix/brews.txt").names()), "cask": set(txn.list("nix/casks.txt").names())}
        paths = shell.carried_paths(f, c)
        early_zsh = {d for _, _, dirs, _, repo_file in paths if repo_file in EARLY_ZSH for d in dirs}
        early_bash = {d for _, _, dirs, _, repo_file in paths if repo_file in EARLY_BASH for d in dirs}

        # Carried PATH lines in config/zsh/zshrc that put a new directory in front: interactive shells. A
        # directory that a line appends goes behind mise's tools.
        sh = shell.facts_of(f)
        new_dirs, shadows = [], []
        for name, lines, _, front, repo_file in paths:
            if repo_file != "config/zsh/zshrc":
                continue
            for d in front:
                if not is_new(d, home, user, early_zsh):
                    continue
                where = f"~/{name}:{_where(lines)}"
                new_dirs.append(f"{where} adds {tilde(d, home)}")
                names = sh.executables.get(d, {}) if sh else {}
                for t, cmds in _shadowed(names, tools):
                    kept = _kept(names, cmds, listed)
                    cmds = [x for x in cmds if x not in kept]
                    if cmds:
                        shadows.append(f"{_commands(cmds)} in {tilde(d, home)} ({_what(t)}), from {where}: in "
                                       f"interactive shells, {'it comes' if len(cmds) == 1 else 'they come'} before "
                                       f"mise's. {_fix(d, names, cmds, home)}")
                    shadows += [_kept_item(tilde(str(Path(d) / x), home), x, pkg,
                                           f"Interactive shells, through {where},") for x, pkg in kept.items()]
        if new_dirs:
            r.todo(SECTION, NEW_DIRS, new_dirs)
        if shadows:
            r.todo(SECTION, INTERACTIVE, shadows)

        # Old copies in the directories of dotkit.toolPath: GUI apps and launchd agents.
        items = []
        for d, names in lf.executables.items():
            for t, cmds in _shadowed(names, tools):
                kept = _kept(names, cmds, listed)
                cmds = [x for x in cmds if x not in kept]
                if cmds:
                    copies = "this copy comes before its mise shim" if len(cmds) == 1 else \
                        "these copies come before their mise shims"
                    items.append(f"{_commands(cmds)} in {tilde(d, home)} ({_what(t)}): in GUI apps and launchd "
                                 f"agents, {copies}; interactive shells use mise's. {_fix(d, names, cmds, home)}")
                items += [_kept_item(tilde(str(Path(d) / x), home), x, pkg, "GUI apps and launchd agents")
                          for x, pkg in kept.items()]
        if items:
            r.todo(SECTION, GUI, items)

        # Shell files that dotkit does not replace: they still run. ~/.profile is for sh -l, which reads
        # none of dotkit's shell files.
        early = {".bashrc": early_bash, ".zlogin": early_zsh, ".zlogout": early_zsh}
        items = []
        for name, res in lf.still_run.items():
            found = []
            for x in res.findings:
                if x.category in (rcparse.FRAMEWORK, rcparse.VERSION_MANAGER) or (x.category == rcparse.PATHS
                                                                                  and x.source):
                    found.append((x.lines, f"~/{name}:{x.where} still runs {shell.finding_code(x)}"
                                  + (f" ({x.source})" if x.source else "")))
            for cl in res.carried:
                new = [tilde(d, home) for d in cl.dirs if is_new(d, home, user, early.get(name, set()))]
                if cl.kind == "path" and new:
                    found.append((cl.lines, f"~/{name}:{_where(cl.lines)} still runs a PATH line that adds "
                                            f"{and_list(new)}"))
            items += [text for _, text in sorted(found)]
        if items:
            r.todo(SECTION, STILL_RUNS, items)
