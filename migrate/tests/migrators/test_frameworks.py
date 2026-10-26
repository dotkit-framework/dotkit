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
                      "need it.\n\n- Oh My Zsh: before `rm -rf ~/.oh-my-zsh`, check that nothing points into it: "
                      "LaunchAgents, IDE interpreter settings, git hooks, crontab "
                      "(`grep -rl ~/.oh-my-zsh ~/Library/LaunchAgents`).\n", report)

    def test_the_removal_quotes_the_path(self):
        self.h.mkdir(".oh-my-zsh")
        p = fake.session(self.h, fake.brew_runner())
        frameworks.facts_of(p.scan()).found["Oh My Zsh"] = "~/my shell/oh-my-zsh"
        report = p.build(p.defaults(str(self.h.home / ".dotfiles"))).report
        self.assertIn("- Oh My Zsh: before `rm -rf ~/'my shell/oh-my-zsh'`, check that nothing points into it", report)
        self.assertIn("(`grep -rl ~/'my shell/oh-my-zsh' ~/Library/LaunchAgents`).\n", report)


if __name__ == "__main__":
    unittest.main()
