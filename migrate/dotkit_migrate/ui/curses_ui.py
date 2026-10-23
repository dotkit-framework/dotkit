"""The full-screen wizard: curses widgets for the screens of screens.py.

Keys: arrows move, space selects, enter goes on, b goes back, q quits. The
widgets only change the Choices through the screens.
"""

from __future__ import annotations

import curses
import os
import sys
import textwrap

from ..model import Choices
from . import screens as screens_mod
from .screens import Quit, Screen

MIN_H, MIN_W = 16, 60


def _enter(key) -> bool:
    return key in ("\n", "\r", curses.KEY_ENTER)


def _up(key) -> bool:
    return key in (curses.KEY_UP, "k")


def _down(key) -> bool:
    return key in (curses.KEY_DOWN, "j")


def _back(key) -> bool:
    return key in ("b", "B", curses.KEY_LEFT) or key == "\x1b"


class UI(screens_mod.Frontend):
    def __init__(self, scr, dry_run: bool):
        self.scr = scr
        self.dry_run = dry_run
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        scr.keypad(True)
        self.accent = self.dim = self.warn = 0
        if curses.has_colors():
            try:
                curses.use_default_colors()
                bg = -1
            except curses.error:
                bg = curses.COLOR_BLACK
            try:
                curses.init_pair(1, curses.COLOR_CYAN, bg)
                curses.init_pair(2, curses.COLOR_YELLOW, bg)
                self.accent, self.warn = curses.color_pair(1), curses.color_pair(2)
            except curses.error:
                pass
        self.dim = curses.A_DIM

    def show(self, screen: Screen, step: int, total: int) -> int:
        widget = {"text": self.text_input, "radio": self.radio, "checklist": self.checklist,
                  "choices": self.choice_list, "review": self.pager}[screen.kind]
        return widget(screen, step, total)

    # --- Input and drawing

    def key(self, text_mode: bool = False):
        try:
            k = self.scr.get_wch()
        except KeyboardInterrupt:
            raise Quit() from None
        except curses.error:
            return None
        if k == curses.KEY_RESIZE:
            return None
        if k == "\x1b":
            k = self._escape()
        if k == "\x03":
            raise Quit()
        if not text_mode and k in ("q", "Q"):
            raise Quit()
        return k

    def _escape(self):
        """An arrow key that curses did not translate (a terminal outside keypad mode), or Esc."""
        self.scr.nodelay(True)
        try:
            seq = ""
            while len(seq) < 2:
                try:
                    c = self.scr.get_wch()
                except curses.error:
                    break
                seq += c if isinstance(c, str) else ""
        finally:
            self.scr.nodelay(False)
        arrows = {"A": curses.KEY_UP, "B": curses.KEY_DOWN, "C": curses.KEY_RIGHT, "D": curses.KEY_LEFT}
        if len(seq) == 2 and seq[0] in "[O" and seq[1] in arrows:
            return arrows[seq[1]]
        for c in reversed(seq):
            curses.unget_wch(c)
        return "\x1b"

    def put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and 0 <= x < w - 1:
            try:
                self.scr.addnstr(y, x, text, w - x - 1, attr)
            except curses.error:
                pass

    def para(self, y: int, text: str, attr: int = 0, limit: int | None = None, x: int = 2) -> int:
        _, w = self.scr.getmaxyx()
        for line in (textwrap.wrap(text, max(20, w - x - 2)) or [""])[:limit]:
            self.put(y, x, line, attr)
            y += 1
        return y

    def frame(self, step: int, total: int, title: str, keys: str):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if h < MIN_H or w < MIN_W:
            self.put(0, 0, f"Make the window larger: {MIN_W}x{MIN_H} or more.")
            self.put(1, 0, "q quits.")
            self.scr.refresh()
            return None
        self.put(0, 2, "dotkit setup", curses.A_BOLD | self.accent)
        label = f"{step}/{total}"
        self.put(0, w - len(label) - 2, label, self.dim)
        self.put(2, 2, title, curses.A_BOLD)
        self.put(h - 1, 2, keys, self.dim)
        return h, w

    def page_head(self, screen: Screen, size, extra: str = "") -> int:
        """The intro, the page note, and where the items go. Returns the next free line."""
        y = self.para(4, screen.intro, limit=3)
        if screen.note:
            y = self.para(y, screen.note[:1].upper() + screen.note[1:] + ".", self.dim, limit=1)
        if screen.dest or extra:
            self.put(y, 2, "   ".join(x for x in (f"-> {screen.dest}" if screen.dest else "", extra) if x),
                     self.dim)
            y += 1
        return y + 1

    # --- Widgets. Each returns 1 (next) or -1 (back).

    def text_input(self, screen: Screen, step: int, total: int) -> int:
        value = screen.get(screen.id)
        error = ""
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            while True:
                size = self.frame(step, total, screen.title, "type a path   enter next   esc quit")
                if size:
                    y = self.para(4, screen.intro)
                    self.put(y + 1, 4, "Path: ", curses.A_BOLD)
                    self.put(y + 1, 10, value + " ", curses.A_UNDERLINE)
                    if error:
                        self.para(y + 3, error, self.warn, x=4)
                    try:
                        self.scr.move(y + 1, min(10 + len(value), size[1] - 2))
                    except curses.error:
                        pass
                    self.scr.refresh()
                k = self.key(text_mode=True)
                if not size and k in ("q", "Q"):
                    raise Quit()
                if k is None or not size:
                    continue
                if k == "\x1b":
                    raise Quit()
                if _enter(k):
                    error = screen.set(screen.id, value)
                    if not error:
                        return 1
                elif k in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    value, error = value[:-1], ""
                elif k == "\x15":
                    value, error = "", ""
                elif isinstance(k, str) and k.isprintable() and len(value) < 200:
                    value, error = value + k, ""
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

    def radio(self, screen: Screen, step: int, total: int) -> int:
        rows = screen.rows
        pos = next((i for i, r in enumerate(rows) if screen.get(r.id)), 0)
        while True:
            size = self.frame(step, total, screen.title, "↑↓ select   enter next   b back   q quit")
            if size:
                y = self.para(4, screen.intro)
                for i, r in enumerate(rows):
                    mark = "(•)" if i == pos else "( )"
                    attr = curses.A_REVERSE if i == pos else 0
                    self.put(y + 1 + i, 4, f"{mark} {r.id:6} {r.label}", attr)
                self.scr.refresh()
            k = self.key()
            if k is None or not size:
                continue
            if _up(k):
                pos = max(0, pos - 1)
            elif _down(k):
                pos = min(len(rows) - 1, pos + 1)
            elif k == " ":
                pos = (pos + 1) % len(rows)
            elif _enter(k) or _back(k):
                screen.set(rows[pos].id, True)
                return 1 if _enter(k) else -1

    def checklist(self, screen: Screen, step: int, total: int) -> int:
        rows = screen.rows
        pos = top = 0
        while True:
            size = self.frame(step, total, screen.title,
                              "↑↓ move   space select   a all or none   enter next   b back   q quit")
            if size:
                h, w = size
                chosen = sum(1 for r in rows if screen.get(r.id))
                y = self.page_head(screen, size, f"{chosen} of {len(rows)} selected")
                view = max(3, min(len(rows), h - y - 4))
                top = max(0, min(top, pos, len(rows) - view))
                if pos >= top + view:
                    top = pos - view + 1
                for i in range(top, min(len(rows), top + view)):
                    mark = "[x]" if screen.get(rows[i].id) else "[ ]"
                    attr = curses.A_REVERSE if i == pos else 0
                    self.put(y + i - top, 4, f"{mark} {rows[i].label}", attr)
                if top > 0:
                    self.put(y, 2, "↑", self.dim)
                if top + view < len(rows):
                    self.put(y + view - 1, 2, "↓", self.dim)
                yy = y + view + 1
                lines = textwrap.wrap(screen.detail(rows[pos].id), w - 8) if rows else []
                for line in lines[: max(1, h - yy - 2)]:
                    self.put(yy, 6, line, self.dim)
                    yy += 1
                self.scr.refresh()
            k = self.key()
            if k is None or not size:
                continue
            if _up(k):
                pos = max(0, pos - 1)
            elif _down(k):
                pos = min(len(rows) - 1, pos + 1)
            elif k == " " and rows:
                screen.set(rows[pos].id, not screen.get(rows[pos].id))
            elif k in ("a", "A") and rows:
                screens_mod.toggle_all(screen)
            elif _enter(k):
                return 1
            elif _back(k):
                return -1

    def choice_list(self, screen: Screen, step: int, total: int) -> int:
        rows = screen.rows
        pos = 0
        while True:
            size = self.frame(step, total, screen.title,
                              "↑↓ move   space or ←→ change   enter next   b back   q quit")
            if size:
                h, w = size
                y = self.para(4, screen.intro, limit=2) + 1
                width = min(40, max(len(r.label) for r in rows) + 2)
                view = max(3, min(len(rows), h - y - 6))
                top = 0 if pos < view else pos - view + 1
                for i in range(top, min(len(rows), top + view)):
                    r = rows[i]
                    attr = curses.A_REVERSE if i == pos else 0
                    self.put(y + i - top, 4, f"{r.label:<{width}} < {r.option_text[screen.get(r.id)]} >", attr)
                r = rows[pos]
                yy = y + view + 1
                note = screen.detail(r.id)
                if note:
                    yy = self.para(yy, note, self.dim, x=6)
                cur = screen.get(r.id)
                choices = "   ".join(f"[{o}]" if o == cur else o for o in r.options)
                self.para(yy, "Choices: " + choices, self.dim, limit=max(1, h - yy - 2), x=6)
                self.scr.refresh()
            k = self.key()
            if k is None or not size:
                continue
            r = rows[pos]
            options = r.options
            cur = screen.get(r.id)
            if _up(k):
                pos = max(0, pos - 1)
            elif _down(k):
                pos = min(len(rows) - 1, pos + 1)
            elif k in (" ", curses.KEY_RIGHT, "l"):
                screen.set(r.id, options[(options.index(cur) + 1) % len(options)])
            elif k == curses.KEY_LEFT or k == "h":
                screen.set(r.id, options[(options.index(cur) - 1) % len(options)])
            elif _enter(k):
                return 1
            elif k in ("b", "B", "\x1b"):
                return -1

    def pager(self, screen: Screen, step: int, total: int) -> int:
        top = 0
        action = "print the plan" if self.dry_run else "create the repository"
        text = screen.lines() if screen.lines else []
        while True:
            size = self.frame(step, total, screen.title, f"enter {action}   ↑↓ scroll   b back   q quit")
            if size:
                h, w = size
                lines: list[str] = []
                for line in text:
                    lines += textwrap.wrap(line, w - 6, subsequent_indent="    ") or [""]
                view = max(1, h - 6)
                top = max(0, min(top, len(lines) - view))
                for i, line in enumerate(lines[top: top + view]):
                    self.put(4 + i, 2, line)
                self.scr.refresh()
            k = self.key()
            if k is None or not size:
                continue
            if _up(k):
                top = max(0, top - 1)
            elif _down(k):
                top += 1
            elif _enter(k):
                return 1
            elif _back(k):
                return -1


def run(planner, c: Choices, home: str, dry_run: bool = False) -> bool:
    """Shows the wizard. False when the person quits."""
    screens = screens_mod.build(planner, c, home)

    def main(scr) -> bool:
        return screens_mod.run(UI(scr, dry_run), screens)

    os.environ.setdefault("ESCDELAY", "25")
    return curses.wrapper(main)


def available() -> bool:
    """True when curses can drive this terminal. Tries xterm-256color for an unknown TERM."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    term = os.environ.get("TERM", "")
    if term in ("", "dumb", "unknown"):
        return False
    try:
        curses.setupterm(term, sys.stdout.fileno())
        return True
    except curses.error:
        pass
    # Ghostty and others set TERMINFO; without it, their TERM can be unknown here.
    try:
        curses.setupterm("xterm-256color", sys.stdout.fileno())
        os.environ["TERM"] = "xterm-256color"
        return True
    except curses.error:
        return False
