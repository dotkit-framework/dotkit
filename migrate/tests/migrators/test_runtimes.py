"""The runtimes migrator: the versions of version managers, and the other files that mise reads."""

import dataclasses
import os
import shutil
import unittest

from dotkit_migrate import base, registry
from dotkit_migrate._compat import tomllib
from dotkit_migrate.migrators import runtimes
from dotkit_migrate.model import Choices
from tests import fake
from tests.test_session import used_mac


class RuntimesTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.repo = str(self.h.home / ".dotfiles")

    def scan(self, runner=None):
        facts = fake.session(self.h, runner or fake.brew_runner()).scan()
        self.assertEqual(facts.warnings, [])
        return runtimes.facts_of(facts)

    def test_version_managers(self):
        self.h.mkdir(".nvm/versions/node/v18.20.0")
        self.h.mkdir(".nvm/versions/node/v20.11.0")
        self.h.write(".nvm/alias/default", "20\n")
        self.h.mkdir(".pyenv/versions/3.11.4")
        self.h.mkdir(".pyenv/versions/3.9.18")
        self.h.write(".pyenv/version", "3.11.4\n")
        self.h.mkdir(".asdf/installs/nodejs/18.0.0")
        self.h.mkdir(".asdf/installs/golang/1.22.0")
        self.h.write(".tool-versions", "nodejs 18.0.0\ngolang 1.22.0\n")
        self.h.mkdir(".sdkman/candidates/java/17.0.9-tem")
        os.symlink("17.0.9-tem", self.h.home / ".sdkman/candidates/java/current")
        s = self.scan()
        vms = {vm.name: (vm.versions, vm.default) for vm in s.managers}
        self.assertEqual(vms["nvm"], ({"node": ["v18.20.0", "v20.11.0"]}, {"node": "20"}))
        self.assertEqual(vms["pyenv"], ({"python": ["3.9.18", "3.11.4"]}, {"python": "3.11.4"}))
        self.assertEqual(vms["asdf"], ({"go": ["1.22.0"], "node": ["18.0.0"]}, {"node": "18.0.0", "go": "1.22.0"}))
        self.assertEqual(vms["sdkman"], ({"java": ["17.0.9-tem"]}, {"java": "17.0.9-tem"}))
        self.assertIn(".tool-versions", s.mise_extra)

    def test_version_manager_defaults(self):
        """The global version of each version manager, from its own file or link; "system" is none."""
        self.h.mkdir(".local/share/fnm/node-versions/v20.11.0/installation")
        os.makedirs(self.h.home / ".local/share/fnm/aliases")
        os.symlink(str(self.h.home / ".local/share/fnm/node-versions/v20.11.0/installation"),
                   self.h.home / ".local/share/fnm/aliases/default")
        self.h.mkdir(".volta/tools/image/node/20.11.0")
        self.h.json(".volta/tools/user/platform.json", {"node": {"runtime": "20.11.0", "npm": None}})
        self.h.mkdir(".rbenv/versions/3.3.0")
        self.h.write(".rbenv/version", "3.3.0\nignored\n")
        self.h.mkdir(".jenv/versions/17")
        self.h.write(".jenv/version", "system\n")
        s = self.scan()
        vms = {vm.name: (vm.path, vm.tool, vm.versions, vm.default) for vm in s.managers}
        self.assertEqual(list(vms), ["fnm", "volta", "rbenv", "jenv"])
        self.assertEqual(vms["fnm"], ("~/.local/share/fnm", "node", {"node": ["v20.11.0"]}, {"node": "v20.11.0"}))
        self.assertEqual(vms["volta"], ("~/.volta", "node", {"node": ["20.11.0"]}, {"node": "20.11.0"}))
        self.assertEqual(vms["rbenv"], ("~/.rbenv", "ruby", {"ruby": ["3.3.0"]}, {"ruby": "3.3.0"}))
        self.assertEqual(vms["jenv"], ("~/.jenv", "java", {"java": ["17"]}, {}))

    def test_part_of_the_runtimes(self):
        """The carried row names the managers of the selected tools; the to-do says what is still to do."""
        self.h.mkdir(".pyenv/versions/3.12.2")
        self.h.write(".pyenv/version", "3.12.2\n")
        self.h.mkdir(".asdf/installs/ruby/3.3.0")
        p = fake.session(self.h, used_mac(self.h))
        runtimes = next(page for page in p.pages() if page.key == "runtimes")
        self.assertEqual({i.id for i in runtimes.items}, {"runtime:node", "runtime:python", "runtime:ruby"})
        c = p.defaults(self.repo)
        c.picked["runtimes"] = {"runtime:python"}
        result = p.build(c)
        self.assertIn("runtime versions of pyenv -> config/mise.toml", result.summary)
        tools = tomllib.loads(result.files["config/mise.toml"])["tools"]
        self.assertEqual(tools["python"], "3.12.2")
        self.assertNotIn("node", tools)
        self.assertIn("pyenv (~/.pyenv): python 3.12.2 (default 3.12.2). config/mise.toml has these versions now.",
                      result.report)
        self.assertIn("nvm (~/.nvm): node v20.11.0 (default 20). With mise: `mise use -g node@20`.", result.report)
        c.picked["runtimes"] = set()
        self.assertFalse(any(line.startswith("runtime versions") for line in p.build(c).summary))

    def test_a_selected_runtime(self):
        p = fake.session(self.h, used_mac(self.h))
        c = p.defaults(self.repo)
        c.picked["runtimes"] = {"runtime:node"}
        tools = tomllib.loads(p.build(c).files["config/mise.toml"])["tools"]
        self.assertEqual(tools["node"], "20")


    def test_a_tool_of_your_mise_config_is_not_added(self):
        """pyenv with python in your layered mise config: your version stays, and the item says so."""
        self.h.mkdir(".pyenv/versions/3.12.2")
        self.h.write(".pyenv/version", "3.12.2\n")
        self.h.write(".config/mise/config.toml", '[tools]\npython = "3.11"\n')
        p = fake.session(self.h, fake.brew_runner())
        c = p.defaults(self.repo)
        c.picked["runtimes"] = {"runtime:python"}
        result = p.build(c)
        self.assertEqual(tomllib.loads(result.files["config/mise.toml"])["tools"], {"python": "3.11"})
        self.assertIn("pyenv (~/.pyenv): python 3.12.2 (default 3.12.2). config/mise.toml has python from your "
                      "mise config already, so these versions were not added; `mise use -g python@<version>` for "
                      "one you need. Then remove", result.report)
        self.assertNotIn("has these versions now", result.report)
        self.assertFalse(any(line.startswith("runtime versions") for line in result.summary))

    def test_the_base_runtimes_come_from_the_base(self):
        """The page note and the intro name the runtimes of the base config/mise.toml (Base.mise)."""
        self.h.mkdir(".pyenv/versions/3.12.2")
        ctx = fake.context_of(self.h, fake.brew_runner())
        ctx.base.__dict__["mise"] = {"node": "22", "uv": "latest", "python": {"version": "3.13"}}
        self.assertEqual(runtimes.base_runtimes(ctx.base), "Node 22 and Python 3.13")
        p = registry.Session(ctx, strict=True)
        page = next(page for page in p.pages() if page.key == "runtimes")
        self.assertEqual(page.note, "off: mise installs them; the base has Node 22 and Python 3.13")
        report = p.build(p.defaults(self.repo)).report
        self.assertIn("The base has Node 22 and Python 3.13. A project pins its own versions with `mise use` or a "
                      ".nvmrc, .python-version, .ruby-version, or .tool-versions file.", report)
        base = fake.context_of(self.h, fake.brew_runner()).base
        self.assertEqual(runtimes.base_runtimes(base), "Node {node}, Python {python}, and Ruby {ruby}".format(
            **tomllib.loads((fake.ROOT / "config/mise.toml").read_text())["tools"]))

    def test_the_variable_of_a_version_manager_is_its_directory(self):
        """NVM_DIR, VOLTA_HOME, ...: the directory, even outside the home; the usual one is not tried then."""
        nvm = self.h.root / "opt/nvm"
        (nvm / "versions/node/v22.1.0").mkdir(parents=True)
        (nvm / "alias").mkdir()
        (nvm / "alias/default").write_text("22\n")
        self.h.mkdir(".nvm/versions/node/v18.0.0")
        self.h.mkdir("volta/tools/image/node/20.11.0")
        self.h.mkdir(".volta/tools/image/node/18.0.0")
        env = {"NVM_DIR": str(nvm), "VOLTA_HOME": "~/volta", "PYENV_ROOT": ""}
        self.h.mkdir(".pyenv/versions/3.12.2")
        s = runtimes.facts_of(fake.session(self.h, fake.brew_runner(), env).scan())
        vms = {vm.name: (vm.path, vm.versions) for vm in s.managers}
        self.assertEqual(vms["nvm"], (str(nvm), {"node": ["v22.1.0"]}))
        self.assertEqual(s.managers[0].default, {"node": "22"})
        self.assertEqual(vms["volta"], ("~/volta", {"node": ["20.11.0"]}))
        self.assertEqual(vms["pyenv"], ("~/.pyenv", {"python": ["3.12.2"]}), "an empty variable is not set")
        # A variable that points to nothing: that manager is not there, though ~/.nvm is.
        env["NVM_DIR"] = str(self.h.root / "missing")
        s = runtimes.facts_of(fake.session(self.h, fake.brew_runner(), env).scan())
        self.assertNotIn("nvm", [vm.name for vm in s.managers])

    def managers(self, env):
        s = runtimes.facts_of(fake.session(self.h, fake.brew_runner(), env).scan())
        return {vm.name: vm.path for vm in s.managers}

    def test_a_variable_that_is_not_a_directory_of_its_own(self):
        """A file, the home, or a parent of it is no manager; dirs are not tried then."""
        self.h.mkdir(".nvm/versions/node/v18.0.0")
        self.h.mkdir(".sdkman/candidates/java/17")
        self.h.write("nvm-file", "x\n")
        for env in ({"NVM_DIR": str(self.h.home / "nvm-file")}, {"NVM_DIR": str(self.h.home)},
                    {"NVM_DIR": str(self.h.home) + "/"}, {"NVM_DIR": str(self.h.root)}, {"NVM_DIR": "/"}):
            with self.subTest(env=env):
                self.assertNotIn("nvm", self.managers(env))
        self.assertNotIn("sdkman", self.managers({"SDKMAN_DIR": "~"}))
        self.assertEqual(self.managers({"SDKMAN_DIR": "~/.sdkman"})["sdkman"], "~/.sdkman")

    def test_the_home_by_another_name_is_no_manager(self):
        """By file identity: a link to the home, or the home's name in another case (APFS ignores case)."""
        self.h.mkdir(".nvm/versions/node/v18.0.0")
        os.symlink(self.h.home, self.h.root / "home-link")
        os.symlink(self.h.root, self.h.root / "root-link")
        for value in (str(self.h.root / "home-link"), str(self.h.root / "root-link"),
                      str(self.h.home.parent / self.h.home.name.upper())):
            with self.subTest(value=value):
                self.assertNotIn("nvm", self.managers({"NVM_DIR": value}))
        # A link to a directory of its own is that manager.
        os.symlink(self.h.home / ".nvm", self.h.root / "nvm-link")
        self.assertEqual(self.managers({"NVM_DIR": str(self.h.root / "nvm-link")})["nvm"],
                         str(self.h.root / "nvm-link"))

    def test_a_variable_with_spaces_around_it(self):
        nvm = self.h.root / "opt/nvm"
        (nvm / "versions/node/v22.1.0").mkdir(parents=True)
        self.assertEqual(self.managers({"NVM_DIR": f"  {nvm} \n"})["nvm"], str(nvm))

    def test_a_blank_or_relative_variable_counts_as_unset(self):
        for value in (" ", "x", ".", "~bob/.nvm"):
            with self.subTest(value=value):
                self.assertNotIn("nvm", self.managers({"NVM_DIR": value}))
        self.h.mkdir(".nvm/versions/node/v18.0.0")
        for value in (" ", "x", "."):
            with self.subTest(value=value):
                self.assertEqual(self.managers({"NVM_DIR": value})["nvm"], "~/.nvm")

    def test_the_first_fnm_directory_only(self):
        self.h.mkdir(".local/share/fnm/node-versions/v20.11.0/installation")
        self.h.mkdir("Library/Application Support/fnm/node-versions/v18.0.0/installation")
        s = self.scan()
        self.assertEqual([(vm.name, vm.path, vm.versions) for vm in s.managers],
                         [("fnm", "~/.local/share/fnm", {"node": ["v20.11.0"]})])

    def test_the_removal_quotes_the_path(self):
        self.h.mkdir("Library/Application Support/fnm/node-versions/v18.0.0/installation")
        report = fake.session(self.h, fake.brew_runner()).build(Choices(self.repo)).report
        self.assertIn("`rm -rf ~/'Library/Application Support/fnm'`", report)
        self.h.mkdir(".pyenv/versions/3.12.2")
        report = fake.session(self.h, fake.brew_runner()).build(Choices(self.repo)).report
        self.assertIn("`rm -rf ~/.pyenv`", report)

    def test_has_these_versions_only_when_they_were_added(self):
        """A template whose config/mise.toml has node already: add_tool refuses it, so the item says what to do."""
        template = self.h.root / "template"
        shutil.copytree(fake.ROOT / "templates/default", template, symlinks=True)
        (template / "config/mise.toml").write_text('[tools]\nnode = "18"\n')

        class TemplateBase(base.Base):
            @property
            def template(self):
                return template

        self.h.mkdir(".nvm/versions/node/v20.11.0")
        self.h.write(".nvm/alias/default", "20\n")
        ctx = fake.context_of(self.h, fake.brew_runner())
        ctx = dataclasses.replace(ctx, base=TemplateBase(fake.ROOT))
        p = registry.Session(ctx, strict=True)
        c = p.defaults(self.repo)
        c.picked["runtimes"] = {"runtime:node"}
        result = p.build(c)
        self.assertNotIn("config/mise.toml", result.files, "the template's file, unchanged")
        self.assertIn("nvm (~/.nvm): node v20.11.0 (default 20). With mise: `mise use -g node@20`.", result.report)
        self.assertNotIn("versions now", result.report)
        self.assertFalse(any(line.startswith("runtime versions") for line in result.summary))

    def test_one_manager_with_an_added_tool_and_one_of_yours(self):
        self.h.mkdir(".asdf/installs/nodejs/20.11.0")
        self.h.mkdir(".asdf/installs/ruby/3.3.0")
        self.h.write(".config/mise/config.toml", '[tools]\nruby = "3.2"\n')
        p = fake.session(self.h, fake.brew_runner())
        c = p.defaults(self.repo)
        c.picked["runtimes"] = {"runtime:node", "runtime:ruby"}
        result = p.build(c)
        self.assertEqual(tomllib.loads(result.files["config/mise.toml"])["tools"], {"ruby": "3.2", "node": "20.11.0"})
        self.assertIn("asdf (~/.asdf): node 20.11.0; ruby 3.3.0. config/mise.toml has the node versions now. "
                      "config/mise.toml has ruby from your mise config already, so these versions were not added; "
                      "`mise use -g ruby@<version>` for one you need. Then remove", result.report)
        self.assertIn("runtime versions of asdf -> config/mise.toml", result.summary)

    def test_a_runtime_that_your_kept_mise_config_has(self):
        """Kept in place with node in it: runtime:node gets nothing; ruby, not selected, keeps "With mise"."""
        self.h.mkdir(".asdf/installs/nodejs/20.11.0")
        self.h.mkdir(".asdf/installs/ruby/3.3.0")
        self.h.write(".config/mise/config.toml", '[tools]\nnode = "20"\n')
        p = fake.session(self.h, fake.brew_runner())
        c = p.defaults(self.repo)
        c.options["file:.config/mise/config.toml"] = "keep"
        c.picked["runtimes"] = {"runtime:node"}
        result = p.build(c)
        self.assertNotIn("config/mise.toml", result.files, "the template's file, unchanged")
        self.assertNotIn("`node` (", result.report)
        self.assertNotIn("Your ~/.config/mise/config.toml stays in place", result.report)
        self.assertNotIn("versions now", result.report)
        self.assertNotIn("mise use -g node@", result.report)
        self.assertFalse(any(line.startswith("runtime versions") for line in result.summary))
        self.assertIn("asdf (~/.asdf): node 20.11.0; ruby 3.3.0. With mise: `mise use -g ruby@3.3.0`. Then remove",
                      result.report)


if __name__ == "__main__":
    unittest.main()
