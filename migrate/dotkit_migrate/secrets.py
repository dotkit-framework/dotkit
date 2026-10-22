"""Find tokens, passwords, and keys in text, so they stay out of the repository.

The findings have the line number and a name, never the value. The rules
err toward "secret": a false alarm moves a line to local/ or keeps a file in
place, and a miss would commit a secret. Comments are checked too.
"""

from __future__ import annotations

import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

# Values that are secrets whatever their name.
_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("a GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("an npm token", re.compile(r"\bnpm_[A-Za-z0-9]{30,}")),
    ("a Slack webhook", re.compile(r"https://hooks\.slack\.com/services/[^\s\'\"]+")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("a Stripe key", re.compile(r"\b[rsp]k_(live|test)_[A-Za-z0-9]{10,}")),
    ("an API key", re.compile(r"\bsk-(ant-|proj-)?[A-Za-z0-9_-]{20,}")),
    ("an AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("a JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.")),
    ("a password in a URL", re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:@'\"]+:[^\s/@'\"$]+@")),
    ("a user and password", re.compile(r"(^|\s)(-u|--user|--password)[\s=]+['\"]?[^\s'\"$:]+:[^\s'\"$]+")),
    ("an authorization header", re.compile(
        r"(?i)\b(authorization:\s*)?(bearer|basic|token)\s+[A-Za-z0-9+/=_.\-]{20,}")),
    ("a key header", re.compile(
        r"(?i)\b(x-api-key|api-key|apikey|x-auth-token|x-access-token|private-token|x-token|x-secret|"
        r"authorization)\s*:\s*['\"]?[^\s'\"$]{6,}")),
    # A classic GitHub token and other hex keys.
    ("a 40-character hex string", re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{40}(?![0-9a-fA-F])")),
)

# Names of settings and variables that hold secrets, after - becomes _ and
# without a prefix before : or . (//registry/:_authToken, http.extraheader).
_SECRET_NAME = re.compile(
    r"(TOKEN|SECRET|PASSW(OR)?D|PASSPHRASE|PASS$|^PAT$|_PAT$|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|_KEY$|^KEY$|"
    r"PWD$|WEBHOOK|CREDENTIAL|^_?AUTH$|_AUTH$|AUTHTOKEN|SESSION_?ID|COOKIE)"
)

# NAME=value or name = value or name:value, anywhere in a line: after
# export, typeset -x, local, { or ;, inside an alias value, in a comment.
# Names may be quoted, as in JSON config.
_ASSIGNMENTS = re.compile(
    r"""(?<![\w$.])(?P<name>[A-Za-z_][\w.\-]*)["']?(?P<sep>\s*=\s*|:\s*)"""
    r"""(?P<value>"[^"]*"?|'[^']*'?|[^\s;&|)}'"]+)"""
)

# Config directives and CLI options with a space rather than =. The
# lookahead allows finding a setting inside another word's value (an alias).
_SETTINGS = re.compile(
    r"""(?i)(?=(?<![\w$.])(?P<name>--[\w-]+|password|passwd|pass|secret|token)\s+"""
    r"""(?P<value>"[^"]*"?|'[^']*'?|[^\s;&|)}'"]+))"""
)
_SSHPASS = re.compile(r"""\bsshpass\s+-p\s*(["'][^"']*["']|[^\s;&|)}'"]+)""")

# Allow only the hash value in a revision field or URL, never the rest of
# the line: a neighboring token still needs all the normal secret checks.
_HEX_REV = r"[0-9a-fA-F]{40}(?![0-9a-fA-F])"
_REV_FIELD = re.compile(
    rf"""(?i)(?<![\w.-])["']?(?:commit|rev|sha|hash|revision)["']?\s*[:=]\s*["']?({_HEX_REV})"""
)
_REV_URL = re.compile(
    rf"""[a-z][a-z0-9+.-]*://[^\s'"`<>]*?(?:[#@]|[?&]rev=)({_HEX_REV})"""
)


def _without_revisions(line: str) -> str:
    spans = [m.span(1) for pattern in (_REV_FIELD, _REV_URL) for m in pattern.finditer(line)]
    for start, end in sorted(set(spans), reverse=True):
        line = line[:start] + "revision" + line[end:]
    return line


# A value that only refers to something else: a variable, a command, a
# keychain lookup. It holds no secret itself.
_REFERENCE = re.compile(r"""^["']?(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$\(.*\)?|`.*`?)["']?$""")

# Long random-looking words: the last check of generated files.
_WORD = re.compile(r"[A-Za-z0-9+_]{24,}")
_PUBLIC_KEY = re.compile(r"\b(ssh-(ed25519|rsa|dss)|ecdsa-sha2-\S+|sk-ssh-ed25519@\S+) AAAA")

# Files that hold login tokens. They are never copied into the repository.
SECRET_FILES = (
    ".config/gh/hosts.yml",
    ".netrc",
    ".config/github-copilot",
    ".config/gcloud",
    ".config/configstore",
    ".config/op",
    ".docker/config.json",
    ".aws/credentials",
    ".gem/credentials",
    ".pypirc",
)


@dataclass(frozen=True)
class Secret:
    line: int
    what: str  # for example "GITHUB_TOKEN" or "a GitHub token"


def secret_name(name: str) -> bool:
    n = re.split(r"[:.]", name.upper().replace("-", "_"))[-1]
    return bool(_SECRET_NAME.search(n))


def _literal(value: str) -> bool:
    """True when a value holds text of its own, not only a reference."""
    v = value.strip().rstrip(";")
    if _REFERENCE.match(v):
        return False
    inner = v.strip("\"'")
    if len(inner) < 6:
        return False
    # A path, for example PASSWORD_STORE_DIR=~/.password-store.
    if inner.startswith(("/", "~/", "$HOME/", "${HOME}/")):
        return False
    # Only variables and commands, for example "$USER:$PASS".
    if not re.sub(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$\([^)]*\)|[\s:@/\"']", "", inner):
        return False
    lower = inner.lower()
    return lower not in {"true", "false", "none", "null", "keychain", "osxkeychain", "store", "cache", "manager",
                         "manager-core", "required", "optional"}


def _entropy(word: str) -> float:
    counts = {c: word.count(c) for c in set(word)}
    return -sum(n / len(word) * math.log2(n / len(word)) for n in counts.values())


def random_word(line: str) -> bool:
    """True when a line has a long word that looks random: letters and digits, not hex, high entropy."""
    if _PUBLIC_KEY.search(line):
        return False
    for w in _WORD.findall(line):
        # Random tokens mix upper case, lower case, and digits; names,
        # host names, and hex hashes do not.
        if not (re.search(r"\d", w) and re.search(r"[a-z]", w) and re.search(r"[A-Z]", w)):
            continue
        if _entropy(w) >= 3.7:
            return True
    return False


def line_secret(line: str, strict: bool = True) -> str | None:
    """What secret a line holds, or None. strict also flags random-looking words."""
    if not line.strip():
        return None
    line = _without_revisions(line)
    for what, pattern in _VALUE_PATTERNS:
        if pattern.search(line):
            return what
    # The line, and the text in its quotes: an alias value has assignments too.
    parts = [line] + [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", line)]
    for part in parts:
        for m in _ASSIGNMENTS.finditer(part):
            name = m.group("name")
            if secret_name(name) and _literal(m.group("value")):
                return name
    for m in _SETTINGS.finditer(line):
        name = m.group("name").lstrip("-")
        if secret_name(name) and _literal(m.group("value")):
            return name
    for m in _SSHPASS.finditer(line):
        if _literal(m.group(1)):
            return "an sshpass password"
    if strict and random_word(line):
        return "a random-looking value"
    return None


def logical_lines(text: str):
    """(first line, last line, text), joining backslash continuations for checking only."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        start, line = i, lines[i]
        while line.endswith("\\") and i + 1 < len(lines):
            i += 1
            line = line[:-1] + lines[i]
        yield start + 1, i + 1, line
        i += 1


def scan_text(text: str, strict: bool = True) -> list[Secret]:
    out = []
    for start, _end, line in logical_lines(text):
        what = line_secret(line, strict)
        if what:
            out.append(Secret(start, what))
    return out


class Unscannable(Exception):
    """A file that cannot be checked: too large, binary, unreadable, or not a regular file."""


def scan_file(path: Path, limit: int = 1_000_000, strict: bool = True) -> list[Secret]:
    """Secrets in a text file. Raises Unscannable when the file cannot be checked."""
    try:
        st = os.lstat(path)
    except OSError as e:
        raise Unscannable(f"cannot be read: {e.strerror}") from e
    if stat.S_ISLNK(st.st_mode):
        try:
            st = os.stat(path)
        except OSError as e:
            raise Unscannable("a broken link") from e
    if not stat.S_ISREG(st.st_mode):
        raise Unscannable("not a regular file")
    if st.st_size > limit:
        raise Unscannable("larger than 1 MB")
    try:
        with open(path, "rb") as f:
            data = f.read(limit + 1)
    except OSError as e:
        raise Unscannable(f"cannot be read: {e.strerror}") from e
    if b"\0" in data:
        raise Unscannable("a binary file")
    return scan_text(data.decode("utf-8", errors="replace"), strict)


def has_secret(path: Path) -> bool:
    """True when a file has a secret or cannot be checked."""
    try:
        return bool(scan_file(path))
    except Unscannable:
        return True


def known_secret_file(rel: str) -> bool:
    """True for files and directories that hold login tokens by design."""
    return any(rel == s or rel.startswith(s + "/") for s in SECRET_FILES)
