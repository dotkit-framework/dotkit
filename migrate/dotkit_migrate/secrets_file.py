"""~/.secrets: the export lines of tokens and passwords, outside every repository.

The base ~/.zprofile and ~/.bash_profile source it, and the gui-secrets agent
of nix/darwin.nix reads it with /bin/sh. So the wizard writes only POSIX
lines: `export NAME='value'`, or the assignment of a shell file as it was.
read() takes the assignments of an existing file, so that the wizard adds
only the names that are not there yet and never changes one that is.
Pure functions, no I/O.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_LINE = re.compile(
    r"^\s*(?:export\s+|typeset\s+-x\s+|declare\s+-x\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*?)\s*$")


def quote(value: str) -> str:
    """A value in single quotes, for sh, bash, and zsh."""
    return "'" + value.replace("'", "'\\''") + "'"


def export_line(name: str, value: str) -> str:
    if not NAME.fullmatch(name):
        raise ValueError(f"not a variable name: {name!r}")
    return f"export {name}={quote(value)}"


def unquote(text: str) -> Optional[str]:
    """The value of a shell word without expansions: 'a', "a", a, or quoted parts after each other.

    None for a word that the shell would expand ($, `, a glob, ~ at the start)
    or that is not one word: such a value is compared as its text.
    """
    out, i = [], 0
    text = text.strip()
    if not text:
        return ""
    while i < len(text):
        c = text[i]
        if c == "'":
            end = text.find("'", i + 1)
            if end < 0:
                return None
            out.append(text[i + 1:end])
            i = end + 1
        elif c == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                if text[i] in "$`":
                    return None
                if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in '"\\$`':
                    i += 1
                out.append(text[i])
                i += 1
            if i >= len(text):
                return None
            i += 1
        elif c == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        elif c in "$`*?[~;&|<>()# \t" and not (c == "#" and out):
            return None
        else:
            out.append(c)
            i += 1
    return "".join(out)


def value_of(text: str) -> str:
    """The value of the right side of an assignment, for comparing: unquoted when it can be, else its text."""
    v = unquote(text)
    return text.strip() if v is None else v


def read(text: str) -> Dict[str, str]:
    """The variables that a ~/.secrets text assigns, name -> value_of(right side). The last one wins."""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            out[m.group("name")] = value_of(m.group("value"))
    return out
