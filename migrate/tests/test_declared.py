"""Declared: what the base, the repository, and your mise config provide (ARCHITECTURE.md, section 4.5)."""

import unittest

from dotkit_migrate import base
from dotkit_migrate.declared import Declared
from tests import fake


class DeclaredTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.base = base.Base(fake.ROOT)
        self.repo = self.h.home / ".dotfiles"

    def facts(self, mise_config=None):
        if mise_config is not None:
            self.h.write(".config/mise/config.toml", mise_config)
        return fake.session(self.h, fake.FakeRunner()).scan()

    def test_setup(self):
        d = Declared.for_setup(self.base, self.facts('[tools]\n"aqua:jqlang/jq" = "latest"\n"npm:x" = "1"\n'))
        self.assertEqual(d.brew("ripgrep", "formula"), "ripgrep (dotkit, from Nix)")
        self.assertEqual(d.brew("node", "formula"), "node (dotkit, from mise)")
        self.assertEqual(d.brew("gpg", "formula"), "gnupg (dotkit, from Nix)")
        self.assertEqual(d.brew("acme/tap/jq", "formula"), "jq (dotkit, from Nix)", "the base first")
        self.assertIsNone(d.brew("wget", "formula"))
        self.assertIsNone(d.brew("x", "cask"), "your mise config has no casks")
        self.assertTrue(d.mise_key("npm:x") and d.yours_mise_key("npm:x"))
        self.assertTrue(d.mise_key("node"), "the base mise tools")
        self.assertFalse(d.yours_mise_key("node"), "the base's node is a default, not yours")
        self.assertFalse(d.mise_key("npm:y"))
        self.assertEqual(d.tool_source("bat"), "bat (from Nix)")
        self.assertEqual(d.tool_source("github.com/sharkdp/hyperfine"), "hyperfine (from Nix)")
        self.assertEqual(d.tool_source("herdr"), "github:herdrdev/herdr (from mise)")
        self.assertFalse(d.tool("@scope/x"), "a tool of your mise config is not one that dotkit has")
        self.assertFalse(d.tool("wget"))
        self.assertFalse(d.tap("acme/tools"))
        self.assertFalse(d.mas_id(1))
        self.assertFalse(d.mas_name("Pages"))

    def test_your_mise_config_provides_a_formula(self):
        d = Declared.for_setup(self.base, self.facts('[tools]\n"aqua:hashicorp/terraform" = "1.7"\n'))
        self.assertEqual(d.brew("hashicorp/tap/terraform", "formula"), "aqua:hashicorp/terraform (your mise config)")
        other = fake.Home()
        self.addCleanup(other.cleanup)
        self.assertIsNone(Declared.for_setup(self.base, fake.session(other, fake.FakeRunner()).scan())
                          .brew("hashicorp/tap/terraform", "formula"))

    def test_without_the_facts_of_config_files(self):
        facts = self.facts('[tools]\nterraform = "1.7"\n').without({"config_files"})
        self.assertIsNone(Declared.for_setup(self.base, facts).brew("terraform", "formula"))

    def test_repository(self):
        h = self.h
        h.write(".dotfiles/nix/packages.txt", "dust # disk usage\n")
        h.write(".dotfiles/nix/brews.txt", "acme/tools/thing\nwget\n")
        h.write(".dotfiles/nix/casks.txt", "zed")
        h.write(".dotfiles/nix/taps.txt", "# taps\nacme/tools git@x:y.git\n")
        h.write(".dotfiles/nix/mas.txt", "1 C# Toolkit\n2 Pages\n")
        h.write(".dotfiles/config/mise.toml", '[tools]\n"github:sharkdp/hyperfine" = "latest"\n')
        evaluated = {"taps": ["corp/tap"], "masApps": {"C\\# Toolkit": 1, "Pages": 2, "Keynote": 3}}
        d = Declared.for_repo(self.base, self.repo, evaluated, self.facts('[tools]\njq = "latest"\n'))
        self.assertEqual(d.brew("thing", "formula"), "thing (from Homebrew)")
        self.assertEqual(d.brew("other/tap/wget", "formula"), "wget (from Homebrew)")
        self.assertEqual(d.brew("dust", "formula"), "dust (from Nix)")
        self.assertEqual(d.brew("zed", "cask"), "zed (from Homebrew)")
        self.assertIsNone(d.brew("zed", "formula"))
        self.assertTrue(d.mise_key("github:sharkdp/hyperfine"))
        self.assertFalse(d.yours_mise_key("github:sharkdp/hyperfine"))
        self.assertTrue(d.mise_key("jq") and d.yours_mise_key("jq"), "your mise config")
        self.assertTrue(d.tool("github.com/x/dust"))
        self.assertTrue(d.tool("wget"), "the repository has it from Homebrew")
        self.assertTrue(d.tap("acme/tools") and d.tap("corp/tap"))
        self.assertFalse(d.tap("other/tap"))
        self.assertTrue(d.mas_id(1) and d.mas_id(3))
        self.assertFalse(d.mas_id(4))
        # The raw names of nix/mas.txt, and a name whose escaped form is a key written by hand.
        self.assertTrue(d.mas_name("C# Toolkit") and d.mas_name("Pages") and d.mas_name("Keynote"))
        self.assertFalse(d.mas_name("C\\# Toolkit"))
        hand = Declared.for_repo(self.base, self.repo, {"masApps": {'Foo \\"Pro\\"': 5, 'Bar "Pro"': 6}},
                                 self.facts())
        self.assertTrue(hand.mas_name('Foo "Pro"'))
        self.assertFalse(hand.mas_name('Foo \\"Pro\\"'))
        self.assertFalse(hand.mas_name('Bar "Pro"'), "its escaped form is not a key")

    def test_an_empty_repository(self):
        d = Declared.for_repo(self.base, self.repo, {}, self.facts())
        self.assertIsNone(d.brew("wget", "formula"))
        self.assertFalse(d.mas_name("Pages") or d.tap("acme/tools") or d.mise_key("npm:x"))


if __name__ == "__main__":
    unittest.main()
