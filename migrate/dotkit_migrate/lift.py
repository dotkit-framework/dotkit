"""Move the secrets of a config file to variables of ~/.secrets: pure functions, no I/O.

npmrc() takes the registries and tokens of ~/.npmrc. npm expands ${NAME} in
its config, so each token becomes a variable. gitconfig() takes the settings
with a secret out of a Git config file. Git cannot expand a variable in its
config, but it reads settings from GIT_CONFIG_COUNT, GIT_CONFIG_KEY_<n>, and
GIT_CONFIG_VALUE_<n>, so the migration exports them in ~/.secrets. Both
return None when a secret is in a place that they cannot move: the file
then stays out of the repository, as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import secrets

# --- npm

# Settings of a registry: //host/path/:<key>. A token at the top level is for the default registry.
_NPM_AUTH = ("_authToken", "_auth", "_password", "username", "email", "certfile", "keyfile")
_NPM_SECRET = ("_authToken", "_auth", "_password")
_NPM_LINE = re.compile(r"^\s*(?P<key>[^=\s][^=]*?)\s*=\s*(?P<value>.*?)\s*$")
_NPM_SCOPED = re.compile(r"^//(?P<host>[^/:\s]+)[^\s]*:(?P<name>[^:/\s]+)$")
_REFERENCE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")

# Variable names for the tokens of these registries; others get the host name.
NPM_HOSTS = {"registry.npmjs.org": "NPM", "npm.pkg.github.com": "GITHUB_NPM"}


@dataclass
class NpmSetting:
    key: str  # for example //npm.pkg.github.com/:_authToken or @acme:registry
    value: str
    # The variable for a literal secret, before Txn.secrets().free_name; "" for a value that is carried as it is.
    var: str = ""


@dataclass
class NpmTokens:
    settings: List[NpmSetting]  # the registries and their tokens, in the order of the file
    other: List[str]  # the keys of the other settings, which are not carried


def _var_base(host: str) -> str:
    return NPM_HOSTS.get(host) or re.sub(r"[^A-Z0-9]+", "_", host.upper()).strip("_") + "_NPM"


def _unquote_ini(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def npmrc(text: str) -> Optional[NpmTokens]:
    """The registries and tokens of an .npmrc, or None when a secret is in another setting."""
    settings: List[NpmSetting] = []
    other: List[str] = []
    registry: Optional[NpmSetting] = None
    hosts = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in "#;":
            continue  # a comment, also one with a secret: not carried
        m = _NPM_LINE.match(line)
        if m is None:
            if secrets.line_secret(line):
                return None
            continue
        key, value = m.group("key"), _unquote_ini(m.group("value"))
        scoped = _NPM_SCOPED.match(key)
        name = scoped.group("name") if scoped else key
        if name in _NPM_AUTH and (scoped or name in _NPM_SECRET):
            host = scoped.group("host") if scoped else "registry.npmjs.org"
            hosts.add(host)
            secret = name in _NPM_SECRET or secrets.line_secret(line) is not None
            var = ""
            if secret and value and not _REFERENCE.match(value):
                suffix = {"_authToken": "TOKEN", "_auth": "AUTH", "_password": "PASSWORD"}.get(name) or \
                    re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
                var = f"{_var_base(host)}_{suffix}"
            settings.append(NpmSetting(key, value, var))
        elif re.fullmatch(r"@[^:\s]+:registry", key):
            if secrets.line_secret(line):
                return None
            settings.append(NpmSetting(key, value))
        elif key == "registry":
            if secrets.line_secret(line):
                return None
            registry = NpmSetting(key, value)
        elif secrets.line_secret(line):
            return None
        else:
            other.append(key)
    # The default registry, when the file has a token for it.
    if registry is not None:
        host = re.sub(r"^[a-z]+://", "", registry.value).split("/")[0].split(":")[0]
        if host in hosts:
            settings.insert(0, registry)
        else:
            other.append("registry")
    return NpmTokens(settings, other)


# --- Git

_GIT_HEADER = re.compile(r'^\s*\[\s*(?P<section>[A-Za-z0-9.-]+)(?:\s+"(?P<sub>(?:[^"\\]|\\.)*)")?\s*\]\s*([#;].*)?$')
_GIT_KEY = re.compile(r"^\s*(?P<key>[A-Za-z][A-Za-z0-9-]*)\s*(?:=(?P<value>.*))?$")


def _git_value(raw: str) -> Optional[str]:
    """The value of a Git config line: quotes, escapes, and a comment outside quotes. None when it continues."""
    out, quoted, i, pending = [], False, 0, ""
    raw = raw.strip()
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            if i + 1 >= len(raw):
                return None  # the value continues on the next line
            n = raw[i + 1]
            esc = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "b": "\b"}.get(n)
            if esc is None:
                return None
            out.append(pending + esc)
            pending = ""
            i += 2
            continue
        if c == '"':
            quoted = not quoted
            out.append(pending)
            pending = ""
        elif not quoted and c in "#;":
            break
        elif not quoted and c in " \t":
            pending += c  # kept only when more of the value follows
        else:
            out.append(pending + c)
            pending = ""
        i += 1
    if quoted:
        return None
    return "".join(out)


def _sub(raw: str) -> str:
    return re.sub(r"\\(.)", r"\1", raw)


def gitconfig(text: str) -> Optional[Tuple[str, List[Tuple[str, str]]]]:
    """The text without its settings with a secret, and those settings as (key, value) for GIT_CONFIG_KEY/VALUE.

    A section whose header has the secret, such as [url "https://<token>@github.com/"],
    goes as a whole. A comment with a secret is dropped. None when a secret
    is in a line that this cannot read, for example a value that continues
    on the next line.
    """
    rows = text.splitlines()
    keep = [True] * len(rows)
    taken: List[Tuple[str, str]] = []
    section: Optional[str] = None  # "url.https://x@github.com/"
    drop_section = False
    for i, row in enumerate(rows):
        secret = secrets.line_secret(row) is not None
        h = _GIT_HEADER.match(row)
        if h:
            sub = h.group("sub")
            section = h.group("section") + (f".{_sub(sub)}" if sub is not None else "")
            drop_section = secret
            keep[i] = not secret
            continue
        stripped = row.strip()
        if not stripped or stripped[0] in "#;":
            if drop_section or secret:
                keep[i] = False
            continue
        k = _GIT_KEY.match(row)
        if not (drop_section or secret):
            if row.rstrip().endswith("\\") and any(secrets.line_secret(r) for r in rows[i + 1:i + 2]):
                return None  # a value that continues into a line with a secret
            continue
        if k is None or section is None:
            return None
        value = "true" if k.group("value") is None else _git_value(k.group("value"))
        if value is None:
            return None
        taken.append((f"{section}.{k.group('key')}", value))
        keep[i] = False
    out = "\n".join(r for r, k in zip(rows, keep) if k)
    return (out + "\n" if out else ""), taken
