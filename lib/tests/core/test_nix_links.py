"""The link lines of nix/ (core/nix_links.py): the plan, the edits, and a file that changes after the plan."""

from __future__ import annotations

import unittest

from dotkit.core import nix_links
from tests import fake

BLOCK = '{\n  dotkit.links = {\n    ".zshrc.local" = "config/zsh/zshrc";\n    ".gemrc" = null; # off\n  };\n}\n'


class NixLinksTests(unittest.TestCase):
    def setUp(self):
        self.repo = fake.tempdir().resolve()
        self.addCleanup(fake.remove, self.repo)
        (self.repo / "nix").mkdir()
        self.home_nix = self.repo / "nix/home.nix"
        self.home_nix.write_text(BLOCK)

    def test_insert_in_the_block(self):
        e = nix_links.find_insert(self.repo, ".config/x", 'config/a"b${c}')
        self.assertEqual((e.path, e.at, e.expected), (self.home_nix, 1, "  dotkit.links = {"))
        self.assertEqual(e.line, '    ".config/x" = "config/a\\"b\\${c}";')
        nix_links.apply([e, nix_links.find_insert(self.repo, ".y", "config/y")])
        self.assertEqual(self.home_nix.read_text().split("\n")[2:4],
                         ['    ".config/x" = "config/a\\"b\\${c}";', '    ".y" = "config/y";'])

    def test_insert_after_the_dotted_lines(self):
        self.home_nix.write_text('{\n  dotkit.links.".a" = "a";\n  dotkit.links.".b" = "b";\n}\n')
        e = nix_links.find_insert(self.repo, ".c", "c")
        self.assertEqual((e.at, e.line), (2, '  dotkit.links.".c" = "c";'))
        self.home_nix.write_text("{ }\n")
        self.assertIsNone(nix_links.find_insert(self.repo, ".c", "c"))

    def test_rows(self):
        self.assertEqual([(e.at, e.expected) for e in nix_links.find_rows(self.repo, ".gemrc")],
                         [(3, '    ".gemrc" = null; # off')])
        nix_links.apply(nix_links.find_rows(self.repo, ".gemrc"))
        self.assertNotIn(".gemrc", self.home_nix.read_text())
        self.assertEqual(nix_links.find_rows(self.repo, ".zsh"), [])

    def test_a_file_that_changed_after_the_plan(self):
        """An edit checks its line: a changed file raises OSError and stays as it is."""
        release = nix_links.find_rows(self.repo, ".gemrc")
        own = nix_links.find_insert(self.repo, ".c", "c")
        changed = BLOCK.replace("  dotkit.links = {\n", "  # mine\n  dotkit.links = {\n")
        self.home_nix.write_text(changed)
        for edits in (release, [own]):
            with self.subTest(edits=edits):
                with self.assertRaisesRegex(OSError, "changed after the plan"):
                    nix_links.apply(edits)
                self.assertEqual(self.home_nix.read_text(), changed)
        self.home_nix.write_text("{ }\n")
        with self.assertRaises(OSError):
            nix_links.apply(release)

    def test_row_of_the_migration(self):
        m = nix_links.ROW.match('    ".npmrc" = "config/npmrc";')
        self.assertEqual((m.group(1), m.group(2)), (".npmrc", "config/npmrc"))
        self.assertIsNone(nix_links.ROW.match('    ".npmrc" = null;').group(2))


if __name__ == "__main__":
    unittest.main()
