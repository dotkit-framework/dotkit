import re
import unittest

from dotkit.migrate import base
from dotkit.migrate._compat import tomllib
from tests import fake


class BaseTests(unittest.TestCase):
    def setUp(self):
        self.b = base.Base(fake.ROOT)

    def test_links_match_nix_home(self):
        """The tables of base.py are the links of nix/home.nix."""
        text = (fake.ROOT / "nix/home.nix").read_text()
        # The layers block has the same form: leave it out.
        text = re.sub(r"^\s*layers = \{\n.*?^\s*\};\n", "", text, flags=re.M | re.S)
        links = dict(re.findall(r'^\s*"([^"]+)" = (?:lib\.mkDefault )?"?([^";]+)"?;', text, re.M))
        targets = {t for t, src in links.items() if src.startswith(("config/", "scripts/"))}
        targets |= {".local/share/dotkit"}
        # A file of the dotfiles repository: config_files handles it as the mise config.
        targets -= {".config/mise/config.toml"}
        self.assertEqual(targets, set(base.BASE_TARGETS))
        for link in base.LINKS:
            self.assertEqual(links[link.target], link.source, link.target)
            self.assertTrue((fake.ROOT / link.source).is_file(), link.source)
        for main in base.SHELL_LAYERS:
            self.assertIn(main, links)

    def test_own_files_match_nix(self):
        """base.OWN_FILES: the base puts their settings where the tool reads them first, and does not link them."""
        home = (fake.ROOT / "nix/home.nix").read_text()
        darwin = (fake.ROOT / "nix/darwin.nix").read_text()
        self.assertEqual([o.target for o in base.OWN_FILES], [".npmrc", ".config/uv/uv.toml"])
        self.assertIn('NPM_CONFIG_GLOBALCONFIG = "${cfg.source}/config/npmrc";', home)
        self.assertIn('"uv/uv.toml".source = ../config/uv.toml;', darwin)
        for own in base.OWN_FILES:
            self.assertTrue((fake.ROOT / own.source).is_file(), own.source)
            self.assertNotIn(f'"{own.target}" =', home, own.target)
            self.assertNotIn(own.target, base.BASE_TARGETS)

    def test_layers_match_nix_home(self):
        """dotkit.layers of nix/home.nix are the layers of base.py, and the mise config."""
        text = (fake.ROOT / "nix/home.nix").read_text()
        block = re.search(r"^\s*layers = \{\n(.*?)^\s*\};\n", text, re.M | re.S)
        self.assertIsNotNone(block, "dotkit.layers in nix/home.nix")
        layers = dict(re.findall(r'^\s*"([^"]+)" = "([^"]+)";', block.group(1), re.M))
        expected = {link.target: link.layer for link in base.LINKS if link.layer}
        expected.update({main: layer for main, (layer, _) in base.SHELL_LAYERS.items()})
        expected[".config/mise/conf.d/dotkit.toml"] = ".config/mise/config.toml"
        self.assertEqual(layers, expected)

    def test_tool_path_matches_nix_home(self):
        """base.TOOL_PATH is dotkit.toolPath of nix/home.nix, in its order."""
        text = (fake.ROOT / "nix/home.nix").read_text()
        block = re.search(r"^\s*toolPath = lib\.mkMerge \[\n(.*?)^\s*\];\n", text, re.M | re.S)
        self.assertIsNotNone(block, "dotkit.toolPath in nix/home.nix")
        dirs = re.findall(r'^\s*"([^"]+)"\s*$', block.group(1), re.M)
        dirs = [d.replace("${user.name}", "<user>").replace("${user.home}", "~") for d in dirs]
        self.assertEqual(dirs, list(base.TOOL_PATH))
        self.assertEqual(base.tool_path("/Users/jane", "jane")[:3],
                         ["/etc/profiles/per-user/jane/bin", "/run/current-system/sw/bin", "/Users/jane/.local/bin"])

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

    def test_mise_settings(self):
        """Base.mise_settings is the [settings] table of the base config/mise.toml, as Base.mise is [tools]."""
        data = tomllib.loads((fake.ROOT / "config/mise.toml").read_text())
        self.assertEqual(self.b.mise_settings, data["settings"])
        self.assertEqual(self.b.mise, data["tools"])
        self.assertIsInstance(self.b.mise_settings["idiomatic_version_file_enable_tools"], list)
        self.assertEqual(base.Base(fake.ROOT / "nonexistent").mise_settings, {})

    def test_template_anchors(self):
        """docs.py edits the template at these lines."""
        t = self.b.template
        self.assertIn('    ".zshrc.local" = "config/zsh/zshrc";\n', (t / "nix/home.nix").read_text())
        darwin = (t / "nix/darwin.nix").read_text()
        self.assertIn("    taps = [ ./taps.txt ];\n", darwin)
        self.assertIn("    masApps = [ ./mas.txt ];\n", darwin)
        self.assertNotIn("homebrew.", darwin, "taps and App Store apps are in their list files")
        for rel in ("nix/taps.txt", "nix/mas.txt"):
            self.assertEqual(base.read_list(t / rel), [], rel)
        self.assertIn('# keymap = "vi";', (t / "nix/shell.nix").read_text())
        self.assertIn("[tools]", (t / "config/mise.toml").read_text())


if __name__ == "__main__":
    unittest.main()
