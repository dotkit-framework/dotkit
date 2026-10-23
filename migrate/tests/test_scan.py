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
        got = {p.name: (p.where, p.ok, p.note) for p in s.pipx}
        self.assertEqual(got["ruff"], ("pipx", True, ""))
        self.assertFalse(got["mine"][1])
        self.assertEqual(got["httpie"], ("uv tool", True, "with httpie-plugin"))
        self.assertFalse(got["local"][1])

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

    def test_version_managers_and_frameworks(self):
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
        self.h.mkdir(".oh-my-zsh")
        self.h.write(".p10k.zsh", "")
        s = self.scan()
        vms = {vm.name: (vm.versions, vm.default) for vm in s.version_managers}
        self.assertEqual(vms["nvm"], ({"node": ["v18.20.0", "v20.11.0"]}, {"node": "20"}))
        self.assertEqual(vms["pyenv"], ({"python": ["3.9.18", "3.11.4"]}, {"python": "3.11.4"}))
        self.assertEqual(vms["asdf"], ({"go": ["1.22.0"], "node": ["18.0.0"]}, {"node": "18.0.0", "go": "1.22.0"}))
        self.assertEqual(vms["sdkman"], ({"java": ["17.0.9-tem"]}, {"java": "17.0.9-tem"}))
        self.assertEqual(s.frameworks, {"Oh My Zsh": "~/.oh-my-zsh", "Powerlevel10k": "~/.p10k.zsh"})
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
        vms = {vm.name: (vm.path, vm.tool, vm.versions, vm.default) for vm in s.version_managers}
        self.assertEqual(list(vms), ["fnm", "volta", "rbenv", "jenv"])
        self.assertEqual(vms["fnm"], ("~/.local/share/fnm", "node", {"node": ["v20.11.0"]}, {"node": "v20.11.0"}))
        self.assertEqual(vms["volta"], ("~/.volta", "node", {"node": ["20.11.0"]}, {"node": "20.11.0"}))
        self.assertEqual(vms["rbenv"], ("~/.rbenv", "ruby", {"ruby": ["3.3.0"]}, {"ruby": "3.3.0"}))
        self.assertEqual(vms["jenv"], ("~/.jenv", "java", {"java": ["17"]}, {}))

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
        files = {f.name: (f.managed, f.read) for f in s.shell_files}
        self.assertEqual(files, {".zshrc": (False, True), ".zshrc.local": (False, True),
                                 ".zshenv.local": (False, False), ".zprofile": (True, True)})
        self.assertEqual(s.other_shell_files, [".bashrc"])
        cfg = {c.target: (bool(c.secrets), c.same_as_base) for c in s.config_files}
        self.assertEqual(cfg, {".config/ghostty/config": (False, False), ".npmrc": (True, False),
                               ".editorconfig": (False, True), ".config/starship.toml": (False, False)})
        self.assertIsNotNone(s.gitconfig)
        self.assertEqual(s.mise_config, self.h.home / ".config/mise/config.toml")
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
