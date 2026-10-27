"""The dotkit command: the usage, the dispatch, and the options (PARITY.md, rows C and O).

The tools come from three sources, and dotkit uses the same three words everywhere:
  nix   nix/packages.txt (CLIs), nix/flake.lock (the dotkit release, which
        pins Nixpkgs)
  brew  nix/casks.txt, nix/brews.txt, nix/taps.txt, nix/mas.txt (App Store apps)
  mise  config/mise.toml (runtimes and fast-moving CLIs)
The dotkit base has its own lists of each kind. `dotkit add` and `dotkit
remove` change only the lists of the dotfiles repository (dotkit/pkg).

DOTKIT_DIR selects a dotkit checkout instead of the release in nix/flake.lock,
for work on dotkit itself (core/env.py).
"""

from __future__ import annotations

import os
from typing import List, Sequence

from ..core import args, ui
from ..core.env import Env

USAGE = """\
Usage: dotkit <command> [options]

Daily:
  apply [-n]                  Apply the config: Nix, Homebrew, and mise.
                              -n builds and shows the changes, and changes nothing.
  upgrade [-n] [-y] [source]  Get new releases. Shows one plan, asks once, then applies.
                              source: nix, brew, mise, or a flake input (dotkit).
                              Without a source, all three.
  add [-n] [-y] <tool>...     Select the source for a tool, add it, and apply.
  remove [-n] [-y] <tool>...  Remove a tool from the config that declares it, and apply.
  info <tool>...              Where a tool is declared, its command, and its sources.
  list [nix|brew|mise]        The declared tools.
  migrate [-n] [-y] [--all]   Find Homebrew packages and taps, App Store apps, and
                              global npm and pipx tools that were installed by hand
                              and that the lists do not have. Asks, then adds them.
                              --all: also cargo and Go tools, which mise builds from
                              source.
  tidy [-n] [-y] [--plan FILE]
                              Give the apps of this Mac a clean source: find the apps
                              from a DMG or an installer that have a cask, and the App
                              Store apps that the lists do not have. Also finds old
                              copies of tools that dotkit has (npm, pipx, uv, cargo,
                              Homebrew, files) and .pkg installer packages. Opens the
                              plan in your editor, asks once, then installs the casks,
                              adds them to the lists, and uninstalls what you chose.
                              App data stays.

Recovery:
  history                     The package changes of the last apply.
  rollback                    Go back to the previous Nix generation.
                              Homebrew and mise do not roll back.
  clean [-n] [-y] [age]       Delete Nix generations older than age (default 14d),
                              old Homebrew downloads, and unused mise versions.

New Mac: see the dotkit README.

Examples:
  dotkit add lazygit          Nix, mise, or Homebrew: dotkit add selects one
  dotkit add npm:cowsay       A prefix selects the source (dotkit add --help)
  dotkit upgrade -n           Show what is outdated
  dotkit upgrade mise         Upgrade only the mise tools
  DOTKIT_DIR=~/.dotkit dotkit apply
                              Use a dotkit checkout instead of the locked release"""

COMMANDS = ("apply", "upgrade", "history", "rollback", "clean", "migrate", "tidy")
OLD_NAMES = {
    "switch": "'switch' is now 'apply'.",
    "build": "'build' is now 'apply -n'.",
    "diff": "'diff' is now 'apply -n'.",
    "update": "'update' is now 'upgrade nix' (or 'upgrade <input>').",
    "brew": "'brew' is now 'upgrade brew'.",
    "tools": "'tools' is now 'upgrade mise'.",
    "changes": "'changes' is now 'history'.",
    "where": "'where' is now 'info'.",
    "gc": "'gc' is now 'clean'.",
}


def plan_path(value: str) -> str:
    """The absolute path of --plan: the directory resolved, the file name kept (PARITY.md, O5 and B1)."""
    trimmed = value.rstrip("/") or "/"
    directory = os.path.dirname(trimmed) or "."
    if not os.path.isdir(directory):
        raise ui.usage_error(f"'{value}' is not in a directory that exists.")
    return f"{os.path.realpath(directory)}/{os.path.basename(trimmed)}"


SPEC = args.Spec(flags={"-n": "dry_run", "--dry-run": "dry_run", "-y": "yes", "--yes": "yes",
                        "-a": "all", "--all": "all"},
                 values={"--plan": ("plan", "--plan needs a file.")}, convert={"plan": plan_path})


def main(env: Env, argv: Sequence[str]) -> int:
    cmd, rest = (argv[0], list(argv[1:])) if argv else ("", [])
    # __names: the Bash dotkit asks it for the completion until step 7 of CLI-PLAN.md.
    if cmd in ("add", "remove", "info", "list", "__names"):
        from ..pkg import cli
        return cli.main(env, [cmd, *rest])
    # For the zsh completion (nix/completions/_dotkit). Reads files, runs no nix.
    if cmd == "__complete":
        what = rest[0] if rest else ""
        if what == "inputs":
            from . import upgrade
            for _, name in upgrade.flake_inputs(env):
                ui.out(name)
            return 0
        if what in ("tools-user", "tools-all"):
            from ..pkg import cli
            return cli.main(env, ["__names", what[len("tools-"):]])
        raise ui.usage_error("__complete takes inputs, tools-user, or tools-all.")
    if cmd in ("", "-h", "--help", "help"):
        ui.out(USAGE)
        return 0
    if cmd in OLD_NAMES:
        raise ui.usage_error(OLD_NAMES[cmd])
    if cmd not in COMMANDS:
        raise ui.usage_error(f"unknown command: {cmd}.")
    try:
        opts, words = args.parse(rest, SPEC)
    except args.Help:
        ui.out(USAGE)
        return 0
    if cmd in ("history", "rollback") and (opts["dry_run"] or opts["yes"]):
        raise ui.usage_error(f"{cmd} takes no options.")
    if cmd == "apply" and opts["yes"]:
        raise ui.usage_error("apply does not ask, so it takes no -y.")
    if opts["all"] and cmd != "migrate":
        raise ui.usage_error("--all is for migrate only.")
    if opts["plan"] and cmd != "tidy":
        raise ui.usage_error("--plan is for tidy only.")
    return run(env, cmd, opts, words)


def run(env: Env, cmd: str, opts: args.Options, words: List[str]) -> int:
    dry_run, yes = bool(opts["dry_run"]), bool(opts["yes"])
    if cmd == "apply":
        from . import apply
        return apply.apply(env, words, dry_run)
    if cmd == "upgrade":
        from . import upgrade
        return upgrade.upgrade(env, words, dry_run, yes)
    from . import recovery, wizard
    if cmd == "history":
        return recovery.history(env, words)
    if cmd == "rollback":
        return recovery.rollback(words)
    if cmd == "clean":
        return recovery.clean(env, words, dry_run, yes)
    if cmd == "migrate":
        return wizard.migrate(env, words, dry_run, yes, bool(opts["all"]))
    return wizard.tidy(env, words, dry_run, yes, str(opts["plan"]))
