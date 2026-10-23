"""The built_tools migrator: tools of cargo install and the Go bin directory."""

import unittest

from dotkit_migrate.migrators import built_tools
from tests import fake


class BuiltToolsTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.repo = str(self.h.home / ".dotfiles")

    def scan(self, runner=None, env=None):
        facts = fake.session(self.h, runner or fake.brew_runner(), env).scan()
        self.assertEqual(facts.warnings, [])
        return built_tools.facts_of(facts)

    def test_cargo_and_go(self):
        self.h.json(".cargo/.crates2.json", {"installs": {
            "ripgrep 14.1.0 (registry+https://github.com/rust-lang/crates.io-index)": {},
            "tool 0.1.0 (git+https://github.com/x/tool#abc)": {},
        }})
        self.h.write(".cargo/bin/cargo", "", 0o755)
        gopls = self.h.write("go/bin/gopls", "", 0o755)
        gopls.write_bytes(b"\x00junk\xff Go buildinf:\npath\tgolang.org/x/tools/gopls\n"
                          b"mod\tgolang.org/x/tools/gopls\tv0.15.0\th1:abc\n\x00")
        self.h.write("go/bin/plain", "#!/bin/sh\n", 0o755)
        s = self.scan()
        self.assertTrue(s.has_cargo)
        self.assertEqual([(p.name, p.version, p.ok) for p in s.cargo],
                         [("ripgrep", "14.1.0", True), ("tool", "0.1.0", False)])
        self.assertFalse(s.has_go)
        self.assertEqual([(p.name, p.version, p.ok) for p in s.go],
                         [("golang.org/x/tools/gopls", "v0.15.0", True), ("plain", "", False)])

    def test_go_where_has_the_home_as_tilde(self):
        self.h.write("go/bin/plain", "#!/bin/sh\n", 0o755)
        self.assertEqual([p.where for p in self.scan().go], ["~/go/bin/plain"])
        # A GOBIN outside the home stays absolute.
        gobin = self.h.root / "gobin"
        gobin.mkdir()
        (gobin / "tool").write_text("#!/bin/sh\n")
        (gobin / "tool").chmod(0o755)
        self.assertEqual([p.where for p in self.scan(env={"GOBIN": str(gobin)}).go], [str(gobin / "tool")])

    def test_cargo_git_revision_report_survives(self):
        source = fake.fill("git+https://github.com/x/tool#@HEX40@")
        self.h.json(".cargo/.crates2.json", {"installs": {f"tool 0.1.0 ({source})": {}}})
        p = fake.session(self.h, fake.brew_runner())
        result = p.build(p.defaults(self.repo))
        self.assertIn(source, result.report)


if __name__ == "__main__":
    unittest.main()
