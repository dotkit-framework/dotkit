"""The other_config migrator: other config in ~/.config and dotfiles, copied into the repository on request."""

import os
import unittest

from dotkit.migrate import writer
from dotkit.migrate.migrators import other_config
from tests import fake
from tests.test_session import other_config as other_items, pick, used_mac


class OtherConfigTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.repo = str(self.h.home / ".dotfiles")

    def scan(self, runner=None):
        facts = fake.session(self.h, runner or fake.brew_runner()).scan()
        self.assertEqual(facts.warnings, [])
        return other_config.facts_of(facts)

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
        other = {o.rel: (o.adoptable, o.reason) for o in s.found}
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

    def test_finder_metadata_only_is_skipped_not_other_binaries(self):
        for app in ("omniwm", "tijd"):
            self.h.write(f".config/{app}/config.toml", "enabled = true\n")
            self.h.write(f".config/{app}/.DS_Store").write_bytes(b"finder\0metadata")
            self.h.write(f".config/{app}/nested/.DS_Store").write_bytes(b"finder\0metadata")
        self.h.write(".config/tijd/nested/plugin.dat").write_bytes(b"other\0binary")
        self.h.write(".config/metadata/.DS_Store").write_bytes(b"finder\0metadata")
        other = {o.rel: o for o in self.scan().found}
        self.assertTrue(other[".config/omniwm"].adoptable)
        self.assertEqual(other[".config/omniwm"].files, 1)
        self.assertFalse(other[".config/tijd"].adoptable)
        self.assertIn("a binary file", other[".config/tijd"].reason)
        self.assertEqual(other[".config/metadata"].reason, "empty")

    def test_compact_json_credentials_cannot_be_adopted(self):
        configs = {
            "token": '{"token":"hunter2hunter2"}',
            "neighbor": '{"commit":"@HEX40@","token":"hunter2hunter2"}',
            "reverse": '{"token":"hunter2hunter2","revision":"@HEX40@"}',
        }
        for name, text in configs.items():
            self.h.write(f".config/{name}/config.json", fake.fill(text) + "\n")
        self.h.write(".config/clean/lazy-lock.json", fake.fill('{"lazy.nvim":{"commit":"@HEX40@"}}\n'))
        p = fake.session(self.h, fake.brew_runner())
        for name in configs:
            with self.subTest(name=name):
                self.assertNotIn(f".config/{name}", other_items(p))
        self.assertIn(".config/clean", other_items(p))
        c = p.defaults(self.repo)
        # A forced choice must not bypass the assessment or the final guard.
        pick(c, *[f"other:.config/{name}" for name in configs], "other:.config/clean")
        result = p.build(c)
        for name in configs:
            self.assertNotIn(f"config/{name}", result.copies)
            self.assertNotIn(f".config/{name}", result.links)
        self.assertIn("config/clean", result.copies)
        self.assertNotIn("hunter2", result.report)
        for text in result.files.values():
            self.assertNotIn("hunter2", text)
        writer.write_new(result, fake.ROOT / "templates/default")
        for name in configs:
            self.assertFalse((result.repo / "config" / name).exists())
        self.assertIn(fake.TOKENS["@HEX40@"], (result.repo / "config/clean/lazy-lock.json").read_text())

    def test_pi(self):
        """~/.pi/agent: its config entries, each offered on its own; logins and state stay; the old place is not offered."""
        self.h.write(".pi/agent/settings.json", '{"theme": "dark"}\n')
        self.h.write(".pi/agent/AGENTS.md", "Be brief.\n")
        self.h.write(".pi/agent/extensions/hello.ts", "export default () => {};\n")
        self.h.write(".pi/agent/models.json", fake.fill('{"providers": {"x": {"apiKey": "@NPM@"}}}\n'))
        self.h.write(".pi/agent/auth.json", fake.fill('{"anthropic": {"key": "@GH@"}}\n'))
        self.h.write(".pi/agent/sessions/one.jsonl", "{}\n")
        self.h.write(".config/pi/settings.json", '{"theme": "light"}\n')
        s = self.scan()
        found = {o.rel: (o.adoptable, o.reason, o.dest) for o in s.found}
        self.assertEqual({k: v for k, v in found.items() if k.startswith(".pi/")}, {
            ".pi/agent/settings.json": (True, "", "config/pi/settings.json"),
            ".pi/agent/AGENTS.md": (True, "", "config/pi/AGENTS.md"),
            ".pi/agent/models.json": (False, "has a secret", "config/pi/models.json"),
            ".pi/agent/extensions": (True, "", "config/pi/extensions"),
        })
        self.assertNotIn(".config/pi", found)
        self.assertEqual([(o.rel, o.app, o.root) for o in s.old], [(".config/pi", "pi", ".pi/agent")])
        p = fake.session(self.h, fake.brew_runner())
        self.assertEqual([i for i in other_items(p) if "pi" in i],
                         [".pi/agent/settings.json", ".pi/agent/AGENTS.md", ".pi/agent/extensions"])
        c = p.defaults(self.repo)
        pick(c, "other:.pi/agent/settings.json", "other:.pi/agent/extensions")
        result = p.build(c)
        self.assertEqual(result.copies["config/pi/settings.json"], self.h.home / ".pi/agent/settings.json")
        self.assertEqual(result.links[".pi/agent/extensions"], "config/pi/extensions")
        self.assertNotIn(".config/pi", result.links)
        self.assertIn("- ~/.config/pi: an old place of the pi config. pi reads ~/.pi/agent now, so ~/.config/pi has "
                      "no effect, and it is not offered. Delete it when you no longer need it.\n", result.report)
        self.assertIn("- ~/.pi/agent/auth.json: on a new Mac, `/login` in pi\n", result.report)
        self.assertIn("- ~/.pi/agent/AGENTS.md\n", result.report)

    def test_other_config_whose_place_is_taken(self):
        """A copy goes only to a free repository path; the other file stays in place, with a to-do."""
        cases = {
            "an earlier copy": ({".npmrc": "prefix=${HOME}/.local\n", ".config/npmrc": "color=false\n"},
                                {"file:.npmrc": "replace"}, ".config/npmrc", "config/npmrc", "~/.npmrc"),
            "a merged file": ({".config/git/config": "[pull]\n\trebase = true\n", ".gitconfig": "[core]\n",
                               ".config/gitconfig": "[x]\n"},
                              {}, ".config/gitconfig", "config/gitconfig", "~/.config/git/config and ~/.gitconfig"),
        }
        for why, (files, choices, rel, dest, has) in cases.items():
            with self.subTest(why):
                h = fake.Home()
                self.addCleanup(h.cleanup)
                for name, text in files.items():
                    h.write(name, text)
                p = fake.session(h, fake.brew_runner())
                self.assertIn(rel, other_items(p))
                c = p.defaults(str(h.home / ".dotfiles"))
                c.options.update(choices)
                pick(c, f"other:{rel}")
                result = p.build(c)
                self.assertNotIn(rel, result.links)
                if why == "an earlier copy":
                    self.assertEqual(result.copies[dest], h.home / ".npmrc")
                    self.assertEqual(result.links[".npmrc"], dest)
                else:
                    self.assertNotIn(dest, result.copies)
                    self.assertTrue(result.files[dest].startswith("# From ~/.config/git/config.\n"))
                    self.assertEqual(result.links[".gitconfig"], dest)
                self.assertIn(f"- ~/{rel}: its place in the repository, {dest}, has {has} already. It stays in "
                              "place. To keep it in the repository, copy it under another name and link it in "
                              "nix/home.nix.\n", result.report)
                self.assertNotIn(f"~/{rel} -> {dest}", result.report)

    def test_other_config_that_would_contain_a_document_path(self):
        """~/.config/bash would go to config/bash, which holds config/bash/bash_profile: it stays in place."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".bash_profile", "export BASH_THING=1\n")
        h.write(".config/bash/aliases", "alias ll='ls -la'\n")
        h.write(".config/bash2/aliases", "alias la='ls -A'\n")
        p = fake.session(h, fake.brew_runner())
        self.assertIn(".config/bash", other_items(p))
        c = p.defaults(str(h.home / ".dotfiles"))
        pick(c, "other:.config/bash", "other:.config/bash2")
        result = p.build(c)
        self.assertIn("export BASH_THING=1", result.files["config/bash/bash_profile"])
        self.assertNotIn("config/bash", result.copies)
        self.assertNotIn(".config/bash", result.links)
        self.assertIn("- ~/.config/bash: its place in the repository, config/bash, would contain "
                      "config/bash/bash_profile; it stays in place. To keep it in the repository, copy it under "
                      "another name and link it in nix/home.nix.\n", result.report)
        self.assertNotIn("~/.config/bash -> config/bash\n", result.report)
        # Path parts, not string prefixes: config/bash2 is not inside config/bash.
        self.assertEqual(result.copies["config/bash2"], h.home / ".config/bash2")
        self.assertEqual(result.links[".config/bash2"], "config/bash2")

    def test_other_config_inside_a_copied_directory(self):
        """~/.config/home goes to config/home first, so ~/.vimrc has no place at config/home/vimrc."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/home/settings", "x = 1\n")
        h.write(".vimrc", "set number\n")
        p = fake.session(h, fake.brew_runner())
        self.assertEqual(other_items(p), [".config/home", ".vimrc"])
        c = p.defaults(str(h.home / ".dotfiles"))
        pick(c, "other:.config/home", "other:.vimrc")
        result = p.build(c)
        self.assertEqual(result.copies, {"config/home": h.home / ".config/home"})
        self.assertNotIn(".vimrc", result.links)
        self.assertIn("- ~/.vimrc: its place in the repository, config/home/vimrc, is inside config/home, which "
                      "has ~/.config/home already. It stays in place. To keep it in the repository, copy it under "
                      "another name and link it in nix/home.nix.\n", result.report)

    def test_other_config_paths_compare_like_apfs(self):
        """Without case: ~/.config/Zsh would contain config/zsh/...; ~/.config/Home holds config/home/vimrc."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/Zsh/aliases.zsh", "alias ll='ls -la'\n")
        h.write(".config/Home/settings", "x = 1\n")
        h.write(".vimrc", "set number\n")
        p = fake.session(h, fake.brew_runner())
        self.assertEqual(other_items(p), [".config/Home", ".config/Zsh", ".vimrc"])
        c = p.defaults(str(h.home / ".dotfiles"))
        pick(c, *[f"other:{rel}" for rel in other_items(p)])
        result = p.build(c)
        self.assertEqual(result.copies, {"config/Home": h.home / ".config/Home"})
        self.assertEqual({t: s for t, s in result.links.items() if s and s.startswith("config/")},
                         {".config/Home": "config/Home"})
        self.assertIn("- ~/.config/Zsh: its place in the repository, config/Zsh, would contain "
                      "config/zsh/zprofile; it stays in place. To keep it in the repository, copy it under "
                      "another name and link it in nix/home.nix.\n", result.report)
        self.assertIn("- ~/.vimrc: its place in the repository, config/home/vimrc, is inside config/Home, which "
                      "has ~/.config/Home already. It stays in place. To keep it in the repository, copy it under "
                      "another name and link it in nix/home.nix.\n", result.report)
        writer.write_new(result, fake.ROOT / "templates/default")
        self.assertEqual((result.repo / "config/zsh/zshrc").read_text(),
                         (fake.ROOT / "templates/default/config/zsh/zshrc").read_text())
        self.assertEqual(sorted(x.name for x in (result.repo / "config").iterdir()), ["Home", "mise.toml", "zsh"])

    def test_adopt_other_config(self):
        session = fake.session(self.h, used_mac(self.h))
        c = session.defaults(self.repo)
        pick(c, "other:.config/nvim", "other:.bashrc")
        p = session.build(c)
        self.assertEqual(p.links[".config/nvim"], "config/nvim")
        self.assertEqual(p.links[".bashrc"], "config/home/bashrc")
        self.assertEqual(p.copies["config/nvim"], self.h.home / ".config/nvim")


if __name__ == "__main__":
    unittest.main()
