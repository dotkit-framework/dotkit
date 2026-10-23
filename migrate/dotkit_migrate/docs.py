"""The documents of the new repository and the transactions that change them.

A document is one output file (or, for Copies, the files copied into the
repository). It starts from base text: the template file in setup, the
repository's current file in adopt. Planning code changes documents only
through a Txn from Docs.begin(owner), and the rules are the same for every
view:

1. add() checks the entry and raises ValueError for a bad one, so rendering
   committed entries never fails;
2. it resolves against the base text first, then committed and pending
   entries: the first writer wins;
3. the same entry again is a no-op that returns False;
4. a different value for the same key is a conflict: recorded with both
   owners in Docs.conflicts, raised in strict mode, and otherwise the first
   value stays (add returns False);
5. add() returns True or False right away;
6. nothing reaches a document before Txn.commit(); rollback() drops what
   the transaction added.
"""

from __future__ import annotations

import copy
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from . import base as base_mod
from . import rcparse, secrets
from ._compat import tomllib
from .context import tilde
from .report import ORDER

# --- Text helpers


def nix_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${") + '"'


_TOML_ESCAPES = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r", '"': '\\"', "\\": "\\\\"}


def toml_key(s: str) -> str:
    """A TOML basic string: a quoted key, or a string value."""
    return '"' + "".join(_TOML_ESCAPES.get(c, c if c >= " " and c != "\x7f" else f"\\u{ord(c):04x}")
                         for c in s) + '"'


def _bare_or_quoted(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else toml_key(key)


def toml_value(v: object) -> str:
    """A value that _check_value accepts, as TOML on one line."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return toml_key(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    assert isinstance(v, dict)
    return "{ " + ", ".join(f"{_bare_or_quoted(k)} = {toml_value(x)}" for k, x in v.items()) + " }"


def insert_tools(text: str, lines: List[str]) -> str:
    """Text of a mise config with the lines added at the end of its [tools] table."""
    if not lines:
        return text
    rows = text.splitlines()
    start = next((i for i, r in enumerate(rows) if re.match(r"^\s*\[tools\]\s*(#.*)?$", r)), None)
    if start is None:
        body = text.rstrip("\n")
        return (body + "\n\n" if body else "") + "[tools]\n" + "\n".join(lines) + "\n"
    end = next((i for i in range(start + 1, len(rows)) if re.match(r"^\s*\[", rows[i])), len(rows))
    while end > start + 1 and not rows[end - 1].strip():
        end -= 1
    rows[end:end] = lines
    return "\n".join(rows) + "\n"


def ruby_escape(name: str) -> str:
    """A name as nix/read-mas.nix escapes it for the Brewfile: `\\`, `"`, and `#` get a `\\` in front."""
    return re.sub(r'[\\"#]', lambda m: "\\" + m.group(0), name)


# --- The checks of the list lines
#
# nix/read-taps.nix and nix/read-mas.nix reject a bad line, and that stops the
# evaluation of the whole configuration, so ListDoc checks each line first.

_TAP_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def tap_name_problem(name: str) -> Optional[str]:
    """Why a tap name is not owner/repo, or None."""
    if not _TAP_NAME.fullmatch(name):
        return "its name is not owner/repo of letters, digits, `_`, `.`, and `-`"
    return None


def tap_problem(line: str) -> Optional[str]:
    """Why a line cannot go into nix/taps.txt, or None. A URL with a secret says "it has a secret (<what>)".

    The line is `owner/repo`, or `owner/repo <clone URL>` for a tap that is not on GitHub.
    """
    name, sep, url = line.partition(" ")
    problem = tap_name_problem(name)
    if problem:
        return problem
    if not sep:
        return None
    # The secret first: such a URL is never written and never shown.
    what = secrets.line_secret(url)
    if what:
        return f"it has a secret ({what})"
    for bad, why in ((not url, "it is empty"), (re.search(r"\s", url), "it has a space"),
                     ("#" in url, "it has a `#`"), ('"' in url, 'it has a `"`'), ("\\" in url, "it has a `\\`")):
        if bad:
            return why
    return None


def mas_problem(app_id: object, name: str) -> Optional[str]:
    """Why an App Store app cannot go into nix/mas.txt, or None."""
    if not re.fullmatch(r"[0-9]+", str(app_id)):
        return "its ID is not digits"
    if not name.strip():
        return "its name is empty"
    if "\n" in name or "\r" in name:
        return "its name has a line break"
    return None


def mas_label(app_id: int, name: str, taken: Dict[str, int]) -> Optional[str]:
    """The name of an app in nix/mas.txt: its name, or `<name> (<ID>)` when another app has the name.

    homebrew.masApps lists apps by name, and the name is only a label. None
    when other apps have both.
    """
    label = name if name not in taken else f"{name} ({app_id})"
    if label in taken and taken[label] != app_id:
        return None
    return label


def _list_lines(text: str) -> List[str]:
    """The entries of a list file: each line without its comment, trimmed, without blank lines."""
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _mas_entries(text: str) -> Dict[str, int]:
    """The apps of nix/mas.txt, name -> ID, as nix/read-mas.nix reads them before it escapes the names."""
    apps: Dict[str, int] = {}
    for line in text.splitlines():
        m = re.fullmatch(r"(\d+)\s+(.+)", line.strip())
        if m and not line.strip().startswith("#"):
            apps.setdefault(m.group(2), int(m.group(1)))
    return apps


def _check_comment(comment: str) -> None:
    if comment and (not comment.startswith("#") or "\n" in comment or "\r" in comment):
        raise ValueError(f"a comment is one line that starts with #: {comment!r}")


def _check_rel(rel: str, what: str) -> None:
    if not rel or rel.startswith("/") or "\n" in rel or ".." in Path(rel).parts:
        raise ValueError(f"{what} must be a relative path without ..: {rel!r}")


# Repository paths are compared the way APFS compares names by default:
# without case, and without differences of Unicode normalization. The
# original spelling stays for writing and for messages.


def _key(part: str) -> str:
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", part).casefold())


def _parts(rel: str) -> Tuple[str, ...]:
    """The keys of the parts of a repository path."""
    return tuple(_key(p) for p in PurePosixPath(rel).parts)


def _same(a: str, b: str) -> bool:
    return _parts(a) == _parts(b)


def _inside(rel: str, outer: str) -> bool:
    """rel is below outer, by path parts: config/bash/x is inside config/bash, config/bash2 is not."""
    a, b = _parts(rel), _parts(outer)
    return len(a) > len(b) and a[:len(b)] == b


# --- Conflicts


class ConflictError(ValueError):
    pass


@dataclass
class Conflict:
    path: str
    key: str
    first: str  # the owner whose value stays
    second: str  # the owner whose value was refused
    kept: str
    refused: str


# --- Documents
#
# Each document has its committed state and, while a transaction works on
# it, a copy of that state with the pending entries. commit() swaps the
# copy in; rollback() drops it.


class _Doc:
    def __init__(self, docs: Docs, path: str):
        self.docs = docs
        self.path = path
        self.state = self._empty()
        self.work = None

    def _empty(self):
        raise NotImplementedError

    def _w(self, txn: Txn):
        """The state with the pending entries of the open transaction."""
        assert txn is self.docs._open, "the transaction is not open"
        if self.work is None:
            self.work = copy.deepcopy(self.state)
            txn._touched.append(self)
        return self.work

    def _view_state(self, txn: Txn):
        return self.work if self.work is not None and txn is self.docs._open else self.state

    def _conflict(self, txn: Txn, key: str, first: str, kept: str, refused: str) -> bool:
        return self.docs._conflict(Conflict(self.path, key, first, txn.owner, kept, refused))


@dataclass
class _ListState:
    entries: List[Tuple[str, str, str, str]] = field(default_factory=list)  # (owner, key, line, comment)


class ListDoc(_Doc):
    """nix/brews.txt, nix/casks.txt, nix/taps.txt, nix/mas.txt."""

    KINDS = {"nix/brews.txt": "brews", "nix/casks.txt": "casks", "nix/taps.txt": "taps", "nix/mas.txt": "mas"}

    def __init__(self, docs: Docs, path: str, base_text: str):
        super().__init__(docs, path)
        self.kind = self.KINDS[path]
        self.base_text = base_text
        if self.kind == "mas":
            self.base = {name: f"{i} {name}" for name, i in _mas_entries(base_text).items()}
        else:
            self.base = {}
            for line in _list_lines(base_text):
                self.base.setdefault(line.split()[0], line)

    def _empty(self):
        return _ListState()

    def _check(self, entry: str) -> Tuple[str, str]:
        """The key and the line of an entry. Raises ValueError with the reason."""
        if self.kind == "taps":
            problem = tap_problem(entry)
            if problem:
                raise ValueError(problem)
            return entry.partition(" ")[0], entry
        if self.kind == "mas":
            app_id, _, name = entry.partition(" ")
            problem = mas_problem(app_id, name)
            if problem:
                raise ValueError(problem)
            return name, entry
        if not re.fullmatch(r"[^\s#]+", entry):
            raise ValueError("it is not one word without `#`")
        return entry, entry

    def add(self, txn: Txn, entry: str, comment: str = "") -> bool:
        key, line = self._check(entry)
        _check_comment(comment)
        w = self._w(txn)
        if self.kind == "mas":
            app_id = int(line.partition(" ")[0])
            taken = {n: int(v.partition(" ")[0]) for n, v in self.base.items()}
            for _, k, v, _ in w.entries:
                taken.setdefault(k, int(v.partition(" ")[0]))
            if taken.get(key) == app_id:
                return False
            label = mas_label(app_id, key, taken)
            if label is None:
                raise ValueError(f"other apps have this name and `{key} ({app_id})`")
            if taken.get(label) == app_id:
                return False
            key, line = label, f"{app_id} {label}"
        if key in self.base:
            return False
        for owner, k, v, _ in w.entries:
            if k == key:
                return False if v == line else self._conflict(txn, key, owner, v, line)
        w.entries.append((txn.owner, key, line, comment))
        return True

    def names(self, txn: Optional[Txn] = None) -> List[str]:
        """The keys: of the base text, then of the entries. A view also sees its pending entries."""
        st = self._view_state(txn) if txn else self.state
        return list(self.base) + [k for _, k, _, _ in st.entries if k not in self.base]

    def added(self) -> List[str]:
        return [line for _, _, line, _ in self.state.entries]

    def render(self) -> Optional[str]:
        if not self.state.entries:
            return None
        rows, said = [], set()
        for _, _, line, comment in self.state.entries:
            if comment and comment not in said:
                said.add(comment)
                rows.append(comment)
            rows.append(line)
        host = self.docs.host or "this Mac"
        if self.docs.mode == "adopt":
            text = self.base_text + ("\n" if self.base_text and not self.base_text.endswith("\n") else "")
            return text + f"# From {host}, see MIGRATION.md.\n" + "".join(r + "\n" for r in rows)
        source = "the App Store" if self.kind == "mas" else "Homebrew"
        return (self.base_text.rstrip("\n") + "\n\n" + f"# From {source} on {host}, see MIGRATION.md.\n" +
                "".join(r + "\n" for r in rows))


def _check_value(v: object) -> None:
    """A mise tool value: a string, a list of strings, or a flat table of strings, integers, and booleans."""
    if isinstance(v, str):
        return
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return
    if isinstance(v, dict) and all(isinstance(k, str) and k and isinstance(x, (str, int, bool))
                                   for k, x in v.items()):
        return
    raise ValueError(f"not a string, a list of strings, or a flat table: {v!r}")


def _is_value(v: object) -> bool:
    try:
        _check_value(v)
    except ValueError:
        return False
    return True


def _check_setting(v: object) -> None:
    """A mise setting: a string, an integer, a boolean, or a list of strings."""
    if isinstance(v, (str, int, bool)) or (isinstance(v, list) and all(isinstance(x, str) for x in v)):
        return
    raise ValueError(f"not a string, an integer, a boolean, or a list of strings: {v!r}")


def _check_key(key: str) -> None:
    if not isinstance(key, str) or not key or "\n" in key or "\r" in key:
        raise ValueError(f"a mise key is a non-empty line: {key!r}")


def _parse(text: str) -> Optional[dict]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


_SETTINGS = re.compile(r"^\s*\[\s*settings\s*\]\s*(#.*)?$")
_HEADER = re.compile(r"^\s*\[")


def _table_rows(rows: List[str]) -> Optional[Tuple[int, int]]:
    """The rows of the [settings] table: (header row, end), or None."""
    start = next((i for i, r in enumerate(rows) if _SETTINGS.match(r)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(rows)) if _HEADER.match(rows[i])), len(rows))
    return start, end


@dataclass
class _MiseState:
    text: str = ""  # the base text, with the settings that were set or replaced
    base_owner: str = ""  # who called set_base
    tools: List[Tuple[str, str, object, str]] = field(default_factory=list)  # (owner, key, value, comment)
    edited: bool = False  # a setting was set or replaced


class MiseDoc(_Doc):
    """config/mise.toml."""

    def __init__(self, docs: Docs, path: str, base_text: str):
        self.original = base_text
        super().__init__(docs, path)
        self.state.text = base_text

    def _empty(self):
        return _MiseState()

    def _setup_only(self, what: str) -> None:
        if self.docs.mode != "setup":
            raise ValueError(f"{what} is for setup only")

    def set_base(self, txn: Txn, text: str) -> bool:
        """Your mise config instead of the template: the template's comments, then your file as it is."""
        self._setup_only("set_base")
        w = self._w(txn)
        header = "".join(line + "\n" for line in self.original.splitlines() if line.startswith("#"))
        merged = header + "\n# From ~/.config/mise/config.toml.\n" + text.rstrip("\n") + "\n"
        if w.base_owner:
            return False if merged == w.text else self._conflict(txn, "(base)", w.base_owner, w.text, merged)
        if w.edited:
            raise ValueError("set_base comes before the settings")
        # The base text wins over a tool entry that has the same key: a conflict.
        have = self._base_tools(merged)
        clash = [e for e in w.tools if e[1] in have]
        for owner, key, value, _ in clash:
            self.docs._conflict(Conflict(self.path, key, txn.owner, owner, toml_value(have[key])
                                         if _is_value(have[key]) else repr(have[key]), toml_value(value)))
        w.tools = [e for e in w.tools if e not in clash]
        w.text, w.base_owner = merged, txn.owner
        return True

    @staticmethod
    def _base_tools(text: str) -> dict:
        data = _parse(text)
        tools = data.get("tools") if data else None
        return tools if isinstance(tools, dict) else {}

    def has_tool(self, txn: Txn, key: str) -> bool:
        st = self._view_state(txn)
        return key in self._base_tools(st.text) or any(k == key for _, k, _, _ in st.tools)

    def add_tool(self, txn: Txn, key: str, value: object, comment: str = "") -> bool:
        _check_key(key)
        _check_value(value)
        _check_comment(comment)
        w = self._w(txn)
        if key in self._base_tools(w.text):
            return False
        for owner, k, v, _ in w.tools:
            if k == key:
                return False if v == value else self._conflict(txn, key, owner, toml_value(v), toml_value(value))
        w.tools.append((txn.owner, key, copy.deepcopy(value), comment))
        return True

    def set_setting(self, txn: Txn, key: str, value: object) -> bool:
        """Add a key to [settings] when it is absent. False when it is there, or the edit would not be clean."""
        self._setup_only("set_setting")
        _check_key(key)
        _check_setting(value)
        w = self._w(txn)
        data = _parse(w.text)
        if data is None or ("settings" in data and not isinstance(data["settings"], dict)):
            return False
        if key in data.get("settings", {}):
            return False
        line = f"{_bare_or_quoted(key)} = {toml_value(value)}"
        rows = w.text.splitlines()
        span = _table_rows(rows)
        if span is None:
            body = w.text.rstrip("\n")
            new = (body + "\n\n" if body else "") + "[settings]\n" + line + "\n"
        else:
            start, end = span
            while end > start + 1 and not rows[end - 1].strip():
                end -= 1
            rows[end:end] = [line]
            new = "\n".join(rows) + "\n"
        expected = copy.deepcopy(data)
        expected.setdefault("settings", {})[key] = value
        if _parse(new) != expected:
            return False
        w.text, w.edited = new, True
        return True

    def replace_setting(self, txn: Txn, key: str, value: object, expect: object) -> bool:
        """Change a key of [settings] whose value equals expect, when it is one line `key = [...]`.

        Anything else is refused: a dotted key at the top level, an inline
        `settings = {...}`, a value on several lines. The result must parse
        and equal the old data with only that key changed, or nothing changes.
        False when nothing changed.
        """
        self._setup_only("replace_setting")
        _check_key(key)
        _check_setting(value)
        w = self._w(txn)
        data = _parse(w.text)
        settings = data.get("settings") if data else None
        if not isinstance(settings, dict) or key not in settings or settings[key] != expect or value == expect:
            return False
        rows = w.text.splitlines()
        span = _table_rows(rows)
        if span is None:
            return False
        names = "|".join(re.escape(k) for k in (key, toml_key(key), "'" + key + "'"))
        for i in range(span[0] + 1, span[1]):
            m = re.fullmatch(rf"(\s*)({names})(\s*=\s*)(\[.*)", rows[i])
            if not m:
                continue
            rest = m.group(4)
            # The array ends on this line; a comment after it stays.
            cut = next((j + 1 for j in range(len(rest)) if rest[j] == "]" and _parse("x = " + rest[:j + 1])
                        is not None and (not rest[j + 1:].strip() or rest[j + 1:].lstrip().startswith("#"))), None)
            if cut is None:
                return False
            rows[i] = m.group(1) + m.group(2) + m.group(3) + toml_value(value) + rest[cut:]
            new = "\n".join(rows) + "\n"
            expected = copy.deepcopy(data)
            expected["settings"][key] = copy.deepcopy(value)
            if _parse(new) != expected:
                return False
            w.text, w.edited = new, True
            return True
        return False

    def added(self) -> List[str]:
        return [k for _, k, _, _ in self.state.tools]

    def tool_lines(self) -> List[str]:
        """The lines that render() adds to [tools]: each comment once, before its first tool."""
        rows, said = [], set()
        for _, key, value, comment in self.state.tools:
            if comment and comment not in said:
                said.add(comment)
                rows.append(comment)
            rows.append(f"{toml_key(key)} = {toml_value(value)}")
        return rows

    def render(self) -> Tuple[str, Optional[str]]:
        """The text, and the parse error when it would not parse (then it is the base text)."""
        text = insert_tools(self.state.text, self.tool_lines())
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            return self.original, str(e)
        return text, None


LINKS_ANCHOR = '    ".zshrc.local" = "config/zsh/zshrc";\n'
_LINK_ROW = re.compile(r'^\s*"((?:[^"\\]|\\.)*)" = (?:null|"((?:[^"\\]|\\.)*)");\s*$')


class LinksDoc(_Doc):
    """dotkit.links in nix/home.nix."""

    def __init__(self, docs: Docs, path: str, base_text: str):
        super().__init__(docs, path)
        self.base_text = base_text
        self.base: Dict[str, Optional[str]] = {}
        inside = False
        for row in base_text.splitlines():
            if re.match(r"^\s*dotkit\.links = \{\s*$", row):
                inside = True
            elif inside and re.match(r"^\s*\};", row):
                inside = False
            elif inside:
                m = _LINK_ROW.match(row)
                if m:
                    self.base[m.group(1)] = m.group(2)

    def _empty(self):
        return {}  # target -> (owner, source)

    def link(self, txn: Txn, target: str, source: Optional[str]) -> bool:
        _check_rel(target, "a link target")
        if source is not None:
            _check_rel(source, "a link source")
        w = self._w(txn)
        if target in self.base:
            return False
        if target in w:
            owner, old = w[target]
            return False if old == source else self._conflict(txn, target, owner, str(old), str(source))
        w[target] = (txn.owner, source)
        return True

    def links(self) -> Dict[str, Optional[str]]:
        return {t: s for t, (_, s) in self.state.items()}

    def drop_source(self, source: str) -> None:
        """For the secret guard, after the transactions: no link to a copy that it dropped."""
        assert self.docs._open is None
        for target in [t for t, (_, s) in self.state.items() if s == source]:
            del self.state[target]

    def render(self) -> Optional[str]:
        if not self.state:
            return None
        rows = []
        for target, source in sorted(self.links().items(), key=lambda kv: (kv[1] is None, kv[0])):
            if source is None:
                rows.append(f"    # Yours stays in place, see MIGRATION.md.\n    {nix_string(target)} = null;\n")
            else:
                rows.append(f"    {nix_string(target)} = {nix_string(source)};\n")
        return self.base_text.replace(LINKS_ANCHOR, LINKS_ANCHOR + "".join(rows))


# The headers of the shell files that have no template file of their own.
_SHELL_HEADERS = {
    ".zshenv": "# Your zsh lines for every zsh, linked to ~/.zshenv.local. The dotkit zshenv\n"
               "# sources this file at the end. Keep it fast and quiet.\n",
    ".zprofile": "# Your lines for zsh login shells, linked to ~/.zprofile.local. The dotkit\n"
                 "# zprofile sources it, then puts the global tool directories first in PATH.\n",
    ".bash_profile": "# shellcheck shell=bash\n# Your lines for bash login shells, linked to\n"
                     "# ~/.bash_profile.local. The dotkit bash_profile sources it, then puts the\n"
                     "# global tool directories first in PATH.\n",
}


@dataclass
class _ShellState:
    parts: List[Tuple[str, str]] = field(default_factory=list)  # (header, text)
    secrets: List[str] = field(default_factory=list)
    ensured: bool = False


class ShellRcDoc(_Doc):
    """A shell file of the repository (config/zsh/zshrc, ...) and its file of secrets in local/."""

    def __init__(self, docs: Docs, main: str):
        if main not in base_mod.SHELL_LAYERS:
            raise ValueError(f"not a shell file of the base: {main!r}")
        self.main = main
        self.shell = "bash" if main == ".bash_profile" else "zsh"
        path = base_mod.SHELL_LAYERS[main][1]
        super().__init__(docs, path)
        self.local = f"local/{Path(path).name}.{self.shell}"
        if path == "config/zsh/zshrc":
            self.header = docs.base_text(path)
        else:
            self.header = _SHELL_HEADERS[main]

    def _empty(self):
        return _ShellState()

    def add_part(self, txn: Txn, header: str, text: str) -> bool:
        """Carried lines, after their `# From ~/<file>.` line."""
        _check_comment(header)
        if not header:
            raise ValueError("a part needs its header line")
        w = self._w(txn)
        if (header, text) in w.parts:
            return False
        w.parts.append((header, text))
        return True

    def add_secret(self, txn: Txn, text: str) -> bool:
        """Lines with secrets: they go to local/, which Git ignores, and the repository file sources it."""
        w = self._w(txn)
        if text in w.secrets:
            return False
        w.secrets.append(text)
        return True

    def ensure(self, txn: Txn) -> None:
        """Write the file also when it carries nothing."""
        self._w(txn).ensured = True

    def render(self) -> Tuple[Optional[str], Optional[str]]:
        """The text of the repository file and of the local file, None for a file that is not written."""
        st = self.state
        parts = [f"{h}\n{t}" for h, t in st.parts]
        local = None
        if st.secrets:
            local = "\n".join(st.secrets) + "\n"
            rel = tilde(self.docs.repo / self.local, self.docs.home).replace("~", "$HOME", 1)
            parts.insert(0, "# Lines with secrets, not in Git. See MIGRATION.md.\n"
                            f'if [[ -r "{rel}" ]]; then\n  source "{rel}"\nfi\n')
        if not (parts or st.ensured):
            return None, local
        return self.header + ("\n" + "\n".join(parts) if parts else ""), local


class ShellNixDoc(_Doc):
    """nix/shell.nix: the zsh keymap."""

    def __init__(self, docs: Docs, path: str, base_text: str):
        super().__init__(docs, path)
        self.base_text = base_text

    def _empty(self):
        return {}  # "keymap" -> (owner, value)

    def set_keymap(self, txn: Txn, value: str) -> bool:
        if value not in ("emacs", "vi"):
            raise ValueError(f"the keymap is emacs or vi: {value!r}")
        w = self._w(txn)
        if "keymap" in w:
            owner, old = w["keymap"]
            return False if old == value else self._conflict(txn, "keymap", owner, old, value)
        w["keymap"] = (txn.owner, value)
        return True

    @property
    def keymap(self) -> str:
        return self.state.get("keymap", ("", "emacs"))[1]

    def render(self) -> Optional[str]:
        if self.keymap != "vi":
            return None
        return self.base_text.replace('# keymap = "vi";', 'keymap = "vi";')


class FileDoc(_Doc):
    """A whole file with one owner, for example nix/user.nix."""

    def _empty(self):
        return {}  # "text" -> (owner, text)

    def set(self, txn: Txn, text: str) -> bool:
        w = self._w(txn)
        for rel, (owner, src) in self.docs.copies._view_state(txn).items():
            # Two documents claim the path: the copy's, a path inside the copy, or one that would contain it.
            if _same(self.path, rel):
                return self._conflict(txn, "(path)", owner, f"a copy of {src}", "a file")
            if _inside(self.path, rel) or _inside(rel, self.path):
                return self._conflict(txn, "(path)", owner, f"a copy of {src} at {rel}", "a file")
        if "text" in w:
            owner, old = w["text"]
            return False if old == text else self._conflict(txn, "(text)", owner, old, text)
        w["text"] = (txn.owner, text)
        return True

    def render(self) -> Optional[str]:
        return self.state["text"][1] if "text" in self.state else None


class Copies(_Doc):
    """Files and directories copied into the repository: repository path -> source."""

    def _empty(self):
        return {}  # rel -> (owner, src)

    def add(self, txn: Txn, rel: str, src: Path) -> bool:
        _check_rel(rel, "a copy")
        home = self.docs.home
        if not (Path(src).is_absolute() and home and str(src).startswith(home.rstrip("/") + "/")):
            raise ValueError(f"a copy comes from inside the home directory: {src}")
        w = self._w(txn)
        # A path that is taken, by an earlier copy or by a document, is not copied.
        if self.taken(txn, rel) is not None:
            return False
        w[rel] = (txn.owner, Path(src))
        return True

    def taken(self, txn: Txn, rel: str) -> Optional[Tuple[str, str]]:
        """Why a repository path cannot get a copy, or None when it is free.

        ("is", rel): an earlier copy or a document has the path;
        ("inside", outer): the path is inside the copied directory outer;
        ("contains", inner): a copy there would contain inner, a document
        path, an earlier copy, or local/ (the first of them by path).
        Paths are compared by their parts, so config/bash2 is not inside
        config/bash.
        """
        copies = self._view_state(txn)
        if any(_same(rel, c) for c in copies) or self.docs._renders(txn, rel):
            return ("is", rel)
        outer = next((c for c in copies if _inside(rel, c)), None)
        if outer is not None:
            return ("inside", outer)
        inner = sorted((p for p in (*copies, *self.docs._claimed(txn), "local") if _inside(p, rel)), key=_parts)
        if inner:
            return ("contains", inner[0])
        return None

    def source(self, txn: Txn, rel: str) -> Optional[Path]:
        entry = next((e for c, e in self._view_state(txn).items() if _same(rel, c)), None)
        return entry[1] if entry else None

    def items(self) -> Dict[str, Path]:
        return {rel: src for rel, (_, src) in self.state.items()}

    def drop(self, rel: str) -> None:
        """For the secret guard, after the transactions."""
        assert self.docs._open is None
        self.state.pop(rel, None)


@dataclass
class _Block:
    intro: str
    items: List[str] = field(default_factory=list)


@dataclass
class _Section:
    """A section of "To do": blocks of an intro and its items, in the order they first appear."""

    title: str
    blocks: List[_Block] = field(default_factory=list)

    @property
    def items(self) -> List[str]:
        return [i for b in self.blocks for i in b.items]


@dataclass
class _ReportState:
    carried_rows: List[Tuple[str, str]] = field(default_factory=list)
    sections: Dict[str, _Section] = field(default_factory=dict)  # in the order they first appear
    blockers: List[str] = field(default_factory=list)


_SHELL_SECTIONS = {f"Shell lines: {c}" for c, _ in rcparse.CATEGORIES}


class ReportDoc(_Doc):
    """MIGRATION.md: what was carried over, and the sections of "To do". report.render() writes it."""

    def __init__(self, docs: Docs):
        super().__init__(docs, "MIGRATION.md")

    def _empty(self):
        return _ReportState()

    def carried(self, txn: Txn, what: str, where: str) -> None:
        self._w(txn).carried_rows.append((what, where))

    def todo(self, txn: Txn, title: str, intro: str, items: List[str]) -> None:
        """Add to the block of the section with the same intro, or start a new block. Items are not repeated."""
        if title not in ORDER and title not in _SHELL_SECTIONS:
            raise ValueError(f"not a section of the report: {title!r}")
        w = self._w(txn)
        sec = w.sections.get(title)
        if sec is None:
            sec = w.sections[title] = _Section(title)
        block = next((b for b in sec.blocks if b.intro == intro), None)
        if block is None:
            block = _Block(intro)
            sec.blocks.append(block)
        for i in items:
            if i not in block.items:
                block.items.append(i)

    def blocker(self, txn: Txn, msg: str) -> None:
        w = self._w(txn)
        if msg not in w.blockers:
            w.blockers.append(msg)

    @property
    def carried_rows(self) -> List[Tuple[str, str]]:
        return self.state.carried_rows

    @property
    def sections(self) -> Dict[str, _Section]:
        return self.state.sections

    @property
    def blockers(self) -> List[str]:
        return self.state.blockers

    @property
    def count(self) -> int:
        """The number of items to check, without the lines that dotkit does already."""
        return sum(max(1, len(s.items)) for t, s in self.sections.items() if t != "Shell lines: Done by dotkit")


# --- Views: what a transaction offers


class ListView:
    def __init__(self, txn: Txn, doc: ListDoc):
        self._txn, self._doc = txn, doc

    def add(self, entry: str, comment: str = "") -> bool:
        return self._doc.add(self._txn, entry, comment)

    def names(self) -> List[str]:
        return self._doc.names(self._txn)

    def added(self) -> List[str]:
        return [line for _, _, line, _ in self._doc._view_state(self._txn).entries]


class MiseView:
    def __init__(self, txn: Txn, doc: MiseDoc):
        self._txn, self._doc = txn, doc

    def add_tool(self, key: str, value: object, comment: str = "") -> bool:
        return self._doc.add_tool(self._txn, key, value, comment)

    def has_tool(self, key: str) -> bool:
        return self._doc.has_tool(self._txn, key)

    def set_base(self, text: str) -> bool:
        return self._doc.set_base(self._txn, text)

    def set_setting(self, key: str, value: object) -> bool:
        return self._doc.set_setting(self._txn, key, value)

    def replace_setting(self, key: str, value: object, expect: object) -> bool:
        return self._doc.replace_setting(self._txn, key, value, expect)

    def added(self) -> List[str]:
        return [k for _, k, _, _ in self._doc._view_state(self._txn).tools]


class LinksView:
    def __init__(self, txn: Txn, doc: LinksDoc):
        self._txn, self._doc = txn, doc

    def link(self, target: str, source: Optional[str]) -> bool:
        return self._doc.link(self._txn, target, source)


class ShellRcView:
    def __init__(self, txn: Txn, doc: ShellRcDoc):
        self._txn, self._doc = txn, doc

    @property
    def path(self) -> str:
        return self._doc.path

    @property
    def local(self) -> str:
        return self._doc.local

    def add_part(self, header: str, text: str) -> bool:
        return self._doc.add_part(self._txn, header, text)

    def add_secret(self, text: str) -> bool:
        return self._doc.add_secret(self._txn, text)

    def ensure(self) -> None:
        self._doc.ensure(self._txn)


class ShellNixView:
    def __init__(self, txn: Txn, doc: ShellNixDoc):
        self._txn, self._doc = txn, doc

    def set_keymap(self, value: str) -> bool:
        return self._doc.set_keymap(self._txn, value)


class FileView:
    def __init__(self, txn: Txn, doc: FileDoc):
        self._txn, self._doc = txn, doc

    def set(self, text: str) -> bool:
        return self._doc.set(self._txn, text)


class CopiesView:
    def __init__(self, txn: Txn, doc: Copies):
        self._txn, self._doc = txn, doc

    def add(self, rel: str, src: Path) -> bool:
        """False when the repository path is taken: by an earlier copy or a file that a document writes,
        inside a copied directory, or when a copy there would contain one of them or local/."""
        return self._doc.add(self._txn, rel, src)

    def taken(self, rel: str) -> Optional[Tuple[str, str]]:
        """Why the repository path is taken, or None: see Copies.taken."""
        return self._doc.taken(self._txn, rel)

    def source(self, rel: str) -> Optional[Path]:
        """What is copied to the repository path, or None."""
        return self._doc.source(self._txn, rel)


class ReportView:
    def __init__(self, txn: Txn, doc: ReportDoc):
        self._txn, self._doc = txn, doc

    def carried(self, what: str, where: str) -> None:
        self._doc.carried(self._txn, what, where)

    def todo(self, section: str, intro: str, items: List[str]) -> None:
        self._doc.todo(self._txn, section, intro, items)

    def blocker(self, msg: str) -> None:
        self._doc.blocker(self._txn, msg)


# --- Docs and Txn

_MISE = "config/mise.toml"
_HOME_NIX = "nix/home.nix"
_SHELL_NIX = "nix/shell.nix"


def _document_paths() -> set:
    """The paths of the documents other than whole files, and the files that the writer adds."""
    return set(ListDoc.KINDS) | {_MISE, _HOME_NIX, _SHELL_NIX, "MIGRATION.md", ".gitignore"} | \
        {repo_file for _, repo_file in base_mod.SHELL_LAYERS.values()}


class Docs:
    """The documents of one run. mode: "setup" (base text from the template) or "adopt" (from the repository)."""

    def __init__(self, mode: str, template: Path, repo: Optional[Path], home: str = "", host: str = "",
                 strict: bool = False):
        assert mode in ("setup", "adopt")
        assert repo is not None, "the documents need the repository path"
        self.mode, self.template, self.repo = mode, template, repo
        self.home, self.host, self.strict = home, host, strict
        self.conflicts: List[Conflict] = []
        self._docs: Dict[str, _Doc] = {}
        self._open: Optional[Txn] = None
        self.copies = Copies(self, "(copies)")
        self.report = ReportDoc(self)
        # The files of the template are in every new repository, so their paths are taken.
        self.template_files: List[str] = sorted(
            Path(root, name).relative_to(template).as_posix()
            for root, _dirs, names in os.walk(template) for name in names)

    def base_text(self, rel: str) -> str:
        """The text that a document starts from: the template's file in setup, the repository's in adopt."""
        root = self.template if self.mode == "setup" else self.repo
        try:
            return (root / rel).read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            return ""

    def begin(self, owner: str) -> Txn:
        assert self._open is None, f"a transaction is open already: {self._open.owner}"
        self._open = Txn(self, owner)
        return self._open

    def _conflict(self, c: Conflict) -> bool:
        self.conflicts.append(c)
        if self.strict:
            raise ConflictError(f"{c.path}: {c.second} sets {c.key} to {c.refused}, and {c.first} has {c.kept}")
        return False

    def _get(self, path: str, cls, make):
        doc = self._docs.get(path)
        if doc is None:
            doc = self._docs[path] = make()
        elif type(doc) is not cls:
            raise ValueError(f"{path} belongs to another document")
        return doc

    def _setup_only(self, what: str) -> None:
        if self.mode != "setup":
            raise ValueError(f"{what} is for setup only")

    def _renders(self, txn: Txn, rel: str) -> bool:
        """The path is taken by a document, a template file, or local/ (the directory or a path in it)."""
        key = _parts(rel)
        if key[:1] == (_key("local"),):
            return True
        return any(_parts(p) == key for p in self._claimed(txn))

    def _claimed(self, txn: Txn) -> List[str]:
        """The taken paths other than copies: the fixed document paths, nix/user.nix, the template's
        files, and the whole files with text."""
        fixed = sorted(_document_paths() | {"nix/user.nix"} | set(self.template_files))
        return fixed + [p for p, d in self._docs.items() if isinstance(d, FileDoc) and "text" in d._view_state(txn)]

    def keymap(self) -> str:
        """The zsh keymap that core set in nix/shell.nix: emacs when it set none."""
        d = self._docs.get(_SHELL_NIX)
        return d.keymap if isinstance(d, ShellNixDoc) else "emacs"

    # The documents, for the renders and the guards after the transactions.

    def get(self, path: str) -> Optional[_Doc]:
        return self._docs.get(path)

    def drop(self, rel: str) -> None:
        """A copy that the secret guard rejects: no copy, no file, no link to it."""
        self.copies.drop(rel)
        doc = self._docs.get(rel)
        if isinstance(doc, FileDoc):
            doc.state = {}
        links = self._docs.get(_HOME_NIX)
        if isinstance(links, LinksDoc):
            links.drop_source(rel)

    def links(self) -> Dict[str, Optional[str]]:
        doc = self._docs.get(_HOME_NIX)
        return doc.links() if isinstance(doc, LinksDoc) else {}

    def render(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """The tracked files and the local/ files of the committed documents.

        Order: the shell files, the whole files, the lists, nix/shell.nix,
        config/mise.toml, nix/home.nix. A mise config that would not parse
        is the base text, and its tools go to the report.
        """
        assert self._open is None, "a transaction is open"
        files: Dict[str, str] = {}
        local: Dict[str, str] = {}
        docs = list(self._docs.values())
        for main in base_mod.SHELL_LAYERS:
            for d in docs:
                if isinstance(d, ShellRcDoc) and d.main == main:
                    text, secret = d.render()
                    if secret is not None:
                        local[d.local] = secret
                    if text is not None:
                        files[d.path] = text
        for d in docs:
            if isinstance(d, FileDoc) and d.render() is not None:
                files[d.path] = d.render()
        for path in ListDoc.KINDS:
            d = self._docs.get(path)
            if d is not None and d.render() is not None:
                files[path] = d.render()
        d = self._docs.get(_SHELL_NIX)
        if d is not None and d.render() is not None:
            files[_SHELL_NIX] = d.render()
        d = self._docs.get(_MISE)
        if isinstance(d, MiseDoc):
            text, error = d.render()
            if error is not None:
                t = self.begin("render")
                t.report.todo("mise", f"config/mise.toml would not parse ({error}), so it has only the template. "
                                      "Add these tools by hand:",
                              [f"`{line}`" for line in d.tool_lines() if not line.startswith("#")])
                t.commit()
            if text != d.original:
                files[_MISE] = text
        d = self._docs.get(_HOME_NIX)
        if d is not None and d.render() is not None:
            files[_HOME_NIX] = d.render()
        return files, local


class Txn:
    """The changes of one owner. Each accessor returns a view on a document."""

    def __init__(self, docs: Docs, owner: str):
        self.docs, self.owner = docs, owner
        self._touched: List[_Doc] = []
        self._conflicts_at_begin = len(docs.conflicts)
        self.copies = CopiesView(self, docs.copies)
        self.report = ReportView(self, docs.report)

    def list(self, path: str) -> ListView:
        if path not in ListDoc.KINDS:
            raise ValueError(f"not a list file: {path}")
        return ListView(self, self.docs._get(path, ListDoc, lambda: ListDoc(self.docs, path, self.docs.base_text(path))))

    def mise(self) -> MiseView:
        d = self.docs
        return MiseView(self, d._get(_MISE, MiseDoc, lambda: MiseDoc(d, _MISE, d.base_text(_MISE))))

    def links(self) -> LinksView:
        d = self.docs
        d._setup_only("links")
        return LinksView(self, d._get(_HOME_NIX, LinksDoc, lambda: LinksDoc(d, _HOME_NIX, d.base_text(_HOME_NIX))))

    def shell_rc(self, main: str) -> ShellRcView:
        d = self.docs
        d._setup_only("shell_rc")
        if main not in base_mod.SHELL_LAYERS:
            raise ValueError(f"not a shell file of the base: {main!r}")
        return ShellRcView(self, d._get(base_mod.SHELL_LAYERS[main][1], ShellRcDoc, lambda: ShellRcDoc(d, main)))

    def shell_nix(self) -> ShellNixView:
        d = self.docs
        d._setup_only("shell_nix")
        assert self.owner == "core", "the keymap is written by the core"
        return ShellNixView(self, d._get(_SHELL_NIX, ShellNixDoc,
                                         lambda: ShellNixDoc(d, _SHELL_NIX, d.base_text(_SHELL_NIX))))

    def file(self, path: str) -> FileView:
        d = self.docs
        d._setup_only("file")
        _check_rel(path, "a file")
        if path in _document_paths() or path.startswith("local/"):
            raise ValueError(f"{path} belongs to another document")
        return FileView(self, d._get(path, FileDoc, lambda: FileDoc(d, path)))

    def commit(self) -> None:
        assert self.docs._open is self, "the transaction is not open"
        for doc in self._touched:
            assert doc.work is not None
            doc.state, doc.work = doc.work, None
        self._touched = []
        self.docs._open = None

    def rollback(self) -> None:
        assert self.docs._open is self, "the transaction is not open"
        for doc in self._touched:
            doc.work = None
        del self.docs.conflicts[self._conflicts_at_begin:]
        self._touched = []
        self.docs._open = None
