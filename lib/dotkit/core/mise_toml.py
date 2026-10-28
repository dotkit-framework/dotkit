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


def tools(path: Path) -> Dict[str, object]:
    """The [tools] table, key -> value, in the order of the file. {} when the file does not exist."""
    try:
        data = tomllib.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return {}
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise InvalidToml(f"{path} is not valid TOML: {e}.") from None
    table = data.get("tools")
    return table if isinstance(table, dict) else {}


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


def inline(value: object) -> str:
    """A value as TOML on one line: "lts", { version = "latest", minimum_release_age = "1d" }, ["22", "20"]."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(inline(v) for v in value) + "]"
    if isinstance(value, dict):
        def key(k: str) -> str:
            return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k, ensure_ascii=False)
        return "{ " + ", ".join(f"{key(k)} = {inline(v)}" for k, v in value.items()) + " }" if value else "{}"
    return json.dumps(value, default=str)
