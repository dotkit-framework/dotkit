"""The screens of screens.py as line prompts, for a terminal that curses cannot drive (and --plain).

One answer per screen: the path, the keymap, one per checklist page, one per
item of a choices page, and the last question. A checklist answer is Enter
(keep), numbers or ranges that toggle items (`1 3-5`), `a` (all), or `n` (none).
"""

from __future__ import annotations

import re
import sys

from ..model import Choices
from . import screens as screens_mod
from .screens import Quit, Screen


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


def toggles(answer: str, count: int) -> set[int] | None:
    """The 0-based rows that an answer like `1 3-5` names, or None when it is not one."""
    out: set[int] = set()
    for part in answer.replace(",", " ").split():
        m = re.fullmatch(r"([0-9]+)(?:-([0-9]+))?", part)
        if not m:
            return None
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if not 1 <= a <= b <= count:
            return None
        out.update(range(a - 1, b))
    return out


class Plain(screens_mod.Frontend):
    def __init__(self, dry_run: bool):
        self.dry_run = dry_run

    def show(self, screen: Screen, step: int, total: int) -> int:
        del step, total
        try:
            {"text": self.text, "radio": self.radio, "checklist": self.checklist, "choices": self.choices,
             "review": self.review}[screen.kind](screen)
        except (EOFError, KeyboardInterrupt):
            print()
            raise Quit() from None
        return 1

    def text(self, screen: Screen) -> None:
        while True:
            error = screen.set(screen.id, _ask(screen.title, screen.get(screen.id)))
            if not error:
                return
            print(error)

    def radio(self, screen: Screen) -> None:
        names = [r.id for r in screen.rows]
        current = next((n for n in names if screen.get(n)), names[0])
        while True:
            a = _ask(f"{screen.title}, {' or '.join(names)}", current)
            if a in names:
                screen.set(a, True)
                return

    def checklist(self, screen: Screen) -> None:
        rows = screen.rows
        print(f"\n{screen.title} -> {screen.dest}" if screen.dest else f"\n{screen.title}")
        print(f"  {screen.intro}")
        if screen.note:
            print(f"  {screen.note[:1].upper() + screen.note[1:]}.")
        self.list_rows(screen)
        while True:
            a = _ask("  Enter keeps these. Numbers or ranges toggle (1 3-5), a selects all, n none").lower()
            if not a:
                return
            if a in ("a", "n"):
                for r in rows:
                    screen.set(r.id, a == "a")
                break
            picked = toggles(a, len(rows))
            if picked is not None:
                for i in sorted(picked):
                    screen.set(rows[i].id, not screen.get(rows[i].id))
                break
            print(f"  Answer with numbers from 1 to {len(rows)}, ranges like 1-3, a, or n.")
        chosen = sum(1 for r in rows if screen.get(r.id))
        print(f"  {chosen} of {len(rows)} selected.")

    @staticmethod
    def list_rows(screen: Screen) -> None:
        width = len(str(len(screen.rows)))
        for i, r in enumerate(screen.rows, 1):
            print(f"  {i:>{width}} [{'x' if screen.get(r.id) else ' '}] {r.label}")

    def choices(self, screen: Screen) -> None:
        print(f"\n{screen.title}. {screen.intro}")
        for r in screen.rows:
            note = screen.detail(r.id)
            print(f"  {r.label}" + (f" ({note})" if note else ""))
            for o in r.options:
                print(f"    {o}: {r.option_text[o]}")
            while True:
                if not screen.set(r.id, _ask("  Choice", screen.get(r.id))):
                    break

    def review(self, screen: Screen) -> None:
        print("\nThe plan:")
        for line in screen.lines() if screen.lines else []:
            print("  " + line)
        print()
        if not _yes("Print the plan" if self.dry_run else "Create the repository", True):
            raise Quit()


def run(planner, c: Choices, home: str, dry_run: bool = False) -> bool:
    """Asks the questions. False when the person says no at the end, or the input ends."""
    print("dotkit setup. Nothing on this Mac changes before the last question.\n")
    return screens_mod.run(Plain(dry_run), screens_mod.build(planner, c, home))
