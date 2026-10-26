"""The writer: the plan of the documents, the plan-time guard, and write_new with its write-time verification."""

import os
import stat
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from dotkit_migrate import docs, report, secrets, writer
from tests import fake, test_golden
from tests.test_session import other_config, pick, used_mac

TEMPLATE = fake.ROOT / "templates/default"


def setup_docs(h: fake.Home) -> docs.Docs:
    return docs.Docs("setup", TEMPLATE, h.home / ".dotfiles", home=str(h.home), host="mac", strict=True)


def partial_dirs(h: fake.Home) -> list:
    return sorted(p.name for p in h.home.iterdir() if p.name.startswith(".dotfiles"))


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def test_guard(self):
        """A copy or a generated line with a secret is dropped, whatever put it there."""
        h = self.h
        h.write(".config/tool/config", fake.fill("token = @GH@\n"))
        d = setup_docs(h)
        t = d.begin("plan")
        t.copies.add("config/tool", h.home / ".config/tool")
        t.links().link(".config/tool", "config/tool")
        os.mkfifo(h.mkdir(".config/pipes") / "fifo")
        t.copies.add("config/pipes", h.home / ".config/pipes")
        t.mise().add_tool("npm:x", "latest", fake.fill("# @GH@"))
        # Lines that an earlier rule might miss: the last check catches them on its own.
        t.shell_rc(".zshrc").add_part("# From ~/.zshrc.", fake.fill(
            "export EDITOR=vim\n"
            "export WEIRD_NAME=@RANDOM@\n"
            "alias gh2='GITHUB_TOKEN=abcdef123456 gh'\n"
            "  curl -u admin:hunter2 https://example.com\n"
            "# @HEX40@\n"))
        t.commit()
        plan = writer.render(d)
        zshrc = plan.files["config/zsh/zshrc"]
        self.assertIn("\n# From ~/.zshrc.\nexport EDITOR=vim\n", zshrc)
        for value in (fake.TOKENS["@RANDOM@"], "abcdef123456", "hunter2", fake.TOKENS["@HEX40@"]):
            self.assertNotIn(value, zshrc)
        self.assertNotIn("config/tool", plan.copies)
        self.assertNotIn("config/pipes", plan.copies, "a file that cannot be checked counts as a secret")
        self.assertNotIn(".config/tool", plan.links)
        self.assertNotIn('".config/tool"', plan.files.get("nix/home.nix", ""))
        self.assertIn('"npm:x" = "latest"', plan.files["config/mise.toml"])
        self.assertNotIn(fake.TOKENS["@GH@"], plan.files["config/mise.toml"])
        self.assertIn("~/.config/tool has a secret, so it is not in the repository.",
                      d.report.sections["Secrets"].items)

    def test_final_guard_keeps_secret_shell_statements_whole(self):
        d = setup_docs(self.h)
        function = ('request() {\n  echo before\n  curl -H "X-Api-Key: ' + "\\\n" +
                    'hunter2hunter2" https://example.com\n  echo after\n}')
        t = d.begin("plan")
        t.shell_rc(".zshrc").add_part("# From ~/.zshrc.", function + "\nexport EDITOR=vim\n")
        t.commit()
        plan = writer.render(d)
        tracked = plan.files["config/zsh/zshrc"]
        self.assertEqual(plan.secrets, "")
        self.assertNotIn("request()", tracked)
        self.assertNotIn("echo before", tracked)
        self.assertNotIn("echo after", tracked)
        self.assertIn("export EDITOR=vim", tracked)
        self.assertNotIn("/local/", plan.files.get(".gitignore", ""))
        for shell in ("/bin/zsh", "/bin/bash"):
            self.assertEqual(subprocess.run([shell, "-n", "-c", tracked], capture_output=True).returncode, 0)
        self.assertNotIn("hunter2", report.render(d.report, "mac", "~/.dotfiles"))
        self.assertIn("config/zsh/zshrc: a statement with a secret is not in Git.", d.report.sections["Secrets"].items)

    def test_mask_lines(self):
        lines = ["safe\n", fake.fill("token = @GH@\n"), fake.fill("@NPM@")]
        self.assertEqual(writer.mask_lines(lines), ["safe\n", "(a line with a secret was here)\n",
                                                    "(a line with a secret was here)"])


