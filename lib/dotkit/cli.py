"""The command line: one entry per command in COMMANDS, parsed with argparse.

The first word selects the command; the parser of the command then reads
the rest with parse_intermixed_args, so options can come anywhere after the
command (`dotkit add -n ripgrep -y fd`). argparse has no subparsers with
intermixed options, hence the two steps. Each command module imports only
when its command runs. `dotkit __complete` does not come here (complete.py).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, NoReturn, Optional, Sequence

from .core import ui
from .core.env import Env

EPILOG_ADD = """\
A bare name goes to the first source that has it: a Homebrew cask with a
macOS app, Nixpkgs when it has the current release, the mise registry, then
a Homebrew formula. In a terminal, add lets you select the source.

A prefix selects the source. Use the one that matches the install line in the
tool's README:
  nix:ATTR    Nixpkgs                                  -> nix/packages.txt
  brew:NAME   brew install NAME (formula, else cask)   -> nix/brews.txt, casks.txt
  cask:NAME   brew install --cask NAME                 -> nix/casks.txt
  mise:NAME   the mise registry                        -> config/mise.toml
  npm:PKG     npm install -g PKG                       -> config/mise.toml
  pipx:PKG    pipx install PKG, uv tool install PKG    -> config/mise.toml
  mas:ID      mas install ID, an App Store app         -> nix/mas.txt
  Other mise backends: cargo:CRATE, go:MODULE, github:OWNER/REPO, aqua:OWNER/REPO, ...

Examples:
  dotkit add lazygit           Nix, when Nixpkgs has the current release
  dotkit add hk                mise, when Nixpkgs is older or does not have it
  dotkit add zed               Homebrew, because it is a macOS app
  dotkit add @biomejs/biome    npm, because the name is a scoped npm package
  dotkit add -n ripgrep fd     show the plan only

Tools in the dotkit base are not added again. Homebrew taps: edit nix/taps.txt."""

EXAMPLES = """\
Examples:
  dotkit add lazygit          Nix, mise, or Homebrew: dotkit add selects one
  dotkit add npm:cowsay       A prefix selects the source (dotkit add --help)
  dotkit upgrade -n           Show what is outdated
  dotkit upgrade mise         Upgrade only the mise tools
  DOTKIT_DIR=~/.dotkit dotkit apply
                              Use a dotkit checkout instead of the locked release

