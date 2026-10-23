"""The path helpers of context.py."""

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


if __name__ == "__main__":
    unittest.main()
