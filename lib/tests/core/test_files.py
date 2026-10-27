"""core/lists.py and core/mise_toml.py.

The list readers must give what the Nix code gives that installs from the
lists (nix/read-list.nix, read-taps.nix, read-mas.nix), so these tests
evaluate that code with nix on odd files. Without nix (the sandbox of `nix
flake check`), those comparisons skip; CI runs them in the check job.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from dotkit.core import lists, mise_toml
from tests import fake

NIX = shutil.which("nix")
ROOT = fake.ROOT

LIST_FILES = [
    "",
    "\n",
    "jq\n",
    "jq",
    "# comment\njq\n",
    "  jq  # spaced\n\tfd\t\n",
    "jq#nospace\n#only\n   \n",
    "a b c\nlast-no-newline",
    "crlf\r\nnext\r\n",
    "ünïcode\n",
    "# jq\njq # one\n  jq\njqq\nx jq\n",
]
TAP_FILES = ["", "acme/tools\n", "# taps\nacme/tools git@x:y.git  # comment\n  corp/tap\r\nacme/tools\n"]
MAS_FILES = [
    "",
    "# apps\n409183694 Keynote\n1234 Keynote\n",
    "  5   Back\\slash  \n6 Backslash\n7\tTabbed  Name\n9 Two  Spaces\r\n",
    "1 C# Toolkit\n#2 Commented\n",
]


def _write(path: Path, data: str) -> None:
    path.write_bytes(data.encode("utf-8"))


def nix_read(reader: str, path: Path):
    """The value that nix/<reader> gives for one file, as JSON."""
    files = f"/. + {json.dumps(str(path))}"
    arg = f"[ ({files}) ]" if reader in ("read-taps.nix", "read-mas.nix") else f"({files})"
    expr = f"lib: import (/. + {json.dumps(str(ROOT / 'nix' / reader))}) lib {arg}"
    r = subprocess.run([NIX, "eval", "--impure", "--json", "--inputs-from", str(ROOT), "nixpkgs#lib", "--apply",
                        expr], capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


class ListTests(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir()
        self.addCleanup(fake.remove, self.dir)
        self.path = self.dir / "list.txt"

    @unittest.skipUnless(NIX, "no nix")
    def test_readers_agree_with_nix(self):
        for reader, files, ours in (("read-list.nix", LIST_FILES, lists.names),
                                    ("read-taps.nix", TAP_FILES, lists.taps)):
            for text in files:
                with self.subTest(reader=reader, text=text):
                    _write(self.path, text)
                    want = [t if isinstance(t, str) else t["name"] for t in nix_read(reader, self.path)]
                    # read-taps.nix keeps each tap once; the order of the first lines stays.
                    got = list(dict.fromkeys(ours(self.path))) if reader == "read-taps.nix" else ours(self.path)
                    self.assertEqual(got, want)
        for text in MAS_FILES:
            with self.subTest(reader="read-mas.nix", text=text):
                _write(self.path, text)
                ruby = {"\\": "\\\\", '"': '\\"', "#": "\\#"}
                got = {}
                for app_id, name in lists.mas_apps(self.path):
                    got.setdefault("".join(ruby.get(c, c) for c in name), int(app_id))
                self.assertEqual(got, nix_read("read-mas.nix", self.path))

    def test_a_missing_list_has_no_names(self):
        self.assertEqual(lists.names(self.dir / "missing.txt"), [])
        self.assertEqual(lists.mas_apps(self.dir / "missing.txt"), [])

    def test_append(self):
        cases = [(None, "new\n"), ("", "new\n"), ("jq", "jq\nnew\n"), ("jq\n", "jq\nnew\n"),
                 ("a\r\n", "a\r\nnew\n")]
        for text, want in cases:
            with self.subTest(text=text):
                if self.path.exists():
                    self.path.unlink()
                if text is not None:
                    _write(self.path, text)
                lists.append(self.path, "new")
                self.assertEqual(self.path.read_bytes().decode(), want)

    def test_delete(self):
        cases = [("jq\nfd\n", "jq", "fd\n"), ("# CLIs\njq # JSON\n  jq\r\njqq\n# jq\nlast", "jq", "# CLIs\njqq\n# jq\nlast"),
                 ("", "jq", ""), ("jq", "jq", ""), ("a b\nab\n", "a b", "ab\n")]
        for text, name, want in cases:
            with self.subTest(text=text, name=name):
                _write(self.path, text)
                lists.delete(self.path, name)
                self.assertEqual(self.path.read_bytes().decode(), want)

    def test_delete_writes_through_a_link(self):
        real = self.dir / "real.txt"
        _write(real, "jq\nfd\n")
        self.path.symlink_to(real)
        lists.delete(self.path, "jq")
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(real.read_bytes().decode(), "fd\n")

    def test_delete_mas(self):
        _write(self.path, "# apps\n409183694 Keynote\n  1234 Keynote (1234)  \n#1234 old\n")
        lists.delete_mas(self.path, "1234")
        self.assertEqual(self.path.read_bytes().decode(), "# apps\n409183694 Keynote\n#1234 old\n")

    def test_mas_id_of(self):
        _write(self.path, "# apps\n  5   Back\\slash  \n409183694 Keynote\n1234 Keynote\n7\tTabbed  Name\n")
        cases = [("Keynote", [], "409183694"), ("Back\\slash", [], "5"), ("Tabbed  Name", [], "7"),
                 ("Missing", [], None), ("Missing", [("77", "Missing")], "77"), ("", [], None)]
        for name, planned, want in cases:
            with self.subTest(name=name):
                self.assertEqual(lists.mas_id_of(name, self.path, planned), want)


class MiseTomlTests(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir()
        self.addCleanup(fake.remove, self.dir)
        self.path = self.dir / "mise.toml"

    def test_tools(self):
        _write(self.path, '[tools] # c\n  spaced = "1"\nplain = "2"  # pinned\n"npm:@a/b" = "latest"\n'
                          'table = { version = "4.5", os = ["macos"] }\nmany = ["22", "20"]\n'
                          '[settings]\nexperimental = true\n[tools.python]\nversion = "3.13"\n')
        got = {k: mise_toml.version_text(v) for k, v in mise_toml.tools(self.path).items()}
        self.assertEqual(got, {"spaced": "1", "plain": "2", "npm:@a/b": "latest", "table": "4.5",
                               "many": "22, 20", "python": "3.13"})

    def test_missing_and_invalid(self):
        self.assertEqual(mise_toml.tools(self.dir / "missing.toml"), {})
        _write(self.path, "[settings]\nx = 1\n")
        self.assertEqual(mise_toml.tools(self.path), {})
        _write(self.path, "[tools]\nx = \n")
        with self.assertRaises(mise_toml.InvalidToml) as c:
            mise_toml.tools(self.path)
        self.assertTrue(str(c.exception).startswith(f"{self.path} is not valid TOML: "))

    def test_short(self):
        for key, want in (("github:modem-dev/hunk", "hunk"), ("npm:@a/b", "b"), ("node", "node")):
            self.assertEqual(mise_toml.short(key), want)


if __name__ == "__main__":
    unittest.main()