New Mac: see the dotkit README."""

OLD_NAMES = {
    "switch": "apply", "build": "apply -n", "diff": "apply -n", "update": "upgrade nix (or upgrade <input>)",
    "brew": "upgrade brew", "tools": "upgrade mise", "changes": "history", "where": "info", "gc": "clean",
}


BOLD, HEADING, NAME, CODE, DIM, RESET = "\033[1m", "\033[1;33m", "\033[32m", "\033[36m", "\033[2m", "\033[0m"
HEADING_LINE = re.compile(r"[A-Za-z][A-Za-z ]*:")
# The first column of a list: a command, an option, a prefix (nix:ATTR).
FIRST_COLUMN = re.compile(r"(  )([^\s,]+(?:, [^\s,]+)*)(.*)")
# An example: the command, two spaces or more, and what it does.
EXAMPLE = re.compile(r"(  )(\S.*?)(\s{2,}\S.*)?")


def style(text: str) -> str:
    """The help in colors: headings, the commands and options, the examples, and `code`."""
    out, section = [], ""
    for line in text.split("\n"):
        if HEADING_LINE.fullmatch(line):
            section = line
            line = HEADING + line + RESET
        elif line.startswith("usage: "):
            words = line[len("usage: "):].split(" ", 2)
            cmd = " ".join(words[:2]) if len(words) > 1 and not words[1].startswith(("<", "[")) else words[0]
            line = f"{HEADING}usage:{RESET} {BOLD}{cmd}{RESET}{line[len('usage: ') + len(cmd):]}"
        elif line and not line[0].isspace():
            section = ""
        elif section == "Examples:":
            if line.startswith("   "):
                line = DIM + line + RESET
            else:
                m = EXAMPLE.fullmatch(line)
                if m:
                    line = f"{m[1]}{CODE}{m[2]}{RESET}" + (f"{DIM}{m[3]}{RESET}" if m[3] else "")
        else:
            m = FIRST_COLUMN.fullmatch(line)
            if m and (section or ":" in m[2]):
                line = f"{m[1]}{NAME}{m[2]}{RESET}{m[3]}"
        line = re.sub(r"`([^`]+)`", lambda m: f"{CODE}{m[1]}{RESET}", line)
        out.append(line)
    return "\n".join(out)


def show_help(text: str) -> None:
    ui.out(style(text) if ui.colors(sys.stdout) else text)


class Parser(argparse.ArgumentParser):
    """argparse with the errors of dotkit: an exception that main() prints, not sys.exit."""

    def __init__(self, **kw):
        super().__init__(allow_abbrev=False, formatter_class=argparse.RawDescriptionHelpFormatter, **kw)
        # The title of Python 3.10 and newer, also on 3.9 ("optional arguments").
        self._optionals.title = "options"

    def parse(self, args: List[str]) -> argparse.Namespace:
        """Options can come anywhere. parse_intermixed_args does not handle `--`: after it, the words are
        arguments, and the options must come before it."""
        return self.parse_args(args) if "--" in args else self.parse_intermixed_args(args)

    def print_help(self, file=None) -> None:
        show_help(self.format_help().rstrip())

    def error(self, message: str) -> NoReturn:
        raise ui.Exit(2, f"{self.format_usage().rstrip()}\n{self.prog}: error: {message}")

    def exit(self, status: int = 0, message: Optional[str] = None) -> NoReturn:
        raise ui.Exit(status, (message or "").rstrip())


def dry_run(p: Parser, what: str = "Show the plan. Change nothing.") -> None:
    p.add_argument("-n", "--dry-run", action="store_true", help=what)


def yes(p: Parser) -> None:
    p.add_argument("-y", "--yes", action="store_true", help="Do not ask. Required without a terminal.")


def plan_path(value: str) -> str:
    """The absolute path of --plan: the directory resolved, the file name kept (BEHAVIOR.md, O5)."""
    trimmed = value.rstrip("/") or "/"
    directory = os.path.dirname(trimmed) or "."
    if not os.path.isdir(directory):
        raise argparse.ArgumentTypeError(f"'{value}' is not in a directory that exists")
    return f"{os.path.realpath(directory)}/{os.path.basename(trimmed)}"


def source(value: str) -> str:
    if value not in ("nix", "brew", "mise"):
        raise argparse.ArgumentTypeError(f"'{value}' is not a source. Use nix, brew, or mise")
    return value


def age(value: str) -> str:
    if not (value[:-1].isdigit() and value.endswith("d") and value[:-1].isascii()):
        raise argparse.ArgumentTypeError(f"'{value}' is not an age. Use days, for example 30d")
    return value


# --- The commands. Each one: how to set up its parser, and how to run it with the parsed arguments.

def _apply(p: Parser):
    dry_run(p, "Build and show the changes. Change nothing.")
    return lambda env, a: _mod("commands.apply").apply(env, a.dry_run)


def _upgrade(p: Parser):
    dry_run(p)
    yes(p)
    p.add_argument("sources", nargs="*", metavar="source",
                   help="nix, brew, mise, or a flake input such as dotkit. Without one, all three.")
    return lambda env, a: _mod("commands.upgrade").upgrade(env, a.sources, a.dry_run, a.yes)


def _add(p: Parser):
    dry_run(p)
    yes(p)
    p.add_argument("-f", "--force", action="store_true",
                   help="Add a tool even when a global tool already supplies its command.")
    p.add_argument("tools", nargs="+", metavar="tool", help="A name, or a prefix and a name: nix:jq, npm:cowsay.")
    return lambda env, a: _mod("pkg.cli").Pkg(env, a.dry_run, a.yes, a.force).add(a.tools)


def _remove(p: Parser):
    dry_run(p)
    yes(p)
    p.add_argument("tools", nargs="+", metavar="tool", help="A name, or a prefix and a name: nix:jq, brew:wget.")
    return lambda env, a: _mod("pkg.cli").Pkg(env, a.dry_run, a.yes).remove(a.tools)


def _info(p: Parser):
    p.add_argument("tools", nargs="+", metavar="tool", help="A name, a command, or a prefix and a name.")
    return lambda env, a: _mod("pkg.cli").Pkg(env).info(a.tools)


def _list(p: Parser):
    # Not choices=: argparse checks the empty default of nargs="*" against them too.
    p.add_argument("sources", nargs="*", metavar="source", type=source,
                   help="nix, brew, or mise. Without one, all three.")
    return lambda env, a: _mod("pkg.cli").Pkg(env).list(a.sources)


def _doctor(p: Parser):
    return lambda env, a: _mod("commands.overlap").doctor(env)


def _migrate(p: Parser):
    dry_run(p)
    yes(p)
    p.add_argument("-a", "--all", action="store_true",
                   help="Also cargo and Go tools, which mise builds from source.")
    return lambda env, a: _mod("commands.wizard").migrate(env, a.dry_run, a.yes, a.all)


def _tidy(p: Parser):
    dry_run(p)
    yes(p)
    p.add_argument("--plan", type=plan_path, metavar="FILE", help="Read the edited plan from FILE.")
    return lambda env, a: _mod("commands.wizard").tidy(env, a.dry_run, a.yes, a.plan or "")


def _history(p: Parser):
    return lambda env, a: _mod("commands.recovery").history(env)


def _rollback(p: Parser):
    return lambda env, a: _mod("commands.recovery").rollback()


def _clean(p: Parser):
    dry_run(p)
    yes(p)
    p.add_argument("age", nargs="?", default="14d", type=age, help="Days, for example 30d. Default: 14d.")
    return lambda env, a: _mod("commands.recovery").clean(env, a.age, a.dry_run, a.yes)


def _mod(name: str):
    import importlib
    return importlib.import_module(f"dotkit.{name}")


# The groups of the usage, in order: the key in COMMANDS, and the title.
GROUPS = (
    ("Config", "Apply and update the config"),
    ("Tools", "Tools in the lists"),
    ("Check", "Check"),
    ("Mac", "Bring this Mac under dotkit"),
    ("Recovery", "Recovery and cleanup"),
)


@dataclass
class Command:
    group: str
    summary: str
    setup: Callable[[Parser], Callable[[Env, argparse.Namespace], int]]
    description: str = ""
    epilog: str = ""


COMMANDS: Dict[str, Command] = {
    "apply": Command("Config", "Apply the config: Nix, Homebrew, and mise.", _apply),
    "upgrade": Command("Config", "Get new releases. Shows one plan, asks once, then applies.", _upgrade),
    "add": Command("Tools", "Select the source for a tool, add it, and apply.", _add, epilog=EPILOG_ADD),
    "remove": Command("Tools", "Remove a tool from the config that declares it, and apply.", _remove,
                      "Homebrew packages that you remove are uninstalled. Tools of the dotkit base stay."),
    "info": Command("Tools", "Where a tool is declared, its command, and its sources.", _info),
    "list": Command("Tools", "The declared tools.", _list),
    "doctor": Command("Check", "What your dotfiles repeat or replace of the dotkit base.", _doctor,
                      "Compares the active system with the base: tools in the lists and config/mise.toml of\n"
                      "both, your links that replace base config files (with a diff when the files are\n"
                      "similar), and the files that an apply moved aside (*.before-dotkit). apply and\n"
                      "upgrade show the new ones."),
    "history": Command("Check", "The package changes of the last apply.", _history),
    "migrate": Command("Mac", "Add the tools that were installed by hand to the lists.", _migrate,
                       "Finds Homebrew packages and taps, App Store apps, and global npm and pipx tools that\n"
                       "were installed by hand and that the lists do not have. Asks, then adds them."),
    "tidy": Command("Mac", "Give the apps of this Mac a clean source, and remove old copies.", _tidy,
                    "Finds the apps from a DMG or an installer that have a cask, the App Store apps that the\n"
                    "lists do not have, old copies of tools that dotkit has (npm, pipx, uv, cargo, Homebrew,\n"
                    "files), and .pkg installer packages. Opens the plan in your editor, asks once, then\n"
                    "installs the casks, adds them to the lists, and uninstalls what you chose. App data\n"
                    "stays. See lib/dotkit/migrate/TIDY.md."),
    "rollback": Command("Recovery", "Go back to the previous Nix generation.", _rollback,
                        "Homebrew and mise do not roll back."),
    "clean": Command("Recovery", "Delete old Nix generations, Homebrew downloads, and mise versions.", _clean,
                     "Deletes Nix generations older than the age, old Homebrew downloads, and unused mise\n"
                     "versions."),
}


def usage() -> str:
    lines = ["usage: dotkit <command> [options]", "",
             "Day-to-day commands for a Mac with dotkit. Run them from any directory."]
    for group, title in GROUPS:
        lines += ["", f"{title}:"]
        lines += [f"  {name:<10} {c.summary}" for name, c in COMMANDS.items() if c.group == group]
    lines += ["", "Run `dotkit <command> --help` for the options of a command.", "", EXAMPLES]
    return "\n".join(lines)


def parser(name: str) -> Parser:
    c = COMMANDS[name]
    p = Parser(prog=f"dotkit {name}", description=(c.summary + ("\n\n" + c.description if c.description else "")),
               epilog=c.epilog or None)
    p.set_defaults(run=c.setup(p))
    return p


def main(env: Env, argv: Sequence[str]) -> int:
    cmd, rest = (argv[0], list(argv[1:])) if argv else ("", [])
    if cmd in ("", "-h", "--help"):
        show_help(usage())
        return 0
    if cmd == "help":
        if rest and rest[0] in COMMANDS:
            show_help(parser(rest[0]).format_help().rstrip())
        else:
            show_help(usage())
        return 0
    if cmd in OLD_NAMES:
        raise ui.Exit(2, f"dotkit: '{cmd}' is now '{OLD_NAMES[cmd]}'. Run dotkit --help.")
    if cmd not in COMMANDS:
        raise ui.Exit(2, f"dotkit: unknown command: {cmd}. Run dotkit --help.")
    p = parser(cmd)
    args = p.parse(rest)
    return args.run(env, args)
