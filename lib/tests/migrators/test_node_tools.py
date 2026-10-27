"""The node_tools migrator: global tools of npm, pnpm, bun, and Volta."""

import json
import os
import unittest

from dotkit.migrate.migrators import node_tools, tools
from tests import fake
from tests.test_session import used_mac


class NodeToolsTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.repo = str(self.h.home / ".dotfiles")

    def scan(self, runner=None):
        facts = fake.session(self.h, runner or fake.brew_runner()).scan()
        self.assertEqual(facts.warnings, [])
        return node_tools.facts_of(facts)

    def test_npm_pnpm_bun_volta(self):
        def pkg(root, name, version, bin=True):
            self.h.json(f"{root}/{name}/package.json",
                        {"name": name, "version": version, **({"bin": {"x": "x.js"}} if bin else {})})

        nvm = ".nvm/versions/node/v20.11.0/lib/node_modules"
        pkg(nvm, "typescript", "5.4.0")
        pkg(nvm, "@anthropic-ai/claude-code", "1.0.0")
        pkg(nvm, "npm", "10.0.0")
        pkg(nvm, "left-pad", "1.0.0", bin=False)
        brew_root = self.h.prefix / "lib/node_modules/prettier"
        brew_root.mkdir(parents=True)
        (brew_root / "package.json").write_text(json.dumps({"name": "prettier", "version": "3.0.0", "bin": "x"}))
        keg = self.h.prefix / "opt/node@20/lib/node_modules/eslint"
        keg.mkdir(parents=True)
        (keg / "package.json").write_text(json.dumps({"name": "eslint", "version": "9.0.0", "bin": "x"}))
        os.symlink(self.h.mkdir("src/mine"), self.h.home / nvm / "mine")
        self.h.json("src/mine/package.json", {"name": "mine", "bin": "x"})
        self.h.json("Library/pnpm/global/5/package.json", {"dependencies": {"vercel": "^33.0.0"}})
        self.h.json(".bun/install/global/package.json", {"dependencies": {"cowsay": "1.6.0"}})
        self.h.json(".volta/tools/user/packages/yarn.json", {"name": "yarn", "version": "1.22.0"})
        # The npm on PATH, for example of a hosted tool cache. The scan does not run it.
        tool = self.h.root / "toolcache/node/22"
        (tool / "bin").mkdir(parents=True)
        (tool / "bin/npm").write_text("")
        (tool / "lib/node_modules/newman").mkdir(parents=True)
        (tool / "lib/node_modules/newman/package.json").write_text(
            json.dumps({"name": "newman", "version": "6.0.0", "bin": "x"}))
        runner = fake.brew_runner()
        runner.paths["npm"] = str(tool / "bin/npm")
        s = self.scan(runner)
        self.assertFalse([c for c in runner.calls if c.startswith("npm")], "npm does not run")
        got = {p.name: (p.version, p.where, p.ok) for p in s.packages}
        self.assertEqual(got.pop("newman"), ("6.0.0", f"npm in {tool}", True))
        self.assertEqual(got, {
            "@anthropic-ai/claude-code": ("1.0.0", "nvm v20.11.0", True),
            "typescript": ("5.4.0", "nvm v20.11.0", True),
            "prettier": ("3.0.0", "Homebrew node", True),
            "eslint": ("9.0.0", "Homebrew node@20", True),
            "mine": ("", "nvm v20.11.0", False),
            "vercel": ("33.0.0", "pnpm", True),
            "cowsay": ("1.6.0", "bun", True),
            "yarn": ("1.22.0", "volta", True),
        })

    def test_one_item_for_a_package_in_two_places(self):
        """A package twice in the scan (the npm scan keeps the first; pages() does not rely on it): one row."""
        p = fake.session(self.h, used_mac(self.h))
        node_tools.facts_of(p.scan()).packages.append(tools.Package("typescript", "5.5.0", "nvm v22.1.0"))
        npm = next(page for page in p.pages() if page.key == "npm")
        self.assertEqual([i.id for i in npm.items], ["npm:typescript"])
        result = p.build(p.defaults(self.repo))
        self.assertIn("1 npm tool -> config/mise.toml", result.summary)
        c = p.defaults(self.repo)
        c.picked["npm"] = set()
        self.assertEqual(p.build(c).report.count("`mise use -g npm:typescript`"), 2, "both copies stay")


    def pkg(self, root, name):
        p = root / name
        p.mkdir(parents=True)
        (p / "package.json").write_text(json.dumps({"name": name, "version": "1.0.0", "bin": "x"}))

    def test_the_npm_scan_follows_the_variables_of_the_version_managers(self):
        """NVM_DIR and VOLTA_HOME are the directories of nvm and Volta; the usual ones are not read then."""
        nvm = self.h.root / "opt/nvm"
        self.pkg(nvm / "versions/node/v22.1.0/lib/node_modules", "typescript")
        self.pkg(self.h.home / ".nvm/versions/node/v18.0.0/lib/node_modules", "stale")
        self.h.json("volta/tools/user/packages/yarn.json", {"name": "yarn", "version": "1.22.0"})
        self.h.json(".volta/tools/user/packages/old.json", {"name": "old", "version": "1.0.0"})
        env = {"NVM_DIR": str(nvm), "VOLTA_HOME": str(self.h.home / "volta")}
        s = node_tools.facts_of(fake.session(self.h, fake.brew_runner(), env).scan())
        self.assertEqual({p.name: p.where for p in s.packages}, {"typescript": "nvm v22.1.0", "yarn": "volta"})
        env["NVM_DIR"] = str(self.h.root / "missing")
        s = node_tools.facts_of(fake.session(self.h, fake.brew_runner(), env).scan())
        self.assertNotIn("stale", {p.name for p in s.packages}, "a variable that points to nothing: no nvm")

    def test_the_npm_scan_reads_the_first_fnm_directory_only(self):
        self.pkg(self.h.home / ".local/share/fnm/node-versions/v20.11.0/installation/lib/node_modules", "a")
        self.pkg(self.h.home / "Library/Application Support/fnm/node-versions/v18.0.0/installation/lib/node_modules",
                 "b")
        self.assertEqual([(p.name, p.where) for p in self.scan().packages], [("a", "fnm v20.11.0")])

if __name__ == "__main__":
    unittest.main()
