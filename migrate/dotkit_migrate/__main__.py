"""dotkit-migrate: the migration wizard of dotkit setup, and `dotkit migrate`."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import adopt as adopt_mod
from . import base as base_mod
from . import context
from . import plan as plan_mod
from . import scan, writer
from .ui import curses_ui, plain

# make_context(home, base) -> the Context that the scan reads. Tests give a fake Mac.
MakeContext = Callable[[Path, base_mod.Base], context.Context]


def check_syntax(shell: str, text: str) -> bool:
    """True when zsh -n or bash -n accepts the text, or when the shell is missing."""
    exe = {"zsh": "/bin/zsh", "bash": "/bin/bash"}[shell]
    if not os.access(exe, os.X_OK):
        return True
    try:
        r = subprocess.run([exe, "-n", "-c", text], capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return True
    return r.returncode == 0


def info(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cmd_setup(a: argparse.Namespace, make_context: MakeContext) -> int:
    home = os.environ.get("HOME") or str(Path.home())
    b = base_mod.Base(Path(a.base)) if a.base else base_mod.Base()
    repo = context.expand(a.repo or os.environ.get("DOTFILES") or "~/.dotfiles", home)
    # The full-screen wizard needs a terminal on both sides; the plain
    # prompts only need one for the answers.
    interactive = not a.yes and sys.stdin.isatty()
    if not a.yes and not a.dry_run and not interactive:
        info("dotkit setup: the migration asks questions and needs a terminal. Use --yes for the defaults.")
        return 2
    if not interactive:
        error = context.repo_error(repo, home)
        if error:
            info(f"dotkit setup: {error}")
            return 2
    info("Scanning this Mac. This reads files and runs brew and mas; it changes nothing.")
    s = scan.Scanner(make_context(Path(home), b)).run()
    p = plan_mod.Planner(s, b, check_syntax)
    c = p.defaults(repo)
    blockers = p.build(c).blockers
    if blockers and not a.dry_run:
        info("dotkit setup: " + "\n".join(blockers))
        return 2
    if interactive:
        frontend = curses_ui if (not a.plain and curses_ui.available()) else plain
        ok = frontend.run(p, c, home, a.dry_run)
        if not ok:
            info("Stopped. Nothing changed.")
            return 1
    result = p.build(c)
    if result.blockers and not a.dry_run:
        info("dotkit setup: " + "\n".join(result.blockers))
        return 2
    if a.dry_run:
        print("\n".join(result.summary))
        print()
        print("--- MIGRATION.md " + "-" * 50)
        print(result.report, end="")
        info("Dry run: nothing was written.")
        return 0
    try:
        writer.write_new(result, b.template)
    except writer.SecretInRepository as e:
        info(f"dotkit setup: {e}. Nothing was written.")
        return 2
    if a.result:
        Path(a.result).write_text(f"{result.repo}\n", encoding="utf-8")
    info(f"Created {context.tilde(result.repo, home)}. MIGRATION.md there lists what needs your attention.")
    return 0


def cmd_adopt(a: argparse.Namespace, make_context: MakeContext) -> int:
    home = os.environ.get("HOME") or str(Path.home())
    b = base_mod.Base(Path(a.base)) if a.base else base_mod.Base()
    repo = Path(a.repo)
    declared = json.loads(a.declared) if a.declared else {}
    info("Scanning this Mac. It changes nothing.")
    s = scan.Scanner(make_context(Path(home), b)).run()
    m = adopt_mod.missing(s, repo, b, declared, a.all)
    if m.empty:
        print("The lists have everything that this Mac has.", flush=True)
        return 0
    print("Installed on this Mac, and not in the lists:")
    print("\n".join(adopt_mod.describe(m)), flush=True)
    if not (m.formulae or m.casks or m.taps or m.mas or m.mise):
        return 0
    if a.dry_run:
        info("No change. Run dotkit migrate without -n to add them.")
        return 0
    if not a.yes:
        if not sys.stdin.isatty():
            info("dotkit: confirmation needs a terminal. Use -y to continue without one.")
            return 2
        sys.stderr.write("Add them to the lists? [y/N] ")
        sys.stderr.flush()
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            info("dotkit: nothing changed.")
            return 1
    try:
        adopt_mod.apply(m, repo)
    except writer.SecretInRepository as e:
        info(f"dotkit migrate: {e}. Nothing was written.")
        return 2
    info("Added. They are installed already; dotkit apply records them, and mise install gets the mise copies.")
    return 0


def main(argv: list[str] | None = None, make_context: MakeContext | None = None) -> int:
    # Names and files are UTF-8; a terminal with another locale gets ? for
    # what it cannot show, not an error.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="dotkit-migrate", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="create a dotfiles repository from this Mac")
    s.add_argument("--repo", help="the new repository (default: $DOTFILES or ~/.dotfiles)")
    s.add_argument("-y", "--yes", action="store_true", help="take the defaults, without questions")
    s.add_argument("-n", "--dry-run", action="store_true", help="print the plan and the report, write nothing")
    s.add_argument("--plain", action="store_true", help="plain prompts instead of the full-screen wizard")
    s.add_argument("--result", help="write the path of the new repository to this file")
    s.add_argument("--base", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_setup)
    d = sub.add_parser("adopt", help="add what was installed by hand to the lists of a repository")
    d.add_argument("--repo", required=True)
    d.add_argument("-y", "--yes", action="store_true")
    d.add_argument("-n", "--dry-run", action="store_true")
    d.add_argument("--declared", help="JSON with the taps and masApps of the configuration")
    d.add_argument("--all", action="store_true", help="also cargo and Go tools, which mise builds from source")
    d.add_argument("--base", help=argparse.SUPPRESS)
    d.set_defaults(func=cmd_adopt)
    a = ap.parse_args(argv)
    return a.func(a, make_context or context.this_mac)


if __name__ == "__main__":
    sys.exit(main())
