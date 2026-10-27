"""Version order: `sort -V` of macOS (PARITY.md, R17).

The sort of macOS 26 (2.3-Apple) orders versions with the older filevercmp
of gnulib: a file suffix such as `.tar.gz` counts only to break a tie, runs
of digits compare as numbers, `~` sorts before the end of the text, letters
before other characters. When two versions are equal by these rules, sort
compares the bytes. tests/test_core.py checks this against /usr/bin/sort.
"""

from __future__ import annotations


def _isalpha(c: str) -> bool:
    return ("a" <= c <= "z") or ("A" <= c <= "Z")


def _isdigit(c: str) -> bool:
    return "0" <= c <= "9"


def _suffix_start(s: str) -> int:
    """Where the file suffix starts, (\\.[A-Za-z~][A-Za-z0-9~]*)*$; len(s) without one."""
    match = -1
    read_alpha = False
    for i, c in enumerate(s):
        if read_alpha:
            read_alpha = False
            if not _isalpha(c) and c != "~":
                match = -1
        elif c == ".":
            read_alpha = True
            if match < 0:
                match = i
        elif not (_isalpha(c) or _isdigit(c)) and c != "~":
            match = -1
    return len(s) if match < 0 else match


def _order(c: str) -> int:
    if _isdigit(c):
        return 0
    if _isalpha(c):
        return ord(c)
    if c == "~":
        return -1
    return ord(c) + 256


def _verrevcmp(a: str, b: str) -> int:
    i = j = 0
    while i < len(a) or j < len(b):
        first_diff = 0
        while (i < len(a) and not _isdigit(a[i])) or (j < len(b) and not _isdigit(b[j])):
            ca = 0 if i == len(a) else _order(a[i])
            cb = 0 if j == len(b) else _order(b[j])
            if ca != cb:
                return ca - cb
            i += 1
            j += 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while i < len(a) and j < len(b) and _isdigit(a[i]) and _isdigit(b[j]):
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and _isdigit(a[i]):
            return 1
        if j < len(b) and _isdigit(b[j]):
            return -1
        if first_diff:
            return first_diff
    return 0


def _bytes_cmp(a: str, b: str) -> int:
    x, y = a.encode("utf-8", "surrogateescape"), b.encode("utf-8", "surrogateescape")
    return (x > y) - (x < y)


def compare(a: str, b: str) -> int:
    """Below 0 when a sorts before b with `sort -V`, 0 when they are the same, above 0 after."""
    simple = _bytes_cmp(a, b)
    if simple == 0:
        return 0
    if a == "":
        return -1
    if b == "":
        return 1
    for special in (".", ".."):
        if a == special:
            return -1
        if b == special:
            return 1
    if a[0] == "." and b[0] != ".":
        return -1
    if a[0] != "." and b[0] == ".":
        return 1
    if a[0] == "." and b[0] == ".":
        a, b = a[1:], b[1:]
    na, nb = _suffix_start(a), _suffix_start(b)
    if (na < len(a) or nb < len(b)) and na == nb and a[:na] == b[:nb]:
        na, nb = len(a), len(b)
    result = _verrevcmp(a[:na], b[:nb])
    return result if result else simple


def version_ge(a: str, b: str) -> bool:
    """True when version a is the same as or newer than version b; a leading v does not count."""
    a = a[1:] if a.startswith("v") else a
    b = b[1:] if b.startswith("v") else b
    return a == b or compare(a, b) >= 0
