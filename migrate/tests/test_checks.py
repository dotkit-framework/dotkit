"""The checks of a plan against the Mac: links in the way, files moved aside, blockers, and the summary."""

import os
import unittest

from dotkit_migrate import checks, writer
from tests import fake
from tests.test_session import used_mac


class LinkTests(unittest.TestCase):
    def test_links_in_the_way(self):
        """A link of another dotfiles manager is listed for setup, which renames it."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        target = h.write("old-dotfiles/zshrc", "export STOW=1\n")
        os.symlink(target, h.home / ".zshrc")
        os.symlink("/nix/store/abc-home-manager-files/.zprofile", h.home / ".zprofile")
        p = fake.session(h, fake.brew_runner())
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertEqual(result.move_links, [".zshrc"])
        self.assertIn("export STOW=1", result.files["config/zsh/zshrc"])
        self.assertIn("~/.zshrc is a link to ~/old-dotfiles/zshrc", result.report)
        writer.write_new(result, fake.ROOT / "templates/default")
        self.assertEqual(os.readlink(h.home / ".zshrc"), str(target), "setup renames it later, not write")

    def test_folded_stow_directories(self):
        """A linked directory above a target is moved aside; its other files are listed."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        stow = h.write("dotfiles/ghostty/config", "font-size = 16\n").parent
        h.write("dotfiles/ghostty/themes/mine", "background = 000000\n")
        h.mkdir(".config")
        os.symlink(stow, h.home / ".config/ghostty")
        git = h.write("dotfiles/git/config", "[core]\n\teditor = vim\n").parent
        h.write("dotfiles/git/ignore", ".DS_Store\n")
        os.symlink(git, h.home / ".config/git")
        p = fake.session(h, fake.brew_runner())
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertEqual(result.move_links, [".config/ghostty", ".config/git"])
        self.assertEqual(result.blockers, [])
        self.assertIn("~/.config/ghostty is a link to ~/dotfiles/ghostty", result.report)
        self.assertIn("no longer at ~/.config/ghostty: themes/mine", result.report)
        self.assertEqual(result.copies["config/ghostty.conf"], h.home / ".config/ghostty/config")
        self.assertNotIn("~/.config/ghostty/config,", "\n".join(result.summary).split("Links of yours")[0])
        writer.write_new(result, fake.ROOT / "templates/default")
        listed = (h.home / ".dotfiles/.build/links-in-way").read_text()
        self.assertEqual(listed, ".config/ghostty\n.config/git\n")

    def test_linked_config_directory_stops(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        target = h.mkdir("dotfiles/config")
        os.symlink(target, h.home / ".config")
        p = fake.session(h, fake.brew_runner())
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertEqual(len(result.blockers), 1)
        self.assertIn("~/.config is a link to ~/dotfiles/config", result.blockers[0])
        self.assertEqual(result.move_links, [])
        self.assertTrue(result.summary[0].startswith("Setup cannot continue"))


class SummaryTests(unittest.TestCase):
    def build(self, runner_of=used_mac):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = fake.session(h, runner_of(h))
        return h, p.build(p.defaults(str(h.home / ".dotfiles")))

    def test_summary(self):
        h, result = self.build()
        summary = "\n".join(result.summary)
        self.assertIn("Renamed to <name>.before-dotkit by the first apply: ~/.bash_profile, ~/.config/ghostty/config,", summary)
        self.assertIn("~/.npmrc,", summary.split("Renamed")[1])
        self.assertNotIn("Stay in place, not linked", summary)
        self.assertIn("Secrets -> ~/.secrets, outside the repository: GITHUB_TOKEN, NPM_TOKEN", summary)
        self.assertEqual(checks.moved_aside(result)[:2], ["~/.bash_profile", "~/.config/ghostty/config"])

    def test_a_summary_line_with_a_token_is_masked(self):
        def runner_of(h):
            runner = fake.brew_runner()
            runner.commands["git config --global --includes --get user.name"] = fake.fill("Jane @GH@\n")
            return runner

        _, result = self.build(runner_of)
        self.assertIn("(a line with a secret was here)", result.summary)
        self.assertFalse(any(line.startswith("Git identity:") for line in result.summary))
        self.assertNotIn(fake.TOKENS["@GH@"], "\n".join(result.summary))


if __name__ == "__main__":
    unittest.main()
