"""The setup wizard: a few curses screens that edit the choices.

Keys: arrows move, space toggles, enter goes on, b goes back, q quits.
The screens only change the Choices object; plan.py has the rules. Nothing
on the Mac changes until the caller writes the plan after the last screen.
"""

from __future__ import annotations

import curses
import os
import textwrap

from .plan import OPTION_TEXT, Choices, Planner, expand, repo_error, tilde

MIN_H, MIN_W = 16, 60


class Quit(Exception):
    pass


def _enter(key) -> bool:
    return key in ("\n", "\r", curses.KEY_ENTER)


def _up(key) -> bool:
    return key in (curses.KEY_UP, "k")


def _down(key) -> bool:
    return key in (curses.KEY_DOWN, "j")


def _back(key) -> bool:
    return key in ("b", "B", curses.KEY_LEFT) or key == "\x1b"


class UI:
    def __init__(self, scr, planner: Planner, c: Choices, dry_run: bool):
        self.scr = scr
        self.p = planner
        self.c = c
        self.dry_run = dry_run
        self.home = planner.scan.home
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

    # --- Screens. Each returns 1 (next) or -1 (back).

    def repo(self, step: int, total: int) -> int:
        value = tilde(self.c.repo, self.home)
        error = ""
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            while True:
                size = self.frame(step, total, "Where the dotfiles repository goes",
                                  "type a path   enter next   esc quit")
                if size:
                    y = self.para(4, "Setup creates a new Git repository with your settings, lists, and a "
                                     "report of what needs your attention. Nothing on this Mac changes before "
                                     "the last screen.")
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
                    path = expand(value or "~/.dotfiles", self.home)
                    error = repo_error(path, self.home) or ""
                    if not error:
                        self.c.repo = path
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

    def keymap(self, step: int, total: int) -> int:
        options = [("emacs", "Ctrl+A, Ctrl+E, Ctrl+R, as in most terminals"),
                   ("vi", "Esc for command mode, as in vim")]
        pos = 0 if self.c.keymap == "emacs" else 1
        while True:
            size = self.frame(step, total, "The zsh keymap", "↑↓ select   enter next   b back   q quit")
            if size:
                y = self.para(4, "How the command line edits text. nix/shell.nix has it, and you can change "
                                 "it later.")
                for i, (name, desc) in enumerate(options):
                    mark = "(•)" if i == pos else "( )"
                    attr = curses.A_REVERSE if i == pos else 0
                    self.put(y + 1 + i, 4, f"{mark} {name:6} {desc}", attr)
                self.scr.refresh()
            k = self.key()
            if k is None or not size:
                continue
            if _up(k):
                pos = 0
            elif _down(k):
                pos = 1
            elif k == " ":
                pos = 1 - pos
            elif _enter(k):
                self.c.keymap = options[pos][0]
                return 1
            elif _back(k):
                self.c.keymap = options[pos][0]
                return -1

    def toggles(self, step: int, total: int, title: str, intro: str, rows, get, flip, detail) -> int:
        """A list of rows with a check box each. rows: labels; get(i) -> bool; flip(i); detail(i) -> text."""
        pos = top = 0
        while True:
            size = self.frame(step, total, title, "↑↓ move   space select   enter next   b back   q quit")
            if size:
                h, w = size
                y = self.para(4, intro, limit=3)
                y += 1
                view = max(3, min(len(rows), (h - y - 2) // 2))
                top = max(0, min(top, pos, len(rows) - view))
                if pos >= top + view:
                    top = pos - view + 1
                for i in range(top, min(len(rows), top + view)):
                    mark = "[x]" if get(i) else "[ ]"
                    attr = curses.A_REVERSE if i == pos else 0
                    self.put(y + i - top, 4, f"{mark} {rows[i]}", attr)
                if top > 0:
                    self.put(y, 2, "↑", self.dim)
                if top + view < len(rows):
                    self.put(y + view - 1, 2, "↓", self.dim)
                yy = y + view + 1
                lines = textwrap.wrap(detail(pos), w - 8) if rows else []
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
                flip(pos)
            elif _enter(k):
                return 1
            elif _back(k):
                return -1

    def groups(self, step: int, total: int) -> int:
        groups = self.p.groups()
        rows = [f"{g.label} ({g.size}) -> {g.dest}" for g in groups]

        def detail(i: int) -> str:
            g = groups[i]
            names = ", ".join(g.items[:40]) + (", …" if len(g.items) > 40 else "")
            return (g.note + ". " if g.note else "") + names

        def flip(i: int) -> None:
            self.c.groups[groups[i].key] = not self.c.groups.get(groups[i].key)

        intro = ("What this Mac has that the repository can list. Homebrew packages stay installed; tools of "
                 "npm, pipx, and others move to mise. Everything else goes to MIGRATION.md.")
        if not groups:
            intro = "The scan found no packages or shell lines to carry over."
        return self.toggles(step, total, "What to carry over", intro, rows,
                            lambda i: self.c.groups.get(groups[i].key, False), flip, detail)

    def files(self, step: int, total: int) -> int:
        items = self.p.file_items()
        pos = 0
        while True:
            size = self.frame(step, total, "Your config files",
                              "↑↓ move   space or ←→ change   enter next   b back   q quit")
            if size:
                h, w = size
                y = self.para(4, "dotkit has its own file for each of these. Choose which one each tool reads.",
                              limit=2) + 1
                width = min(40, max(len(tilde(f"{self.home}/{i.target}", self.home)) for i in items) + 2)
                view = max(3, min(len(items), h - y - 6))
                top = 0 if pos < view else pos - view + 1
                for i in range(top, min(len(items), top + view)):
                    item = items[i]
                    choice = self.c.files.get(item.target, item.default)
                    name = tilde(f"{self.home}/{item.target}", self.home)
                    attr = curses.A_REVERSE if i == pos else 0
                    self.put(y + i - top, 4, f"{name:<{width}} < {OPTION_TEXT[choice]} >", attr)
                item = items[pos]
                yy = y + view + 1
                if item.note:
                    yy = self.para(yy, item.note, self.dim, x=6)
                cur = self.c.files.get(item.target, item.default)
                choices = "   ".join(f"[{o}]" if o == cur else o for o in item.options)
                self.para(yy, "Choices: " + choices, self.dim, limit=max(1, h - yy - 2), x=6)
                self.scr.refresh()
            k = self.key()
            if k is None or not size:
                continue
            item = items[pos]
            options = item.options
            cur = self.c.files.get(item.target, item.default)
            if _up(k):
                pos = max(0, pos - 1)
            elif _down(k):
                pos = min(len(items) - 1, pos + 1)
            elif k in (" ", curses.KEY_RIGHT, "l"):
                self.c.files[item.target] = options[(options.index(cur) + 1) % len(options)]
            elif k == curses.KEY_LEFT or k == "h":
                self.c.files[item.target] = options[(options.index(cur) - 1) % len(options)]
            elif _enter(k):
                return 1
            elif k in ("b", "B", "\x1b"):
                return -1

    def other(self, step: int, total: int) -> int:
        rels = self.p.adoptable()

        def flip(i: int) -> None:
            self.c.adopt ^= {rels[i]}

        def detail(i: int) -> str:
            o = next(o for o in self.p.scan.other_config if o.rel == rels[i])
            what = (f"a directory with {o.files} file" + ("s" if o.files != 1 else "")) if o.is_dir else "a file"
            return f"~/{o.rel}: {what}. It goes to config/ and is linked back."

        return self.toggles(step, total, "Other config to keep in the repository",
                            "These are not part of dotkit. By default they stay in place and keep working. "
                            "Select the ones to copy into the repository.",
                            [f"~/{r}" for r in rels], lambda i: rels[i] in self.c.adopt, flip, detail)

    def review(self, step: int, total: int) -> int:
        top = 0
        action = "print the plan" if self.dry_run else "create the repository"
        plan = self.p.build(self.c)
        while True:
            size = self.frame(step, total, "The plan", f"enter {action}   ↑↓ scroll   b back   q quit")
            if size:
                h, w = size
                lines: list[str] = []
                for line in plan.summary:
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


def screens(p: Planner) -> list[str]:
    out = ["repo", "keymap", "groups"]
    if p.file_items():
        out.append("files")
    if p.adoptable():
        out.append("other")
    out.append("review")
    return out


def run(planner: Planner, c: Choices, dry_run: bool = False) -> bool:
    """Shows the wizard. False when the person quits."""

    def main(scr) -> bool:
        ui = UI(scr, planner, c, dry_run)
        names = screens(planner)
        i = 0
        try:
            while True:
                move = getattr(ui, names[i])(i + 1, len(names))
                if move > 0 and i == len(names) - 1:
                    return True
                i = max(0, min(len(names) - 1, i + move))
        except Quit:
            return False

    os.environ.setdefault("ESCDELAY", "25")
    return curses.wrapper(main)


def available() -> bool:
    """True when curses can drive this terminal. Tries xterm-256color for an unknown TERM."""
    import sys

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
