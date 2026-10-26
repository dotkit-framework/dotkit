"""The config_files migrator: your files where dotkit links its own, ~/.gitconfig, and your mise config."""

import os
import unittest

from dotkit_migrate import registry
from dotkit_migrate._compat import tomllib
from dotkit_migrate.declared import Declared
from dotkit_migrate.migrators import config_files
from dotkit_migrate.model import Facts
from tests import fake
from tests.test_session import used_mac


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
        """A mise config with a secret stays in place, also when the choice says layer.

        mise then does not read config/mise.toml, so it stays the template, and
        the selected tools are to-dos; the token is nowhere in the plan.
        """
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/mise/config.toml", fake.fill('[tools]\njq = "latest"\n\n[env]\nGITHUB_TOKEN = "@GH@"\n'))
        h.json(".nvm/versions/node/v20.11.0/lib/node_modules/typescript/package.json",
               {"name": "typescript", "version": "5.4.0", "bin": {"tsc": "bin/tsc"}})
        p = fake.session(h, fake.brew_runner())
        c = p.defaults(str(h.home / ".dotfiles"))
        self.assertEqual(c.option("file:.config/mise/config.toml"), "keep")
        c.options["file:.config/mise/config.toml"] = "layer"
        self.assertEqual(config_files.mise_target(p.scan(), c), "kept")
        result = p.build(c)
        self.assertNotIn("config/mise.toml", result.files, "the template's file, unchanged")
        self.assertIsNone(result.links[".config/mise/config.toml"])
        self.assertIn(config_files.KEPT_INTRO + "\n\n- `typescript` (nvm v20.11.0): `mise use -g npm:typescript`\n",
                      result.report)
        token = fake.TOKENS["@GH@"]
        for text in [result.report, *result.summary, *result.files.values(), *result.local.values()]:
            self.assertNotIn(token, text)

    def test_an_unreadable_mise_config(self):
        """A mise config that cannot be read counts as one with a secret: kept, and the plan has no tools of it."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = h.write(".config/mise/config.toml", '[tools]\njq = "latest"\n\n[settings]\n'
                    'idiomatic_version_file_enable_tools = ["rust"]\n')
        os.chmod(p, 0)
        self.addCleanup(os.chmod, p, 0o644)
        s = fake.session(h, fake.brew_runner())
        c = s.defaults(str(h.home / ".dotfiles"))
        c.options["file:.config/mise/config.toml"] = "keep"
        facts = s.scan()
        self.assertEqual(config_files.mise_target(facts, c), "kept")
        self.assertFalse(Declared.for_setup(facts.base, facts).yours_mise_key("jq"))
        result = s.build(c)
        self.assertIsNone(result.links[".config/mise/config.toml"])
        self.assertNotIn("config/mise.toml", result.files)

    def test_mise_target(self):
        """Layered is "repository", kept is "kept", and without a config of yours None."""
        facts = self.p.scan()
        c = self.p.defaults(self.repo)
        self.assertEqual(config_files.mise_target(facts, c), "repository")
        self.assertTrue(Declared.for_setup(facts.base, facts).yours_mise_key("jq"))
        c.options["file:.config/mise/config.toml"] = "keep"
        self.assertEqual(config_files.mise_target(facts, c), "kept")
        self.assertTrue(Declared.for_setup(facts.base, facts).yours_mise_key("jq"))
        h = fake.Home()
        self.addCleanup(h.cleanup)
        empty = fake.session(h, fake.brew_runner()).scan()
        self.assertIsNone(config_files.mise_target(empty, c))
        self.assertFalse(Declared.for_setup(empty.base, empty).yours_mise_key("jq"))
        # Without the facts of config_files (its scan failed), too.
        none = Facts(facts.identity, facts.base)
        self.assertIsNone(config_files.mise_target(none, c))
        self.assertFalse(Declared.for_setup(none.base, none).yours_mise_key("jq"))

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


    ENABLE = "idiomatic_version_file_enable_tools"
    DISABLE = "idiomatic_version_file_disable_files"

    def mise_plan(self, text, option="layer", base_settings=None):
        """The plan of a Mac with only this ~/.config/mise/config.toml, strict."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/mise/config.toml", text)
        ctx = fake.context_of(h, fake.brew_runner())
        if base_settings is not None:
            ctx.base.__dict__["mise_settings"] = base_settings
        p = registry.Session(ctx, strict=True)
        c = p.defaults(str(h.home / ".dotfiles"))
        c.options["file:.config/mise/config.toml"] = option
        return p.build(c)

    def test_idiomatic_settings_you_do_not_set_are_not_written(self):
        """Your config without the keys: nothing is written, so dotkit's lists stay in effect."""
        text = '[tools]\njq = "latest"\n\n[settings]\nexperimental = true\n'
        result = self.mise_plan(text)
        out = result.files["config/mise.toml"]
        self.assertTrue(out.endswith(text))
        self.assertNotIn("idiomatic", out)
        self.assertNotIn("idiomatic", result.report)

    BASE_ENABLE = '`["node", "python", "ruby", "rust"]`'

    def test_a_single_line_key_gets_the_union(self):
        """The edit of your line, and a to-do that says so: an edit of your content is never silent."""
        text = (f'[settings]\n{self.ENABLE} = ["go", "node"]  # mine\n'
                f"{self.DISABLE} = ['python:pyproject.toml']\nexperimental = true\n")
        result = self.mise_plan(text)
        settings = tomllib.loads(result.files["config/mise.toml"])["settings"]
        self.assertEqual(settings, {self.ENABLE: ["node", "python", "ruby", "rust", "go"],
                                    self.DISABLE: ["ruby:Gemfile", "node:package.json", "python:pyproject.toml"],
                                    "experimental": True})
        self.assertIn(f'{self.ENABLE} = ["node", "python", "ruby", "rust", "go"]  # mine\n',
                      result.files["config/mise.toml"])
        self.assertIn(f'config/mise.toml: `{self.ENABLE}` is now `["node", "python", "ruby", "rust", "go"]`, '
                      "dotkit's list and yours, because a list in your config replaces dotkit's "
                      '(yours was `["go", "node"]`). Check it.', result.report)
        self.assertIn(f'config/mise.toml: `{self.DISABLE}` is now '
                      '`["ruby:Gemfile", "node:package.json", "python:pyproject.toml"]`, dotkit\'s list and yours, '
                      "because a list in your config replaces dotkit's (yours was `[\"python:pyproject.toml\"]`). "
                      "Check it.", result.report)
        self.assertEqual(result.report.count("is now `["), 2)

    def test_a_key_with_every_name_of_dotkit_is_not_changed(self):
        """The lists are allowlists: as a set, yours has dotkit's names already. No edit and no to-do."""
        text = f'[settings]\n{self.ENABLE} = ["rust", "ruby", "python", "node"]\n'
        for option in ("layer", "keep"):
            with self.subTest(option):
                result = self.mise_plan(text, option=option)
                if option == "layer":
                    self.assertTrue(result.files["config/mise.toml"].endswith(text))
                self.assertNotIn("idiomatic", result.report)

    def test_a_key_that_cannot_be_replaced_gives_the_to_do(self):
        todo = (f"config/mise.toml sets `{self.ENABLE}`, which replaces dotkit's {self.BASE_ENABLE}. "
                'Set it to `["node", "python", "ruby", "rust", "go"]`.')
        cases = {
            "a value on several lines": f'[settings]\n{self.ENABLE} = [\n  "go",\n]\n',
            "a dotted key": f'settings.{self.ENABLE} = ["go"]\n',
        }
        for why, text in cases.items():
            with self.subTest(why):
                result = self.mise_plan(text)
                self.assertTrue(result.files["config/mise.toml"].endswith(text))
                self.assertEqual(tomllib.loads(result.files["config/mise.toml"])["settings"][self.ENABLE], ["go"])
                self.assertIn(todo, result.report)
                self.assertNotIn("is now `[", result.report, "nothing was edited")

    def test_a_value_that_is_not_a_list_of_names(self):
        """No union and no edit, in both modes: a to-do of its own."""
        cases = {f'{self.ENABLE} = "node,ruby"\n': '`"node,ruby"`',
                 f'{self.ENABLE} = ["go", 1]\n': '`["go", 1]`'}
        for line, shown in cases.items():
            for option in ("layer", "keep"):
                with self.subTest(line=line, option=option):
                    text = "[settings]\n" + line
                    result = self.mise_plan(text, option=option)
                    if option == "layer":
                        self.assertTrue(result.files["config/mise.toml"].endswith(text))
                    self.assertIn(f"your mise config sets `{self.ENABLE}` to {shown}, which is not a list of "
                                  f"names. dotkit's value is {self.BASE_ENABLE}. Check it.", result.report)
                    self.assertNotIn("Set it to", result.report)
                    self.assertNotIn("is now `[", result.report)

    def test_a_kept_mise_config_gets_the_to_do_and_is_not_edited(self):
        text = f'[settings]\n{self.ENABLE} = ["go"]\n'
        result = self.mise_plan(text, option="keep")
        self.assertIsNone(result.links[".config/mise/config.toml"])
        self.assertNotIn("idiomatic", result.files.get("config/mise.toml", ""))
        self.assertFalse([q for q in result.copies.values() if str(q).endswith(".config/mise/config.toml")])
        self.assertIn(f"~/.config/mise/config.toml sets `{self.ENABLE}`, which replaces dotkit's "
                      f'{self.BASE_ENABLE}. Set it to `["node", "python", "ruby", "rust", "go"]`.', result.report)
        self.assertNotIn("is now `[", result.report)

    def test_the_union_starts_with_the_base_file(self):
        """dotkit's values come from the base config/mise.toml (Base.mise_settings), not from the code."""
        text = f'[settings]\n{self.ENABLE} = ["go"]\n{self.DISABLE} = ["x:y"]\n'
        result = self.mise_plan(text, base_settings={self.ENABLE: ["deno", "go"]})
        settings = tomllib.loads(result.files["config/mise.toml"])["settings"]
        self.assertEqual(settings, {self.ENABLE: ["deno", "go"], self.DISABLE: ["x:y"]})
        self.assertEqual(
            tomllib.loads((fake.ROOT / "config/mise.toml").read_text())["settings"][self.ENABLE],
            ["node", "python", "ruby", "rust"], "the other tests assume the base of today")


if __name__ == "__main__":
    unittest.main()
