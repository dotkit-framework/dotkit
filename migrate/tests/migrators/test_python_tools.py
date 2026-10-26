"""The python_tools migrator: tools of pipx and uv tool."""

import unittest

from dotkit_migrate.migrators import python_tools
from tests import fake


class PythonToolsTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def scan(self, runner=None):
        facts = fake.session(self.h, runner or fake.brew_runner()).scan()
        self.assertEqual(facts.warnings, [])
        return python_tools.facts_of(facts)

    def test_pipx_and_uv(self):
        self.h.json(".local/pipx/venvs/ruff/pipx_metadata.json",
                    {"main_package": {"package": "ruff", "package_version": "0.4.0", "package_or_url": "ruff"}})
        self.h.json(".local/pipx/venvs/mine/pipx_metadata.json",
                    {"main_package": {"package": "mine", "package_or_url": "git+https://github.com/x/mine"}})
        self.h.write(".local/share/uv/tools/httpie/uv-receipt.toml",
                     '[tool]\nrequirements = [{ name = "httpie" }, { name = "httpie-plugin" }]\n')
        self.h.write(".local/share/uv/tools/local/uv-receipt.toml",
                     '[tool]\nrequirements = [{ name = "local", directory = "/src/local" }]\n')
        s = self.scan()
        got = {p.name: (p.where, p.ok, p.note) for p in s.packages}
        self.assertEqual(got["ruff"], ("pipx", True, ""))
        self.assertFalse(got["mine"][1])
        self.assertEqual(got["httpie"], ("uv tool", True, "with httpie-plugin"))
        self.assertFalse(got["local"][1])


if __name__ == "__main__":
    unittest.main()
