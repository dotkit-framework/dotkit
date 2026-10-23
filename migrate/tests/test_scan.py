import unittest

from tests import fake


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def scan(self, runner=None, env=None):
        self.facts = fake.session(self.h, runner or fake.brew_runner(), env).scan()
        self.assertEqual(self.facts.warnings, [])
        return self.facts.get("_rest")

    def test_identity(self):
        self.scan()
        self.assertEqual((self.facts.identity.git_name, self.facts.identity.git_email),
                         ("Jane Appleseed", "jane@example.com"))

    def test_a_failed_step_is_a_warning(self):
        class Broken(fake.FakeRunner):
            def run(self, argv, timeout=120, env=None):
                raise RuntimeError("boom")

        s = fake.session(self.h, Broken(paths={"brew": fake.BREW})).scan()
        self.assertTrue(any(w.startswith("homebrew: RuntimeError") for w in s.warnings), s.warnings)


    def test_login_files(self):
        self.h.write(".config/gh/hosts.yml", "github.com:\n  user: jane\n")
        self.assertEqual(self.scan().secret_files, [".config/gh/hosts.yml"])


if __name__ == "__main__":
    unittest.main()
