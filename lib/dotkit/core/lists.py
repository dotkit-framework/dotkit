"""The list files: nix/packages.txt, brews.txt, casks.txt, taps.txt, and mas.txt.

The readers have the rules of the Nix code that reads the lists for the
configuration, so that dotkit sees what Nix installs:

- nix/read-list.nix: a name is the text of a line before `#`, without the
  whitespace at its ends. Blank lines and comments give no name.
- nix/read-taps.nix: a tap is the first word of a name of read-list.nix;
  a second word is its clone URL.
- nix/read-mas.nix: an App Store app is a line `ID name`, without the
  whitespace at its ends; a line that starts with `#` is a comment. When two
  lines have the same name, the first one counts.

tests/core/test_files.py compares the readers with the Nix code. The dotkit
command and the migration wizard both use them. A file that does not exist
has no names; another error of the file system raises OSError.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# [[:space:]] of the regular expressions of Nix: lib.trim removes these.
SPACE = " \t\n\r\v\f"
_APP = re.compile(r"([0-9]+)[" + re.escape(SPACE) + r"]+(.+)", re.S)


def _lines(path: Path) -> List[str]:
    try:
        return path.read_bytes().decode("utf-8", "surrogateescape").split("\n")
    except FileNotFoundError:
        return []


def name_of(line: str) -> str:
    """The name of one line of a list file; "" for a blank line or a comment."""
    return line.split("#", 1)[0].strip(SPACE)


def names(path: Path) -> List[str]:
    """The names of a list file, in order."""
    return [n for n in map(name_of, _lines(path)) if n]


def taps(path: Path) -> List[str]:
    """The tap names of nix/taps.txt, in order, without their clone URLs."""
    return [re.split("[" + re.escape(SPACE) + "]+", n, 1)[0] for n in names(path)]


def mas_apps(path: Path) -> List[Tuple[str, str]]:
    """(ID, name) of each App Store app of nix/mas.txt, in order. A line that is not `ID name` is left out
    here; Nix stops with an error for it."""
    out = []
    for raw in _lines(path):
        line = raw.strip(SPACE)
        m = _APP.fullmatch(line)
        if m and not line.startswith("#"):
            out.append((m.group(1), m.group(2)))
    return out


def mas_id_of(name: str, path: Path, planned: Iterable[Tuple[str, str]] = ()) -> Optional[str]:
    """The ID of the App Store app named `name` in nix/mas.txt or in the planned (ID, name) entries."""
    for app_id, app_name in [*mas_apps(path), *planned]:
        if app_name == name:
            return app_id
    return None


def append(path: Path, name: str) -> None:
    """Adds the line NAME; adds the missing line break at the end of the file first."""
    text = "\n".join(_lines(path))
    with open(path, "a", encoding="utf-8", errors="surrogateescape", newline="") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        f.write(name + "\n")


def delete(path: Path, name: str) -> None:
    """Removes each line whose name is NAME. The other lines and the comments stay as they are.

    The file is written in place, so a link stays a link.
    """
    lines = _lines(path)
    kept = [line for line in lines if name_of(line) != name]
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write("\n".join(kept))


def delete_mas(path: Path, app_id: str) -> None:
    """Removes the line of the App Store app with this ID."""
    lines = _lines(path)
    kept = [line for line in lines
            if not ((m := _APP.fullmatch(line.strip(SPACE))) and m.group(1) == app_id)]
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write("\n".join(kept))
