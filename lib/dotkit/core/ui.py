"""Messages, the question, and the width of the terminal.

All text goes out as UTF-8 bytes and is flushed at once, so that it stays in
order with the output of the commands that dotkit runs. Errors are
exceptions: a command raises die(...) or usage_error(...), and the entry point
prints the text and exits with the code (PARITY.md, the introduction).
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


def usage_error(msg: str, hint: str = "Use dotkit --help.") -> Exit:
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


def pad(text: str, width: int) -> str:
    """Text padded with spaces to width bytes, as printf %-Ns of Bash and awk pads it."""
    return text + " " * max(0, width - len(text.encode("utf-8", "surrogateescape")))


def confirm(question: str, yes: bool) -> bool:
    """True with yes. Else ask "QUESTION [y/N] " on stderr: only y and Y are yes (PARITY.md, Q1).

    Raises die(...) when stdin or stderr is not a terminal. The answer loses
    the spaces and tabs at its ends, as with `read -r`; the end of the input is no.
    """
    if yes:
        return True
    if not (os.isatty(0) and os.isatty(2)):
        raise die("confirmation needs a terminal. Use -y to continue without one.")
    _write(sys.stderr, f"{question} [y/N] ")
    line = sys.stdin.buffer.readline().decode("utf-8", "surrogateescape")
    return line.rstrip("\n").strip(" \t") in ("y", "Y")


def terminal_columns() -> Optional[int]:
    """The width of the terminal when stdout is one, else None. It asks /dev/tty, as `stty size` does."""
    if not os.isatty(1):
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
