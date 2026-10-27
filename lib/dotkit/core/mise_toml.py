"""The [tools] keys of a mise config file, read line by line as the awk of the Bash scripts did (PARITY.md, L5).

This is not a TOML parser, and it must not become one without a deviation in
PARITY.md: a key line starts with a character that is not `#` or a space,
only a line that is exactly `[tools]` starts the section, and any other
line that starts with `[` ends it. A file that does not exist has no tools.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

# [[:space:]] of awk.
_SPACE = " \t\n\r\f\v"
_KEY_LINE = re.compile(r"[^#" + re.escape(_SPACE) + r"][^=]*=")
_VERSION = re.compile(r'version = "[^"]*"')


def _lines(path: Path) -> List[str]:
    try:
        text = path.read_bytes().decode("utf-8", "surrogateescape")
    except FileNotFoundError:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def tool_lines(path: Path) -> List[Tuple[str, str]]:
    """(key, value) for each tool line, in order. The key has no quotes. The value is the string without
    quotes, or the version of an inline table, or else the text of the table without quotes."""
    out = []
    on = False
    for line in _lines(path):
        if line.startswith("["):
            on = line == "[tools]"
            continue
        if not on or not _KEY_LINE.match(line):
            continue
        eq = line.index("=")
        key = line[:eq].rstrip(_SPACE).replace('"', "")
        value = line[eq + 1:].lstrip(_SPACE)
        if value.startswith("{"):
            m = _VERSION.search(value)
            if m:
                value = m.group(0)[len('version = "'):-1]
        out.append((key, value.replace('"', "")))
    return out


def keys(path: Path) -> List[str]:
    return [key for key, _ in tool_lines(path)]


def last_part(key: str) -> str:
    """The part of a key after its last `:` or `/`: github:modem-dev/hunk -> hunk."""
    return re.sub(r".*[:/]", "", key)
