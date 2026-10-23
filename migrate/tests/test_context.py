"""The path helpers of context.py."""

import shlex
import unittest

from dotkit_migrate import context
from tests import fake


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def test_repo_error(self):
        home = str(self.h.home)
        self.h.mkdir(".config")
        self.assertIsNone(context.repo_error(f"{home}/.dotfiles", home))
        self.assertIn("exists", context.repo_error(f"{home}/.config", home))
        self.assertIn("Use a directory in", context.repo_error("/tmp/x", home))
        self.assertIn("does not exist", context.repo_error(f"{home}/a/b", home))
        for bad in ('~/dot "files', "~/a$b", "~/a`b", "~/a\\b", "~/a\nb"):
            self.assertIn("Use a path without", context.repo_error(bad, home), bad)
        self.assertIsNone(context.repo_error("~/my dots", home))

    def test_shell_path(self):
        """A path for a command in the report: ~/ outside the quotes, the rest quoted when it needs it."""
        self.assertEqual(context.shell_path("~/go/bin/hey"), "~/go/bin/hey")
        self.assertEqual(context.shell_path("~/Library/Application Support/fnm"),
                         "~/'Library/Application Support/fnm'")
        self.assertEqual(context.shell_path("/opt/my tools/hey"), "'/opt/my tools/hey'")
        self.assertEqual(context.shell_path("~"), "~")
        home = str(self.h.home)
        for path in ("~/it's here/x", "/opt/a'b c", "~/$HOME/`x`"):
            with self.subTest(path=path):
                words = shlex.split("rm -rf " + context.shell_path(path))
                self.assertEqual(words[:2], ["rm", "-rf"])
                self.assertEqual(len(words), 3)
                # shlex.split does not expand ~; the shell does, for the unquoted ~/ in front.
                self.assertEqual(context.expand(words[2], home), context.expand(path, home))


if __name__ == "__main__":
    unittest.main()
