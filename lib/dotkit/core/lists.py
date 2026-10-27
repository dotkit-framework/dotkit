"""The list files: nix/packages.txt, brews.txt, casks.txt, taps.txt, and mas.txt (PARITY.md, rows L6 and F).

A list has one name per line: the first word, after the comment (`#` to the
end of the line) is removed. nix/read-list.nix reads them the same way. A
list file that does not exist reads as empty (PARITY.md, DV5).

The functions read and write bytes as UTF-8 with surrogateescape, so a byte
that is not UTF-8 stays as it is.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional

# awk splits the fields of a line at spaces and tabs.
_FIELDS = re.compile(r"[ \t]+")
# [[:space:]] of awk.
_SPACE = " \t\n\r\f\v"


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", "surrogateescape")
    except FileNotFoundError:
        return ""


def _first_word(line: str) -> str:
    words = _FIELDS.split(line.strip(" \t"))
    return words[0] if words else ""


def names(path: Path) -> List[str]:
    """The names of a list file, in order: `awk '{ sub(/#.*/, "") } NF { print $1 }'`."""
    out = []
    for line in _read(path).split("\n"):
        word = _first_word(line.split("#", 1)[0])
        if word:
            out.append(word)
    return out


def append(path: Path, name: str) -> None:
    """Adds the line NAME; adds the missing line break at the end of the file first."""
    text = _read(path)
    with open(path, "a", encoding="utf-8", errors="surrogateescape", newline="") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        f.write(name + "\n")


def delete(path: Path, name: str) -> None:
    """Removes each line whose name is NAME. Comments and the other lines stay, each with a line break.

    The file is written in place, so a link stays a link.
    """
    text = _read(path)
    lines = text.split("\n") if text else []
    if text.endswith("\n"):
        lines.pop()
    kept = [line for line in lines if _first_word(line.split("#", 1)[0]) != name]
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write("".join(line + "\n" for line in kept))


def mas_id_of(name: str, path: Path, planned: Iterable[str] = ()) -> Optional[str]:
    """The ID of the first App Store app named NAME in nix/mas.txt, else in the planned entries ("mas:ID Name").

    The same rules as nix/read-mas.nix: a line is an ID, spaces, and the
    name; spaces at the ends of the line do not count (PARITY.md, F3).
    """
    lines = [line.strip(_SPACE) for line in _read(path).split("\n")]
    lines = [line for line in lines if re.match(r"[0-9]+[" + re.escape(_SPACE) + "]", line)]
    lines += [e[len("mas:"):] for e in planned if e.startswith("mas:")]
    for line in lines:
        app_id = _first_word(line)
        rest = re.sub(r"^[0-9]+[" + re.escape(_SPACE) + "]+", "", line)
        if rest == name:
            return app_id
    return None
