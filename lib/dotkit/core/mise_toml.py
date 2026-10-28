"""The tools of a mise config file: the [tools] table, read with a TOML parser, as mise reads it.

mise reads config/mise.toml of the base and of the dotfiles repository (nix/home.nix links them), so a
file that is not valid TOML stops mise too. tools() raises InvalidToml for it, with the path in the
text. The dotkit command and the migration wizard both use this module.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 and 3.10: tomli of dotkit/_vendor, the same parser.
    import tomli as tomllib


class InvalidToml(ValueError):
    pass


def _table(path: Path, name: str) -> Dict[str, object]:
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return {}
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise InvalidToml(f"{path} is not valid TOML: {e}.") from None
    table = data.get(name)
    return table if isinstance(table, dict) else {}


def tools(path: Path) -> Dict[str, object]:
    """The [tools] table, key -> value, in the order of the file. {} when the file does not exist."""
    return _table(path, "tools")


def settings(path: Path) -> Dict[str, object]:
    """The [settings] table. {} when the file does not exist."""
    return _table(path, "settings")


def version_text(value: object) -> str:
    """The version of a tool for `dotkit list`: "lts" for "lts", "1.2" for { version = "1.2", ... },
    "22, 20" for ["22", "20"]. Other values as compact JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("version"), str):
        return value["version"]
    if isinstance(value, list):
        return ", ".join(version_text(v) for v in value)
    return json.dumps(value, separators=(", ", ": "), default=str)


def short(key: str) -> str:
    """The tool name of a key, after its last `:` or `/`: github:modem-dev/hunk -> hunk."""
    return re.split(r"[:/]", key)[-1]


_TOML_ESCAPES = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r", '"': '\\"', "\\": "\\\\"}


def toml_key(s: str) -> str:
    """A TOML basic string: a quoted key, or a string value."""
    return '"' + "".join(_TOML_ESCAPES.get(c, c if c >= " " and c != "\x7f" else f"\\u{ord(c):04x}")
                         for c in s) + '"'


def bare_or_quoted(key: str) -> str:
    """A TOML key: bare when it can be, else quoted."""
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else toml_key(key)


def toml_value(v: object) -> str:
    """A value as TOML on one line: "lts", { version = "latest", minimum_release_age = "1d" }, ["22", "20"].
    The migration writes the values that docs._check_value accepts; another value (a date of a TOML file)
    becomes its text."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return toml_key(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{bare_or_quoted(k)} = {toml_value(x)}" for k, x in v.items()) + " }" if v else "{}"
    return toml_key(str(v))
