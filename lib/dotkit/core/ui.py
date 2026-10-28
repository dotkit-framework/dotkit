"""Messages, the question, and the width of the terminal.

All text goes out as UTF-8 bytes and is flushed at once, so that it stays in
order with the output of the commands that dotkit runs. Errors are
exceptions: a command raises die(...) or usage_error(...), and the entry point
prints the text and exits with the code (BEHAVIOR.md, the introduction).
"""

from __future__ import annotations

import os
import re
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


# --- Colors. The text is the same with and without them: colors and symbols are only for a terminal.

# --no-color of the command line (__main__), for this run.
_no_color = False

# The roles of the text, and their SGR codes. The help (cli.style) uses them too.
STYLES = {
    "error": "1;31", "warn": "1;33", "ok": "1;32", "heading": "1", "name": "1", "file": "36", "command": "36",
    "added": "32", "removed": "31", "changed": "33", "dim": "2", "nix": "34", "brew": "33", "mise": "35",
    # The help: the headings of its sections, and the first column of its lists (commands, options).
    "section": "1;33", "option": "32",
}
# The commands of dotkit (cli.COMMANDS and help; tests/commands/test_commands.py compares them). Only
# "dotkit COMMAND" is a command in a message, with the words after it that look like options or arguments:
# a word that starts with - or ~, or that has : / @ . or - in it. Other commands are highlighted in backticks.
COMMANDS = ("apply", "upgrade", "add", "remove", "info", "list", "own", "doctor", "history", "migrate", "tidy",
            "rollback", "clean", "help")
_ARG = r"(?:-[\w-]+|~(?:[\w:@./~-]*[\w/])?|[\w@~-]*[:/@.-][\w:@./~-]*[\w/])"
_COMMAND = re.compile(r"\bdotkit (?:(?:" + "|".join(COMMANDS) + r")\b|-[\w-]+)(?: " + _ARG + ")*")
_CODE = re.compile(r"`([^`]+)`")


def no_color() -> None:
    global _no_color
    _no_color = True


def color_allowed() -> bool:
    """Colors are not turned off: no --no-color, NO_COLOR and DOTKIT_NO_COLOR empty or not set, TERM not dumb.
    The picker and the curses wizard use this; for a stream, colors()."""
    return (not _no_color and not os.environ.get("NO_COLOR") and not os.environ.get("DOTKIT_NO_COLOR")
            and os.environ.get("TERM") != "dumb")


def colors(stream=None) -> bool:
    """Colors for a stream (stdout by default): color_allowed(), and the stream is a terminal."""
    stream = sys.stdout if stream is None else stream
    return color_allowed() and stream.isatty()


def sgr(text: str, role: str) -> str:
    """text in the color of role, always: for the callers that checked colors() already."""
    return f"\033[{STYLES[role]}m{text}\033[0m" if text else text


def paint(text: str, role: str, err: bool = False) -> str:
    """text in the color of role, when the stream (stderr with err) gets colors."""
    return sgr(text, role) if colors(sys.stderr if err else sys.stdout) else text


def mark(symbol: str, role: str, err: bool = False) -> str:
    """A symbol and a space before a line, only with colors: ✓, !, ▸."""
    return paint(symbol, role, err) + " " if colors(sys.stderr if err else sys.stdout) else ""


def commands(text: str, err: bool = False) -> str:
    """The commands in text (dotkit apply, `code`) in the command color. The backticks go away only with
    colors."""
    if not colors(sys.stderr if err else sys.stdout):
        return text
    # The backticks first: a command in them is then colored once.
    return _COMMAND.sub(lambda m: sgr(m[0], "command"), code(text))


def code(text: str) -> str:
    """The text in backticks in the command color, without the backticks."""
    return _CODE.sub(lambda m: sgr(m[1], "command"), text)


def diff_line(line: str, err: bool = False) -> str:
    """A line of a unified diff in colors on a terminal: the headers dim, + green, - red, @@ cyan."""
    if line.startswith(("+++", "---")):
        return paint(line, "dim", err)
    role = {"+": "added", "-": "removed", "@": "command"}.get(line[:1])
    return paint(line, role, err) if role else line


def out(text: str = "") -> None:
    """A line on stdout."""
    _write(sys.stdout, text + "\n")


def note(text: str) -> None:
    """A line on stderr, without the dotkit: prefix: "Checking Nix...", "No change. ..."."""
    _write(sys.stderr, text + "\n")


def warn(msg: str) -> None:
    """dotkit: MSG on stderr; the command goes on."""
    note(paint("dotkit:", "warn", True) + " " + commands(msg, True))


def progress(text: str) -> None:
    """A step that takes a while, on stderr: "Checking Nix..."."""
    note(mark("▸", "command", True) + paint(text, "dim", True))


def hint(text: str) -> None:
    """What to do next, on stderr: "No change. Run dotkit apply to apply."."""
    note(mark("›", "dim", True) + commands(text, True))


def success(text: str) -> None:
    """The end of a command that changed something, on stderr."""
    note(mark("✓", "ok", True) + paint(text, "ok", True))


def error_text(text: str) -> str:
    """The text of an Exit for stderr: "dotkit:" and "error:" in red."""
    if not colors(sys.stderr):
        return text
    text = re.sub(r"^dotkit:", paint("dotkit:", "error", True), text, flags=re.M)
    text = re.sub(r"^(dotkit [a-z]+: )error:", lambda m: m[1] + paint("error:", "error", True), text, flags=re.M)
    return commands(text, True)


def raw_err(text: str) -> None:
    """Text on stderr as it is, for the output of a command that dotkit captured."""
    _write(sys.stderr, text)


def flush() -> None:
    """Flush both streams, before a command writes to them."""
    sys.stdout.flush()
    sys.stderr.flush()


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
    _write(sys.stderr, f"{mark('?', 'warn', True)}{paint(question, 'heading', True)} {paint('[y/N]', 'dim', True)} ")
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
