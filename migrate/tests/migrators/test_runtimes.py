"""The runtimes migrator: the versions of version managers, and the other files that mise reads."""

import os
import unittest

from dotkit_migrate._compat import tomllib
from dotkit_migrate.migrators import runtimes
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


if __name__ == "__main__":
    unittest.main()
