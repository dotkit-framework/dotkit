"""The links of dotkit.links in the nix/ files of the dotfiles repository, as text.

A link is a line of a `dotkit.links = {` block, `"TARGET" = "SOURCE";`, or a
line `dotkit.links."TARGET" = "SOURCE";`; null instead of the source removes
a base link. dotkit own adds and deletes such lines: find_insert() and
find_rows() plan an Edit, and apply() makes the edits. An edit keeps the text
of its line, and apply() checks it first: when a file changed after the plan,
apply() raises OSError and changes nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# A link line; target and source are the text between the quotes, still escaped. The migration reads the
# template block with it too (migrate/docs.py).
ROW = re.compile(r'^\s*(?:dotkit\.links\.)?"(?P<target>(?:[^"\\]|\\.)*)"\s*=\s*'
                 r'(?:null|"(?P<source>(?:[^"\\]|\\.)*)")\s*;\s*(?:#.*)?$')
BLOCK = re.compile(r"^(?P<indent>[ \t]*)dotkit\.links = \{[ \t]*$")
DOTTED = re.compile(r'^(?P<indent>[ \t]*)dotkit\.links\."')


def nix_string(s: str) -> str:
    """A Nix string literal, with \\, ", and ${ escaped."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${") + '"'


@dataclass(frozen=True)
class Edit:
    path: Path
    at: int  # the index of the line that the edit deletes, or that the new line goes after
    expected: str  # the text of line `at` at the plan
    line: str = ""  # the new line; "" deletes line `at`


def nix_files(repo: Path) -> List[Path]:
    return sorted((repo / "nix").glob("*.nix"))


def _read(path: Path) -> List[str]:
    return path.read_text(encoding="utf-8").split("\n")


def find_insert(repo: Path, target: str, source: str) -> Optional[Edit]:
    """The new link line of target: after the one `dotkit.links = {` line of nix/*.nix, two spaces deeper,
    else after the last `dotkit.links."..."` line. None when the files have neither, or more than one block."""
    blocks, dotted = [], []
    link = f"{nix_string(target)} = {nix_string(source)};"
    for path in nix_files(repo):
        for i, line in enumerate(_read(path)):
            m = BLOCK.match(line)
            if m:
                blocks.append(Edit(path, i, line, f'{m.group("indent")}  {link}'))
            m = DOTTED.match(line)
            if m:
                dotted.append(Edit(path, i, line, f'{m.group("indent")}dotkit.links.{link}'))
    if len(blocks) == 1:
        return blocks[0]
    if not blocks and dotted:
        return dotted[-1]
    return None


def find_rows(repo: Path, target: str) -> List[Edit]:
    """An edit that deletes it, for each line of nix/*.nix that sets the link of target."""
    want = nix_string(target)[1:-1]
    return [Edit(path, i, line) for path in nix_files(repo) for i, line in enumerate(_read(path))
            if (m := ROW.match(line)) and m["target"] == want]


def apply(edits: Sequence[Edit]) -> None:
    """Makes the edits, a file at a time, from the bottom, so that the indexes stay right. New lines after the
    same line keep the order of edits. Raises OSError, before any change to a file, when a line is not the
    one of the plan."""
    by_file: Dict[Path, List[Edit]] = {}
    for e in edits:
        by_file.setdefault(e.path, []).append(e)
    texts = {}
    for path, es in by_file.items():
        lines = _read(path)
        for e in es:
            if e.at >= len(lines) or lines[e.at] != e.expected:
                raise OSError(f"{path} changed after the plan: line {e.at + 1} is not {e.expected.strip()!r} any more")
        for e in sorted(es, key=lambda e: (e.at, es.index(e)), reverse=True):
            if e.line:
                lines.insert(e.at + 1, e.line)
            else:
                del lines[e.at]
        texts[path] = "\n".join(lines)
    for path, text in texts.items():
        path.write_text(text, encoding="utf-8")
