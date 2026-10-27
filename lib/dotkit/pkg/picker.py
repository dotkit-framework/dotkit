"""The source picker of `dotkit add` (CLI-PLAN.md, section 7; PARITY.md, DV2 and rows Pk).

Picker is the state: the rows, the cursor, and what a key does. It runs no
I/O, so its tests are a table. ask() shows it on stderr and reads keys from
stdin: an inline list that it draws again in place, in cbreak mode, so that
Ctrl-C still stops dotkit. With TERM=dumb or a narrow terminal, a numbered
prompt replaces the list.
"""

from __future__ import annotations

import os
import select
import termios
import tty
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..core import ui

PICK, SKIP, STOP = "pick", "skip", "stop"
Result = Tuple[str, Optional[int]]
UP, DOWN, ENTER = "up", "down", "enter"
FOOTER = "↑/↓ select · enter add · s skip · q stop"
REGISTRY_NOTE = "anyone can publish here; check the package"
# The list needs this width; a narrower terminal gets the numbered prompt.
MIN_COLUMNS = 60


@dataclass
class Row:
    spec: str
    version: str
    description: str
    note: str = ""


class Picker:
    def __init__(self, title: str, rows: List[Row], cursor: Optional[int]):
        self.title = title
        self.rows = rows
        # None: no row yet. The cursor starts there when the rules select no source.
        self.cursor = cursor

    def key(self, k: str) -> Optional[Result]:
        """Applies a key. Returns the result when the key ends the picker, else None."""
        if k == UP:
            self.cursor = len(self.rows) - 1 if self.cursor is None else max(self.cursor - 1, 0)
        elif k == DOWN:
            self.cursor = 0 if self.cursor is None else min(self.cursor + 1, len(self.rows) - 1)
        elif k == ENTER:
            if self.cursor is not None:
                return PICK, self.cursor
        elif k in ("s", "S"):
            return SKIP, None
        elif k in ("q", "Q"):
            return STOP, None
        return None

    def lines(self, columns: int) -> List[str]:
        """The text of the list, each line cut to the width of the terminal so that no line wraps."""
        out = [self.title]
        for i, r in enumerate(self.rows):
            mark = ">" if i == self.cursor else " "
            line = f"{mark} {ui.pad(r.spec, 30)} {ui.pad(r.version or '?', 14)} {r.description}"
            if r.note:
                line += f" ({r.note})"
            out.append(line)
        out.append(FOOTER)
        return [_cut(line, columns - 1) for line in out]


def _cut(line: str, width: int) -> str:
    return line if len(line) <= width else line[:max(width - 1, 0)] + "…"


def _read_key(fd: int) -> Optional[str]:
    """One key from the terminal; None at the end of the input, also Ctrl-D (cbreak mode reads it as a byte)."""
    b = os.read(fd, 1)
    if not b or b == b"\x04":
        return None
    if b in (b"\r", b"\n"):
        return ENTER
    if b == b"\x1b":
        seq = b""
        while len(seq) < 2 and select.select([fd], [], [], 0.05)[0]:
            seq += os.read(fd, 1)
        return {b"[A": UP, b"OA": UP, b"[B": DOWN, b"OB": DOWN}.get(seq, "")
    if b in (b"k", b"K"):
        return UP
    if b in (b"j", b"J"):
        return DOWN
    return b.decode("ascii", "replace")


def ask(picker: Picker) -> Result:
    """Shows the picker and returns what the user selected. STOP at the end of the input."""
    columns = ui.terminal_columns(2) or 0
    if os.environ.get("TERM", "") in ("", "dumb") or columns < MIN_COLUMNS:
        return _ask_numbered(picker)
    fd = 0
    old = termios.tcgetattr(fd)
    drawn = 0
    try:
        # cbreak: keys one by one, without echo; Ctrl-C still stops dotkit. TCSANOW, not the TCSAFLUSH of
        # tty.setcbreak: that drops the keys typed before the list shows.
        mode = termios.tcgetattr(fd)
        mode[tty.LFLAG] &= ~(termios.ECHO | termios.ICANON)
        mode[tty.CC][termios.VMIN] = 1
        mode[tty.CC][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, mode)
        ui.raw_err("\x1b[?25l")
        while True:
            lines = picker.lines(columns)
            ui.raw_err((f"\x1b[{drawn}A\r\x1b[J" if drawn else "") + "\n".join(lines) + "\n")
            drawn = len(lines)
            k = _read_key(fd)
            result = (STOP, None) if k is None else picker.key(k)
            if result is not None:
                return result
    finally:
        # The list goes away; the plan of add takes its place.
        ui.raw_err((f"\x1b[{drawn}A\r\x1b[J" if drawn else "") + "\x1b[?25h")
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _ask_numbered(picker: Picker) -> Result:
    ui.note(picker.title)
    for i, r in enumerate(picker.rows, 1):
        note = f" ({r.note})" if r.note else ""
        ui.note(f"  {i}) {ui.pad(r.spec, 30)} {ui.pad(r.version or '?', 14)} {r.description}{note}")
    default = "" if picker.cursor is None else f", Enter for {picker.cursor + 1}"
    while True:
        ui.raw_err(f"Select 1-{len(picker.rows)}{default}, s to skip, q to stop: ")
        line = ui.read_line()
        if line is None:
            return STOP, None
        answer = line.strip(" \t")
        if answer == "" and picker.cursor is not None:
            return PICK, picker.cursor
        if answer.isdigit() and 1 <= int(answer) <= len(picker.rows):
            return PICK, int(answer) - 1
        if answer in ("s", "S", "q", "Q"):
            return picker.key(answer)
