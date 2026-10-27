"""core/lists.py, core/mise_toml.py, and core/versions.py against the awk programs and the sort that they replace.

The awk programs are copies of the ones in scripts/dotkit-pkg, so that these
tests keep their reference after the Bash scripts are gone. /usr/bin/awk and
/usr/bin/sort are the tools of macOS; without them the comparisons skip.
"""

from __future__ import annotations

import os
import random
import subprocess
import unittest
from pathlib import Path

from dotkit.core import lists, mise_toml, versions
from tests import fake

AWK = "/usr/bin/awk"
SORT = "/usr/bin/sort"
BASH = "/bin/bash"

LIST_NAMES = '{ sub(/#.*/, "") } NF { print $1 }'
DELETE = '{ line = $0; sub(/#.*/, "", line); split(line, w) } w[1] == e { next } { print }'
MISE_LIST = r'''
  /^\[/ { on = ($0 == "[tools]"); next }
  on && /^[^#[:space:]][^=]*=/ {
    key = $0; sub(/[[:space:]]*=.*/, "", key); gsub(/"/, "", key)
    v = $0; sub(/^[^=]*=[[:space:]]*/, "", v)
    if (v ~ /^\{/) { if (match(v, /version = "[^"]*"/)) v = substr(v, RSTART + 11, RLENGTH - 12) }
    gsub(/"/, "", v)
    printf "%s\t%s\n", key, v
  }
'''
MISE_KEYS = r'''
  /^\[/ { on = ($0 == "[tools]"); next }
  on && /^[^#[:space:]][^=]*=/ {
    key = $0; sub(/[[:space:]]*=.*/, "", key); gsub(/"/, "", key)
    last = key; sub(/.*[:\/]/, "", last)
    printf "%s\t%s\n", key, last
  }
'''
MAS_ID_OF = r'''
mas_id_of() {
  local name=$1 file=$2
  shift 2
  {
    awk '{ sub(/^[[:space:]]+/, ""); sub(/[[:space:]]+$/, "") } /^[0-9]+[[:space:]]/ { print }' "$file"
    local e
    for e in "$@"; do [[ $e != mas:* ]] || printf '%s\n' "${e#mas:}"; done
  } | N=$name awk '{ id = $1; sub(/^[0-9]+[[:space:]]+/, "") } $0 == ENVIRON["N"] && !f { print id; f = 1 }'
}
mas_id_of "$@"
'''
APPEND = r'''
[[ ! -s $1 || -z $(tail -c 1 "$1") ]] || printf '\n' >>"$1"
printf '%s\n' "$2" >>"$1"
'''

LIST_FILES = [
    "",
    "\n",
    "\n\n",
    "jq\n",
    "jq",
    "# comment\njq\n",
    "  jq  # spaced\n\tfd\tx\n",
    "jq#nospace\n#only\n   \n",
    "a b c\nlast-no-newline",
    "crlf\r\nnext\r\n",
    "ünïcode\n\xff\xfe-bytes\n",
    "# jq\njq # one\n  jq\njqq\nx jq\n",
]

MISE_FILES = [
    "",
    '[tools]\nnode = "lts"\n',
    '# c\n[tools]\n"npm:@a/b" = "latest"\n"github:o/r" = { version = "1.2", os = ["macos"] }\n',
    '[tools]\n  spaced = "1"\n#c = "2"\nplain="3"\nx  =  "4"  # comment\n',
    '[tools]\nt = { os = ["macos"] }\nu = {version="5"}\nv = { version = "6" }\n',
    '[settings]\na = "1"\n[tools]\nb = "2"\n[tools.alias]\nc = "3"\n[tools]\nd = "4"\n[tools] # c\ne = "5"\n',
    '[tools]\r\nf = "6"\r\n[tools]\ng = "7"\r\n',
    '[tools]\nh = "8"\nno-newline = "9"',
    '[tools]\n"a b" = "1"\n"a=b" = "2"\nk = "v=w"\n',
    'x = "before"\n[tools]\n= "empty key"\n',
]


def _write(path: Path, data: str) -> None:
    path.write_bytes(data.encode("utf-8", "surrogateescape"))


def _awk(program: str, path: Path, *assigns: str) -> str:
    argv = [AWK]
    for a in assigns:
        argv += ["-v", a]
    r = subprocess.run([*argv, program, str(path)], capture_output=True, check=True)
    return r.stdout.decode("utf-8", "surrogateescape")


