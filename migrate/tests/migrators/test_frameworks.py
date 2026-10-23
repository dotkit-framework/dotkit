"""The frameworks migrator: the shell frameworks, report only."""

import unittest

from dotkit_migrate.migrators import frameworks
from tests import fake


class FrameworksTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def scan(self):
        facts = fake.session(self.h, fake.brew_runner()).scan()
        self.assertEqual(facts.warnings, [])
        return frameworks.facts_of(facts)

    def test_frameworks(self):
        self.h.mkdir(".oh-my-zsh")
        self.h.write(".p10k.zsh", "")
        self.assertEqual(self.scan().found, {"Oh My Zsh": "~/.oh-my-zsh", "Powerlevel10k": "~/.p10k.zsh"})


    def test_report(self):
        self.h.mkdir(".oh-my-zsh")
        p = fake.session(self.h, fake.brew_runner())
        report = p.build(p.defaults(str(self.h.home / ".dotfiles"))).report
        self.assertIn("### Shell frameworks\n\ndotkit's zsh does not load them. Remove each one when you no longer "
                      "need it.\n\n- Oh My Zsh: `rm -rf ~/.oh-my-zsh`\n", report)


if __name__ == "__main__":
    unittest.main()
