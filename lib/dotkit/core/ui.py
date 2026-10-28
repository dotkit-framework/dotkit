"""Messages, the question, and the width of the terminal.

All text goes out as UTF-8 bytes and is flushed at once, so that it stays in
order with the output of the commands that dotkit runs. Errors are
exceptions: a command raises die(...) or usage_error(...), and the entry point
prints the text and exits with the code (BEHAVIOR.md, the introduction).
"""

from __future__ import annotations

import os
import sys
from typing import Optional


class Exit(Exception):
    """Stop the command with an exit code, and print text (one line or more) on stderr first."""

    def __init__(self, code: int, text: str = ""):
        super().__init__(text)
        self.code = code
        self.text = text


def die(msg: str) -> Exit:
    return Exit(1, f"dotkit: {msg}")


def usage_error(msg: str, hint: str = "Run dotkit --help.") -> Exit:
    return Exit(2, f"dotkit: {msg} {hint}")


def _write(stream, text: str) -> None:
    stream.flush()
    stream.buffer.write(text.encode("utf-8", "surrogateescape"))
    stream.buffer.flush()


def out(text: str = "") -> None:
    """A line on stdout."""
    _write(sys.stdout, text + "\n")


def note(text: str) -> None:
    """A line on stderr, without the dotkit: prefix: "Checking Nix...", "No change. ..."."""
    _write(sys.stderr, text + "\n")


def warn(msg: str) -> None:
    """dotkit: MSG on stderr; the command goes on."""
    note(f"dotkit: {msg}")


def raw_err(text: str) -> None:
    """Text on stderr as it is, for the output of a command that dotkit captured."""
    _write(sys.stderr, text)


def flush() -> None:
    """Flush both streams, before a command writes to them."""
    sys.stdout.flush()
    sys.stderr.flush()


def colors(stream) -> bool:
    """Colors for a stream: a terminal, without NO_COLOR and TERM=dumb."""
    return stream.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"


def one_line(value: object) -> str:
    """A value of JSON as one field of a row: a string or a number as text, on one line; anything else empty."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return str(value).replace("\t", " ").replace("\n", " ").replace("\r", " ")


def width(text: str) -> int:
    """The columns that text takes in a terminal: 2 for a wide character such as 中 or 🙂 (wcwidth)."""
    if text.isascii():
        return len(text)
    from wcwidth import wcswidth  # 60 ms to load: only for text that needs it
    w = wcswidth(text)
    return w if w >= 0 else len(text)


def pad(text: str, columns: int) -> str:
    """Text padded with spaces to columns, so that the next column lines up."""
    return text + " " * max(0, columns - width(text))


def cut(text: str, columns: int) -> str:
    """Text that fits in columns: cut with … when it is longer."""
    if width(text) <= columns:
        return text
    out = ""
    for c in text:
        if width(out + c) > columns - 1:
            break
        out += c
    return out + "…"


def confirm(question: str, yes: bool) -> bool:
    """True with yes. Else ask "QUESTION [y/N] " on stderr: only y and Y are yes (BEHAVIOR.md, Q1).

    Raises die(...) when stdin or stderr is not a terminal. The answer loses
    the spaces and tabs at its ends, as with `read -r`; the end of the input is no.
    """
    if yes:
        return True
    if not (os.isatty(0) and os.isatty(2)):
        raise die("confirmation needs a terminal. Use -y to continue without one.")
    _write(sys.stderr, f"{question} [y/N] ")
    return (read_line() or "").strip(" \t") in ("y", "Y")


def read_line() -> Optional[str]:
    """A line from stdin without its line break; None at the end of the input. It reads byte by byte, so
    that the keys after the line stay for the next reader (the picker and the question share stdin)."""
    data = b""
    while True:
        b = os.read(0, 1)
        if not b:
            return data.decode("utf-8", "surrogateescape") if data else None
        if b == b"\n":
            return data.decode("utf-8", "surrogateescape")
        data += b


def terminal_columns(fd: int = 1) -> Optional[int]:
    """The width of the terminal when fd (stdout by default) is one, else None. It asks /dev/tty, as
    `stty size` does."""
    if not os.isatty(fd):
        return None
    try:
        fd = os.open("/dev/tty", os.O_RDONLY)
    except OSError:
        return None
    try:
        return os.get_terminal_size(fd).columns
    except OSError:
        return None
    finally:
        os.close(fd)
