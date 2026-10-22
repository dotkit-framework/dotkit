import re
import unittest

from dotkit_migrate import base
from tests import fake


class BaseTests(unittest.TestCase):
    def setUp(self):
        self.b = base.Base(fake.ROOT)

    def test_links_match_nix_home(self):
        """The tables of base.py are the links of nix/home.nix."""
        text = (fake.ROOT / "nix/home.nix").read_text()
        links = dict(re.findall(r'^\s*"([^"]+)" = (?:lib\.mkDefault )?"?([^";]+)"?;', text, re.M))
        targets = {t for t, src in links.items() if src.startswith(("config/", "scripts/"))}
        targets |= {".local/share/dotkit"}
        # A file of the dotfiles repository: plan.py handles it as the mise config.
        targets -= {".config/mise/config.toml"}
        self.assertEqual(targets, set(base.BASE_TARGETS))
        for link in base.LINKS:
            self.assertEqual(links[link.target], link.source, link.target)
            self.assertTrue((fake.ROOT / link.source).is_file(), link.source)
        for main in base.SHELL_LAYERS:
            self.assertIn(main, links)

    def test_layers_are_read(self):
        """Each layer file is one that the base file reads."""
        for main, (layer, _) in base.SHELL_LAYERS.items():
            source = {".zshenv": "config/zsh/zshenv", ".zprofile": "config/zsh/zprofile",
                      ".zshrc": "config/zsh/zshrc", ".bash_profile": "config/bash/bash_profile"}[main]
            self.assertIn("~/" + layer, (fake.ROOT / source).read_text(), main)
        self.assertIn("config.local", (fake.ROOT / "config/ghostty.conf").read_text())
        self.assertIn("~/.gitconfig", (fake.ROOT / "config/gitconfig").read_text())

    def test_provides_brew(self):
        self.assertEqual(self.b.provides_brew("ripgrep"), "ripgrep (dotkit, from Nix)")
        self.assertEqual(self.b.provides_brew("yq"), "yq-go (dotkit, from Nix)")
        self.assertEqual(self.b.provides_brew("node"), "node (dotkit, from mise)")
        self.assertEqual(self.b.provides_brew("herdr"), "github:herdrdev/herdr (dotkit, from mise)")
        self.assertEqual(self.b.provides_brew("ghostty", "cask"), "ghostty (dotkit cask)")
        self.assertEqual(self.b.provides_brew("zsh"), "zsh (nix-darwin, the login shell)")
        self.assertIsNone(self.b.provides_brew("wget"))
        self.assertIsNone(self.b.provides_brew("node@20"))
        self.assertIsNone(self.b.provides_brew("ghostty"), "a formula ghostty is not the cask")

    def test_template_anchors(self):
        """plan.py edits the template at these lines."""
        t = self.b.template
        self.assertIn('    ".zshrc.local" = "config/zsh/zshrc";\n', (t / "nix/home.nix").read_text())
        self.assertIn("  homebrew.taps = [ ];\n", (t / "nix/darwin.nix").read_text())
        self.assertIn("  homebrew.masApps = { };\n", (t / "nix/darwin.nix").read_text())
        self.assertIn('# keymap = "vi";', (t / "nix/shell.nix").read_text())
        self.assertIn("[tools]", (t / "config/mise.toml").read_text())


if __name__ == "__main__":
    unittest.main()
