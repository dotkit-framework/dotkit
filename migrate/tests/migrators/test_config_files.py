"""The config_files migrator: your files where dotkit links its own, ~/.gitconfig, and your mise config."""

import unittest

from dotkit_migrate import registry
from dotkit_migrate._compat import tomllib
from dotkit_migrate.migrators import config_files
from tests import fake
from tests.test_plan import used_mac


class ConfigFilesTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.p = fake.session(self.h, used_mac(self.h))
        self.repo = str(self.h.home / ".dotfiles")

    def build(self, options=None, **changes):
        """The plan with the defaults, and these changes."""
        c = self.p.defaults(self.repo)
        c.options.update(options or {})
        for k, v in changes.items():
            setattr(c, k, v)
        return self.p.build(c)

    def test_scan(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/ghostty/config", "font-size = 16\n")
        h.write(".config/ghostty/config.local", "theme = mine\n")
        h.write(".npmrc", fake.fill("//registry.npmjs.org/:_authToken=@NPM@\n"))
        h.write(".editorconfig", (fake.ROOT / "config/editorconfig").read_text())
        h.write(".config/starship.toml", "")
        h.write(".gitconfig", "[user]\n\tname = Jane\n")
        h.write(".config/mise/config.toml", fake.fill('[tools]\njq = "latest"\n\n[env]\nGITHUB_TOKEN = "@GH@"\n'))
        facts = fake.session(h, fake.brew_runner()).scan()
        self.assertEqual(facts.warnings, [])
        s = config_files.facts_of(facts)
        cfg = {c.target: (bool(c.secrets), c.same_as_base, c.layer_exists) for c in s.files}
        self.assertEqual(cfg, {".config/ghostty/config": (False, False, True), ".npmrc": (True, False, False),
                               ".editorconfig": (False, True, False), ".config/starship.toml": (False, False, False)})
        self.assertEqual(s.gitconfig.path, h.home / ".gitconfig")
        # The scan checks your mise config for secrets, as every config file.
        self.assertEqual(s.mise_config.path, h.home / ".config/mise/config.toml")
        self.assertTrue(s.mise_config.secrets)

    def test_layered_mise(self):
        facts = self.p.scan()
        c = self.p.defaults(self.repo)
        self.assertEqual(c.option("file:.config/mise/config.toml"), "layer")
        user = config_files.layered_mise(facts, c)
        self.assertEqual((user.tools, user.settings), ({"jq": "latest"}, {"experimental": True}))
        c.options["file:.config/mise/config.toml"] = "keep"
        self.assertIsNone(config_files.layered_mise(facts, c))

    def test_the_tools_of_your_layered_mise_config_are_not_added_again(self):
        """config_files sets your file as the base after the tool migrators: they skip its tools, no conflict."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/mise/config.toml", '[tools]\n"npm:typescript" = "1"\n')
        h.json(".nvm/versions/node/v20.11.0/lib/node_modules/typescript/package.json",
               {"name": "typescript", "version": "5.4.0", "bin": {"tsc": "bin/tsc"}})
        h.json(".nvm/versions/node/v20.11.0/lib/node_modules/eslint/package.json",
               {"name": "eslint", "version": "9.0.0", "bin": {"eslint": "x"}})
        p = registry.Session(fake.context_of(h, fake.brew_runner()), strict=True)
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertEqual(tomllib.loads(result.files["config/mise.toml"])["tools"],
                         {"npm:typescript": "1", "npm:eslint": "latest"})
        self.assertIn("1 npm tool -> config/mise.toml", result.summary)
        self.assertNotIn("`npm:typescript`, was", result.report)

    def test_merged_sources(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/git/config", "[pull]\n\trebase = true\n")
        h.write(".gitconfig", "[core]\n")
        p = fake.session(h, fake.brew_runner())
        c = p.defaults(str(h.home / ".dotfiles"))
        self.assertEqual(config_files.merged_sources(p.scan(), c),
                         {"config/gitconfig": "~/.config/git/config and ~/.gitconfig"})
        c.options["file:.gitconfig"] = "keep"
        self.assertEqual(config_files.merged_sources(p.scan(), c), {})

    def test_a_mise_config_that_is_not_carried_does_not_hide_tools(self):
        """A mise config with a secret stays in place, also when the choice says layer, so its tools are added."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/mise/config.toml",
                fake.fill('[tools]\n"npm:typescript" = "latest"\n\n[env]\nGITHUB_TOKEN = "@GH@"\n'))
        h.json(".nvm/versions/node/v20.11.0/lib/node_modules/typescript/package.json",
               {"name": "typescript", "version": "5.4.0", "bin": {"tsc": "bin/tsc"}})
        p = fake.session(h, fake.brew_runner())
        c = p.defaults(str(h.home / ".dotfiles"))
        self.assertEqual(c.option("file:.config/mise/config.toml"), "keep")
        c.options["file:.config/mise/config.toml"] = "layer"
        result = p.build(c)
        self.assertEqual(tomllib.loads(result.files["config/mise.toml"])["tools"], {"npm:typescript": "latest"})
        self.assertIsNone(result.links[".config/mise/config.toml"])
        self.assertNotIn(fake.TOKENS["@GH@"], result.files["config/mise.toml"])

    def test_file_choices(self):
        p = self.build(options={"file:.config/ghostty/config": "replace", "file:.config/starship.toml": "dotkit",
                                "file:.npmrc": "keep", "file:.gitconfig": "keep",
                                "file:.config/mise/config.toml": "keep"},
                       keymap="vi")
        self.assertEqual(p.links[".config/ghostty/config"], "config/ghostty.conf")
        self.assertNotIn(".config/starship.toml", p.links)
        self.assertNotIn(".gitconfig", p.links)
        self.assertIsNone(p.links[".config/mise/config.toml"])
        self.assertIn("~/.config/starship.toml is replaced by the dotkit file", p.report)
        self.assertIn('    keymap = "vi";', p.files["nix/shell.nix"])

    def test_a_file_with_a_secret_cannot_go_into_the_repository(self):
        files = next(page for page in self.p.pages() if page.key == "files")
        npmrc = next(i for i in files.items if i.id == "file:.npmrc")
        self.assertEqual(npmrc.options, ("keep", "dotkit"))
        self.assertEqual(npmrc.default_option, "keep")
        # Also when a choice says so: the build keeps the file in place.
        p = self.build(options={"file:.npmrc": "replace"})
        self.assertIsNone(p.links[".npmrc"])
        self.assertNotIn("config/npmrc", p.copies)


if __name__ == "__main__":
    unittest.main()