class WriteTests(unittest.TestCase):
    def test_write(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = fake.session(h, used_mac(h))
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        writer.write_new(result, TEMPLATE)
        repo = h.home / ".dotfiles"
        self.assertTrue((repo / ".git").is_dir())
        self.assertFalse((repo.parent / ".dotfiles.partial").exists())
        self.assertTrue(os.access(repo / "dotkit", os.X_OK))
        self.assertEqual((repo / "config/ghostty.conf").read_text(), "font-size = 16\n")
        self.assertTrue((repo / "MIGRATION.md").read_text().startswith("# Migration from"))
        self.assertFalse((repo / "local").exists())
        self.assertIn("GITHUB_TOKEN", result.secret_names)
        self.assertFalse((h.home / ".secrets").exists(), "write_new does not write ~/.secrets")
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
                                capture_output=True, text=True, check=True).stdout
        self.assertIn("MIGRATION.md", status)

    def test_nested_finder_metadata_is_not_copied(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/nvim/init.lua", "vim.o.number = true\n")
        h.json(".config/nvim/lazy-lock.json", {"lazy.nvim": {"commit": fake.TOKENS["@HEX40@"]}})
        h.write(".config/nvim/nested/settings.lua", "return {}\n")
        for rel in (".config/nvim/.DS_Store", ".config/nvim/nested/.DS_Store"):
            h.write(rel).write_bytes(b"metadata\0binary")
        p = fake.session(h, fake.brew_runner())
        self.assertIn(".config/nvim", other_config(p))
        c = p.defaults(str(h.home / ".dotfiles"))
        pick(c, "other:.config/nvim")
        result = p.build(c)
        self.assertIn("config/nvim", result.copies, "the final guard agrees with assessment")
        writer.write_new(result, TEMPLATE)
        copied = result.repo / "config/nvim"
        self.assertEqual(list(copied.rglob(".DS_Store")), [])
        self.assertTrue((copied / "nested/settings.lua").is_file())
        self.assertIn(fake.TOKENS["@HEX40@"], (copied / "lazy-lock.json").read_text())

    def test_write_leaves_other_directories_alone(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = fake.session(h, used_mac(h))
        result = p.build(p.defaults(str(h.home / "code")))
        other = h.write("code.partial/keep.txt", "mine\n")
        writer.write_new(result, TEMPLATE)
        self.assertEqual(other.read_text(), "mine\n")
        self.assertEqual(sorted(x.name for x in h.home.iterdir() if x.name.startswith("code")),
                         ["code", "code.partial"])
        with self.assertRaises(FileExistsError):
            writer.write_new(result, TEMPLATE)
        self.assertEqual(stat.S_IMODE((h.home / "code").stat().st_mode), 0o755)


class VerifyTests(unittest.TestCase):
    """The write-time verification: a secret that got past the plan-time guard stops the write."""

    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        p = fake.session(self.h, used_mac(self.h))
        c = p.defaults(str(self.h.home / ".dotfiles"))
        pick(c, "other:.config/nvim")
        self.plan = p.build(c)
        self.assertIn("config/nvim", self.plan.copies)

    def assert_aborts(self, what: str) -> None:
        with self.assertRaises(writer.SecretInRepository) as e:
            writer.write_new(self.plan, TEMPLATE)
        self.assertIn(what, str(e.exception))
        for token in fake.TOKENS.values():
            self.assertNotIn(token, str(e.exception), "the message names the file, never the value")
        self.assertEqual(partial_dirs(self.h), [], "nothing is left behind")

    def test_a_generated_file_with_a_secret(self):
        self.plan.files["config/extra.txt"] = fake.fill("safe\nexport GH=@GH@\n")
        self.assert_aborts("config/extra.txt (line 2: ")

    def test_a_copied_file_that_changed_after_the_scan(self):
        self.h.write(".config/nvim/later.lua", fake.fill('local key = "@OPENAI@"\n'))
        self.assert_aborts("config/nvim/later.lua (line 1: ")

    def test_a_copied_file_that_cannot_be_checked(self):
        self.h.write(".config/nvim/blob.bin").write_bytes(b"\0\1\2")
        self.assert_aborts("config/nvim/blob.bin (a binary file)")

    def test_a_read_only_directory_with_a_secret_is_removed(self):
        """A copy brings a 0555 directory with a token: the write aborts, and the cleanup still removes it."""
        ro = self.h.mkdir(".config/nvim/ro")
        (ro / "key.lua").write_text(fake.fill('return "@GH@"\n'))
        ro.chmod(0o555)
        self.addCleanup(ro.chmod, 0o755)
        self.assert_aborts("config/nvim/ro/key.lua (line 1: ")

    def test_a_directory_that_cannot_be_read_aborts(self):
        """The verification fails closed: a staged directory at mode 0000 is a find."""
        copy_template = writer._copy_template

        def with_a_closed_directory(src, dest):
            copy_template(src, dest)
            (dest / "closed").mkdir()
            (dest / "closed/file").write_text("x\n")
            (dest / "closed").chmod(0)

        with mock.patch.object(writer, "_copy_template", with_a_closed_directory):
            self.assert_aborts("closed (cannot be read: Permission denied)")

    def test_build_is_not_checked(self):
        """.build/ is not tracked: a link name that looks random does not stop the write."""
        name = ".config/" + fake.TOKENS["@RANDOM@"]
        self.assertNotEqual(secrets.scan_text(name + "\n"), [], "the name looks like a secret")
        self.plan.move_links.append(name)
        writer.write_new(self.plan, TEMPLATE)
        self.assertIn(name, (self.plan.repo / ".build/links-in-way").read_text())

    def test_secrets_stay_out_of_the_repository(self):
        self.assertIn(fake.TOKENS["@GH@"], self.plan.secrets)
        writer.write_new(self.plan, TEMPLATE)
        self.assertEqual(partial_dirs(self.h), [".dotfiles"])
        self.assertEqual(writer.verify(self.plan.repo, self.plan), [])
        for root, _dirs, files in os.walk(self.plan.repo):
            for name in files:
                self.assertNotIn(fake.TOKENS["@GH@"].encode(), Path(root, name).read_bytes(), name)

    def test_write_secrets_appends(self):
        path = self.h.write(".secrets", "export OLD=1", mode=0o600)
        writer.write_secrets(self.plan)
        self.assertEqual(path.read_text(), "export OLD=1\n\n" + self.plan.secrets)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_the_golden_plans_write(self):
        """The verification never fires on the plans of the golden tests."""
        for name, (make, choose) in test_golden.PLANS.items():
            with self.subTest(name=name):
                h = fake.Home()
                self.addCleanup(h.cleanup)
                runner, env = make(h)
                p = fake.session(h, runner, env, check=test_golden.check_syntax)
                repo = str(h.home / ".dotfiles")
                plan = p.build(choose(p, repo) if choose else p.defaults(repo))
                writer.write_new(plan, TEMPLATE)
                self.assertTrue(Path(repo, ".git").is_dir())


if __name__ == "__main__":
    unittest.main()
