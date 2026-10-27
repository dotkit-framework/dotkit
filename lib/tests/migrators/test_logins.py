"""The logins migrator: files that hold logins, report only."""

import unittest

from dotkit.migrate.migrators import logins
from tests import fake


class LoginsTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def test_login_files(self):
        self.h.write(".config/gh/hosts.yml", "github.com:\n  user: jane\n")
        self.h.write(".netrc", "machine example.com login me password hunter2hunter2\n")
        p = fake.session(self.h, fake.brew_runner())
        facts = p.scan()
        self.assertEqual(facts.warnings, [])
        self.assertEqual(logins.facts_of(facts).files, [".config/gh/hosts.yml", ".netrc"])
        report = p.build(p.defaults(str(self.h.home / ".dotfiles"))).report
        self.assertIn("### Logins\n\nThese files hold logins. They stay in place and are not in the repository.\n\n"
                      "- ~/.config/gh/hosts.yml: on a new Mac, `gh auth login`\n- ~/.netrc: copy it by hand\n", report)
        self.assertNotIn("hunter2", report)


if __name__ == "__main__":
    unittest.main()
