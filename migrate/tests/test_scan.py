import os
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

    def test_home_files(self):
        self.h.write(".zshrc", "source ~/.zshrc.local\n")
        self.h.write(".zshrc.local", "alias x=y\n")
        self.h.write(".zshenv.local", "export A=1\n")
        os.symlink("/nix/store/abc-home-manager-files/.zprofile", self.h.home / ".zprofile")
        self.h.write(".bashrc", "export B=1\n")
        self.h.write(".config/ghostty/config", "font-size = 16\n")
        self.h.write(".npmrc", fake.fill("//registry.npmjs.org/:_authToken=@NPM@\n"))
        self.h.write(".editorconfig", (fake.ROOT / "config/editorconfig").read_text())
        self.h.write(".gitconfig", "[user]\n\tname = Jane\n")
        self.h.write(".config/mise/config.toml", '[tools]\njq = "latest"\n')
        self.h.write(".config/nvim/init.lua", "vim.o.number = true\n")
        self.h.write(".config/app/state.db", "")
        self.h.write(".config/tok/config", "token = abcdefghijklmnop\n")
        self.h.write(".config/gh/hosts.yml", "github.com:\n  user: jane\n")
        self.h.write(".config/direnv/direnv.toml", "")
        self.h.write(".config/starship.toml", "")
        self.h.write(".config/big/data.json", "x" * 1_000_001)
        (self.h.mkdir(".config/bin") / "plugin.so").write_bytes(b"\x7fELF\0\0")
        os.mkfifo(self.h.mkdir(".config/fifo") / "pipe")
        os.mkfifo(self.h.home / ".config/toplevel-fifo")
        self.h.write(".config/linked/real", "a = 1\n")
        os.symlink("/etc/hosts", self.h.home / ".config/linked/hosts")
        s = self.scan()
        other = {o.rel: (o.adoptable, o.reason) for o in s.other_config}
        self.assertEqual(other[".config/nvim"], (True, ""))
        self.assertEqual(other[".config/app"], (False, "holds state or caches"))
        self.assertEqual(other[".config/tok"], (False, "has a secret"))
        self.assertEqual(other[".config/gh"], (False, "holds login tokens"))
        self.assertEqual(other[".bashrc"], (True, ""))
        # A file that cannot be checked for secrets is not for the repository; a FIFO is never read.
        self.assertEqual(other[".config/big"],
                         (False, "has a file that cannot be checked for secrets (larger than 1 MB)"))
        self.assertEqual(other[".config/bin"],
                         (False, "has a file that cannot be checked for secrets (a binary file)"))
        self.assertEqual(other[".config/fifo"], (False, "has a socket, a FIFO, or a device"))
        self.assertEqual(other[".config/toplevel-fifo"],
                         (False, "cannot be checked for secrets: not a regular file"))
        self.assertEqual(other[".config/linked"], (True, ""), "a link is copied as a link")
        self.assertNotIn(".config/direnv", other)
        self.assertIn(".config/gh/hosts.yml", s.secret_files)

    def test_finder_metadata_only_is_skipped_not_other_binaries(self):
        for app in ("omniwm", "tijd"):
            self.h.write(f".config/{app}/config.toml", "enabled = true\n")
            self.h.write(f".config/{app}/.DS_Store").write_bytes(b"finder\0metadata")
            self.h.write(f".config/{app}/nested/.DS_Store").write_bytes(b"finder\0metadata")
        self.h.write(".config/tijd/nested/plugin.dat").write_bytes(b"other\0binary")
        self.h.write(".config/metadata/.DS_Store").write_bytes(b"finder\0metadata")
        other = {o.rel: o for o in self.scan().other_config}
        self.assertTrue(other[".config/omniwm"].adoptable)
        self.assertEqual(other[".config/omniwm"].files, 1)
        self.assertFalse(other[".config/tijd"].adoptable)
        self.assertIn("a binary file", other[".config/tijd"].reason)
        self.assertEqual(other[".config/metadata"].reason, "empty")

    def test_a_failed_step_is_a_warning(self):
        class Broken(fake.FakeRunner):
            def run(self, argv, timeout=120, env=None):
                raise RuntimeError("boom")

        s = fake.session(self.h, Broken(paths={"brew": fake.BREW})).scan()
        self.assertTrue(any(w.startswith("homebrew: RuntimeError") for w in s.warnings), s.warnings)


if __name__ == "__main__":
    unittest.main()