@unittest.skipUnless(os.access(AWK, os.X_OK) and os.access(BASH, os.X_OK), "no /usr/bin/awk or /bin/bash")
class ListTests(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir()
        self.addCleanup(fake.remove, self.dir)
        self.path = self.dir / "list.txt"

    def test_names(self):
        for text in LIST_FILES:
            with self.subTest(text=text):
                _write(self.path, text)
                self.assertEqual(lists.names(self.path), _awk(LIST_NAMES, self.path).split("\n")[:-1])

    def test_a_missing_list_has_no_names(self):
        self.assertEqual(lists.names(self.dir / "missing.txt"), [])

    def test_delete(self):
        for text in LIST_FILES:
            for name in ("jq", "fd", "a", "x", "crlf"):
                with self.subTest(text=text, name=name):
                    _write(self.path, text)
                    want = _awk(DELETE, self.path, f"e={name}")
                    lists.delete(self.path, name)
                    self.assertEqual(self.path.read_bytes().decode("utf-8", "surrogateescape"), want)

    def test_delete_writes_through_a_link(self):
        real = self.dir / "real.txt"
        _write(real, "jq\nfd\n")
        self.path.symlink_to(real)
        lists.delete(self.path, "jq")
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(real.read_text(), "fd\n")

    def test_append(self):
        for text in LIST_FILES + [None]:
            with self.subTest(text=text):
                want_path = self.dir / "want.txt"
                for p in (self.path, want_path):
                    if p.exists():
                        p.unlink()
                    if text is not None:
                        _write(p, text)
                subprocess.run([BASH, "-c", APPEND, "_", str(want_path), "new"], check=True)
                lists.append(self.path, "new")
                self.assertEqual(self.path.read_bytes(), want_path.read_bytes())

    def test_mas_id_of(self):
        text = ("# apps\n  5   Back\\slash  \n6 Backslash\n409183694 Keynote\n1234 Keynote\n"
                "7\tTabbed  Name\n8 \n9 Two  Spaces\nx 10 Not\n")
        _write(self.path, text)
        cases = [("Keynote", []), ("Back\\slash", []), ("Backslash", []), ("Tabbed  Name", []),
                 ("Two  Spaces", []), ("Missing", []), ("Missing", ["mas:77 Missing"]),
                 ("Keynote (1234)", ["mas:1234 Keynote (1234)", "nix:jq"]), ("", []), ("Not", [])]
        for name, planned in cases:
            with self.subTest(name=name, planned=planned):
                r = subprocess.run([BASH, "-c", MAS_ID_OF, "_", name, str(self.path), *planned],
                                   capture_output=True, text=True, check=True)
                self.assertEqual(lists.mas_id_of(name, self.path, planned) or "", r.stdout.strip())

    def test_mas_id_of_a_long_file(self):
        """The first match counts, also with more than a pipe buffer after it."""
        _write(self.path, "7 Keynote\n" + "".join(f"{i} App {i}\n" for i in range(100, 8100)) + "8 Keynote\n")
        self.assertEqual(lists.mas_id_of("Keynote", self.path), "7")


@unittest.skipUnless(os.access(AWK, os.X_OK), "no /usr/bin/awk")
class MiseTomlTests(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir()
        self.addCleanup(fake.remove, self.dir)
        self.path = self.dir / "mise.toml"

    def test_tool_lines(self):
        for text in MISE_FILES:
            with self.subTest(text=text):
                _write(self.path, text)
                want = [tuple(line.split("\t", 1)) for line in _awk(MISE_LIST, self.path).split("\n")[:-1]]
                self.assertEqual(mise_toml.tool_lines(self.path), want)

    def test_keys_and_last_parts(self):
        for text in MISE_FILES:
            with self.subTest(text=text):
                _write(self.path, text)
                want = [tuple(line.split("\t", 1)) for line in _awk(MISE_KEYS, self.path).split("\n")[:-1]]
                self.assertEqual([(k, mise_toml.last_part(k)) for k in mise_toml.keys(self.path)], want)

    def test_a_missing_file_has_no_tools(self):
        self.assertEqual(mise_toml.tool_lines(self.dir / "missing.toml"), [])


VERSIONS = [
    "1.0", "1.0.0", "1.00", "1.01", "1.1", "1.9.0", "1.10.0", "2.0.0", "2.0.0-rc1", "2.0.0rc1", "2.0.0~rc1",
    "2.0.0.rc1", "2.0.0+build", "2.0.0-alpha", "2.0.0-beta.2", "2.0.0-beta.10", "v1.3.0", "1.3.0", "0.9",
    "10", "9", "1.2.3a", "1.2.3b", "1.2.3-1", "1.2.3_1", "2026.10.3", "2026.9.30", "1.0.tar.gz", "1.0.zip",
    "1.0a.b", ".", "..", ".hidden", "1.~", "1..2", "1.2-", "15.1.0", "0.55.1", "abc", "ABC", "a~", "~",
    "1.2.3.4.5", "3.10", "3.9", "1:2.0", "2.12.3", "2.12",
]


@unittest.skipUnless(os.access(SORT, os.X_OK), "no /usr/bin/sort")
class VersionTests(unittest.TestCase):
    def sort_v(self, a: str, b: str) -> str:
        """The last line of `sort -V`, as version_ge of the Bash script reads it."""
        r = subprocess.run([SORT, "-V"], input=f"{a}\n{b}\n", capture_output=True, text=True, check=True,
                           env={**os.environ, "LC_ALL": "C"})
        return r.stdout.splitlines()[-1]

    def test_against_sort(self):
        pairs = [(a, b) for a in VERSIONS for b in VERSIONS if a != b]
        rng = random.Random(7)
        rng.shuffle(pairs)
        alphabet = "0123456789.-~+abzAZ_"
        for _ in range(300):
            pairs.append(tuple("".join(rng.choice(alphabet) for _ in range(rng.randint(1, 6))) for _ in range(2)))
        for a, b in pairs[:1500]:
            if a == b:
                continue
            with self.subTest(a=a, b=b):
                last = self.sort_v(a, b)
                self.assertEqual(versions.compare(a, b) > 0, last == a)

    def test_version_ge(self):
        self.assertTrue(versions.version_ge("1.10.0", "1.9.0"))
        self.assertFalse(versions.version_ge("1.2.0", "v1.3.0"))
        self.assertTrue(versions.version_ge("v2.0", "2.0"))
        self.assertTrue(versions.version_ge("2.0.0-rc1", "2.0.0"))
        self.assertFalse(versions.version_ge("1.01", "1.1"))
        self.assertTrue(versions.version_ge("1.1", "1.01"))


if __name__ == "__main__":
    unittest.main()
