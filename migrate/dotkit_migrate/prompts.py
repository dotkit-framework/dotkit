"""Plain prompts with the same questions as the wizard, for a terminal that curses cannot drive."""

from __future__ import annotations

import sys

from .context import expand, repo_error, tilde
from .plan import OPTION_TEXT, Choices, Planner


def _ask(question: str, default: str = "") -> str:
    sys.stdout.write(question + (f" [{default}]" if default else "") + ": ")
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.strip() or default


def _yes(question: str, default: bool) -> bool:
    while True:
        a = _ask(question + (" (Y/n)" if default else " (y/N)")).lower()
        if not a:
            return default
        if a in ("y", "yes", "n", "no"):
            return a.startswith("y")


def run(p: Planner, c: Choices, dry_run: bool = False) -> bool:
    """Asks the questions. False when the person says no at the end."""
    home = p.scan.home
    try:
        print("dotkit setup. Nothing on this Mac changes before the last question.\n")
        while True:
            path = expand(_ask("Where the dotfiles repository goes", tilde(c.repo, home)), home)
            error = repo_error(path, home)
            if not error:
                c.repo = path
                break
            print(error)
        while True:
            k = _ask("zsh keymap, emacs or vi", c.keymap)
            if k in ("emacs", "vi"):
                c.keymap = k
                break
        groups = p.groups()
        if groups:
            print("\nWhat to carry over:")
        for g in groups:
            names = ", ".join(g.items[:12]) + (", …" if len(g.items) > 12 else "")
            print(f"  {g.label} ({g.size}) -> {g.dest}: {names}")
            if g.note:
                print(f"  {g.note}")
            c.groups[g.key] = _yes(f"  Carry the {g.label.lower()}?", c.groups.get(g.key, g.default))
        items = p.file_items()
        if items:
            print("\nYour config files. dotkit has its own file for each of these.")
        for item in items:
            print(f"  ~/{item.target}" + (f" ({item.note})" if item.note else ""))
            for o in item.options:
                print(f"    {o}: {OPTION_TEXT[o]}")
            while True:
                a = _ask("  Choice", c.files.get(item.target, item.default))
                if a in item.options:
                    c.files[item.target] = a
                    break
        rels = p.adoptable()
        if rels:
            print("\nOther config. It stays in place unless you name it here.")
            for r in rels:
                print(f"  ~/{r}")
            names = _ask("  Copy into the repository (names separated by spaces, or empty)")
            for n in names.split():
                n = n.removeprefix("~/")
                if n in rels:
                    c.adopt.add(n)
                elif ".config/" + n in rels:
                    c.adopt.add(".config/" + n)
        plan = p.build(c)
        print("\nThe plan:")
        for line in plan.summary:
            print("  " + line)
        print()
        return _yes("Print the plan" if dry_run else "Create the repository", True)
    except (EOFError, KeyboardInterrupt):
        print()
        return False
