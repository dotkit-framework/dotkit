"""The shell migrator: the lines of the shell files, by file, through rcparse."""

import os
import stat
import subprocess
import unittest
from unittest import mock

from dotkit.migrate import writer
from dotkit.migrate.migrators import shell
from dotkit.migrate.ui import screens
from tests import fake
from tests.test_session import TOKEN


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.repo = str(self.h.home / ".dotfiles")

    def test_scan(self):
        self.h.write(".zshrc", "source ~/.zshrc.local\nexport EDITOR=vim\n")
        self.h.write(".zshrc.local", "alias x=y\n")
        self.h.write(".zshenv.local", "export A=1\n")
        os.symlink("/nix/store/abc-home-manager-files/.zprofile", self.h.home / ".zprofile")
        self.h.write(".bashrc", "export B=1\n")
        facts = fake.session(self.h, fake.brew_runner()).scan()
        self.assertEqual(facts.warnings, [])
        s = shell.facts_of(facts)
        files = {f.name: (f.managed, f.read) for f in s.files}
        self.assertEqual(files, {".zshrc": (False, True), ".zshrc.local": (False, True),
                                 ".zshenv.local": (False, False), ".zprofile": (True, True)})
        self.assertEqual(s.other_files, [".bashrc"])
        # The scan parses the full chain, for the page labels: not the files that were not read.
        self.assertEqual(list(s.results), [".zshrc", ".zshrc.local"])
        self.assertEqual(s.results[".zshrc"].statements, 1)

    def test_sshpass_function_is_not_carried(self):
        """A function with a secret is no assignment for ~/.secrets: the report names it, without its text."""
        reference = 'sshpass -p "$FIRST_PASS" ssh host1 true'
        literal = 'sshpass -p hunter2hunter2 ssh host2 true'
        for i, calls in enumerate(((reference, literal), (literal, reference))):
            with self.subTest(calls=calls):
                function = 'deploy() { ' + ' && '.join(calls) + '; }'
                self.h.write(".zshrc", function + "\nexport EDITOR=vim\n")
                p = fake.session(self.h, fake.brew_runner())
                c = p.defaults(str(self.h.home / f"repo-{i}"))
                result = p.build(c)
                self.assertEqual(result.secrets, "")
                self.assertIn("export EDITOR=vim", result.files["config/zsh/zshrc"])
                for text in result.files.values():
                    self.assertNotIn("deploy()", text)
                    self.assertNotIn("hunter2", text)
                self.assertNotIn("hunter2", result.report)
                self.assertIn("- ~/.zshrc:1: the function on this line, with an sshpass password\n", result.report)
                self.assertIn(shell.NOT_MOVED_INTRO, result.report)
                writer.write_new(result, fake.ROOT / "templates/default")
                self.assertFalse((result.repo / "local").exists())
                self.assertFalse((self.h.home / ".secrets").exists())

    def test_a_shell_file_with_only_secrets(self):
        """It is an item, on by default: its variables go to ~/.secrets."""
        self.h.write(".zshrc", f"export GITHUB_TOKEN={TOKEN}\n")
        p = fake.session(self.h, fake.brew_runner())
        shell_page = next(page for page in p.pages() if page.key == "shell")
        self.assertEqual([(i.id, i.label, i.default) for i in shell_page.items],
                         [("shell:.zshrc", "~/.zshrc: 1 line with a secret", True)])
        result = p.build(p.defaults(self.repo))
        self.assertEqual(result.secrets, "# Added by the dotkit migration. See MIGRATION.md.\n# From ~/.zshrc.\n"
                                         f"export GITHUB_TOKEN={TOKEN}\n")
        self.assertEqual(result.secret_names, ["GITHUB_TOKEN"])
        self.assertNotIn("local/", result.files["config/zsh/zshrc"])
        self.assertIn("- ~/.zshrc:1: `GITHUB_TOKEN`\n", result.report)
        self.assertIn("1 variable with a secret of ~/.zshrc | ~/.secrets", result.report)
        # Deselected: one item under "Not carried, by your choice", as the review counts it.
        c = p.defaults(self.repo)
        c.picked["shell"] = set()
        result = p.build(c)
        self.assertEqual(result.secrets, "")
        self.assertIn("- ~/.zshrc: Its lines with secrets are not carried: `GITHUB_TOKEN` (line 1). Keep them out "
                      "of files that Git tracks.\n", result.report)
        self.assertNotIn(TOKEN, result.report)

    def test_variables_pass_only_through_carried_files(self):
        """A deselected file passes on no variables: a line that uses one is reported, not carried."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".zshenv", "export DEV=~/dev\n")
        h.write(".zprofile", "export WORK=~/work\n")
        h.write(".zshrc", 'export GOPATH="$DEV/go"\nalias g="cd $DEV"\nexport TOOLS="$WORK/tools"\n')
        p = fake.session(h, fake.brew_runner())
        repo = str(h.home / ".dotfiles")
        # The page counts from the full chain.
        shell = next(page for page in p.pages() if page.key == "shell")
        self.assertEqual([i.label for i in shell.items],
                         ["~/.zshenv: 1 line", "~/.zprofile: 1 line", "~/.zshrc: 3 lines"])
        everything = p.build(p.defaults(repo))
        zshrc = everything.files["config/zsh/zshrc"]
        for line in ('export GOPATH="$DEV/go"', 'alias g="cd $DEV"', 'export TOOLS="$WORK/tools"'):
            self.assertIn(line, zshrc)
        c = p.defaults(repo)
        c.picked["shell"].discard("shell:.zshenv")
        result = p.build(c)
        zshrc = result.files["config/zsh/zshrc"]
        self.assertNotIn("GOPATH", zshrc)
        self.assertIn('alias g="cd $DEV"', zshrc, "aliases are not expanded, as before")
        # .zprofile is carried, so its variable still reaches .zshrc.
        self.assertIn('export TOOLS="$WORK/tools"', zshrc)
        self.assertNotIn("config/zsh/zshenv", result.files)
        exports = result.report[result.report.index("### Shell lines: Exports with commands or other variables"):]
        self.assertIn('- ~/.zshrc:1 `export GOPATH="$DEV/go"`', exports.split("\n### ")[0])
        self.assertIn("2 lines of ~/.zshrc -> config/zsh/zshrc (~/.zshrc.local)", result.summary)
        self.assertIn("- ~/.zshenv: 1 line that could be carried to config/zsh/zshenv.", result.report)
        # The full chain is unchanged by the other build: the cache is per selection.
        self.assertEqual(p.build(p.defaults(repo)).files, everything.files)

    def test_a_deselected_file_says_what_its_page_offered(self):
        """Both files deselected: the ~/.zshrc item counts from the full chain, as its page label does."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".zshenv", "export DEV=~/dev\n")
        h.write(".zshrc", 'export GOPATH="$DEV/go"\n')
        p = fake.session(h, fake.brew_runner())
        c = p.defaults(str(h.home / ".dotfiles"))
        shell = next(page for page in p.pages() if page.key == "shell")
        self.assertEqual([i.label for i in shell.items], ["~/.zshenv: 1 line", "~/.zshrc: 1 line"])
        c.picked["shell"] = set()
        result = p.build(c)
        self.assertIn("- ~/.zshrc: 1 line that could be carried to config/zsh/zshrc. Copy the ones you need.\n",
                      result.report)
        self.assertIn("- ~/.zshenv: 1 line that could be carried to config/zsh/zshenv. Copy the ones you need.\n",
                      result.report)
        self.assertEqual(screens.not_carried(p.pages(), c), [(shell, 2)])
        lines = screens.review_lines(p.build(c), p.pages(), c)
        self.assertEqual(lines[lines.index("Not carried, by your choice:") + 1:], ["  Shell lines: 2"])
        for rel, text in result.files.items():
            if rel.startswith("config/zsh/"):
                self.assertNotIn("DEV", text, rel)
                self.assertNotIn("GOPATH", text, rel)

    def test_names_inside_values_go_to_secrets(self):
        """Step 10a: a secret name inside the value of another assignment sends the line to ~/.secrets."""
        java = "export JAVA_OPTS=-Dapi.password=hunter2hunter2"
        pip = "export PIP_INDEX_URL=https://pypi.example.com/simple?token=hunter2hunter2"
        self.h.write(".zshrc", f"{java}\n{pip}\nexport LESS=-R\n")
        s = fake.session(self.h, fake.brew_runner())
        p = s.build(s.defaults(self.repo))
        self.assertTrue(p.secrets.endswith(f"# From ~/.zshrc.\n{java}\n{pip}\n"))
        self.assertIn("export LESS=-R", p.files["config/zsh/zshrc"])
        self.assertNotIn("hunter2", "".join(p.files.values()) + p.report + "\n".join(p.summary))

    def test_secrets_that_are_there_already(self):
        """~/.secrets keeps its variables: the same value is not added again, another one is refused."""
        self.h.write(".secrets", f"export GITHUB_TOKEN='{TOKEN}'\nexport NPM_TOKEN=npm_old\n", mode=0o600)
        self.h.write(".zshrc", f"export GITHUB_TOKEN=\"{TOKEN}\"\nexport NPM_TOKEN=npm_{'a' * 36}\n"
                               f"typeset -x SLACK_TOKEN=xoxb-{'b' * 20}\nAPI_KEY=sk-{'c' * 30}\n")
        s = fake.session(self.h, fake.brew_runner())
        p = s.build(s.defaults(self.repo))
        self.assertEqual(p.secret_names, ["SLACK_TOKEN", "API_KEY"])
        self.assertEqual(p.secrets, "# Added by the dotkit migration. See MIGRATION.md.\n# From ~/.zshrc.\n"
                                    f"export SLACK_TOKEN=xoxb-{'b' * 20}\nAPI_KEY=sk-{'c' * 30}\n")
        self.assertIn("- ~/.zshrc:2: `NPM_TOKEN`. ~/.secrets has `NPM_TOKEN` with another value already, so this "
                      "one is not added. Check which one you need.\n", p.report)
        self.assertNotIn("npm_old", p.report)
        self.assertNotIn("~/.secrets can be read by other users", p.report)
        writer.write_secrets(p)
        text = (self.h.home / ".secrets").read_text()
        self.assertTrue(text.startswith(f"export GITHUB_TOKEN='{TOKEN}'\nexport NPM_TOKEN=npm_old\n\n# Added"))
        # A second run adds nothing.
        again = fake.session(self.h, fake.brew_runner())
        self.assertEqual(again.build(again.defaults(self.repo)).secrets, "")

    def test_secrets_file_that_others_can_read(self):
        self.h.write(".secrets", "export A=1\n", mode=0o644)
        s = fake.session(self.h, fake.brew_runner())
        p = s.build(s.defaults(self.repo))
        self.assertIn("- ~/.secrets can be read by other users (mode 644). Run `chmod 600 ~/.secrets`.\n", p.report)

    def test_write_secrets_creates_the_file_for_its_owner_only(self):
        self.h.write(".zshrc", f"export GITHUB_TOKEN={TOKEN}\n")
        s = fake.session(self.h, fake.brew_runner())
        p = s.build(s.defaults(self.repo))
        writer.write_secrets(p)
        path = self.h.home / ".secrets"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.read_text(), p.secrets)
        # sh, bash, and zsh read it.
        for sh in ("/bin/sh", "/bin/bash", "/bin/zsh"):
            out = subprocess.run([sh, "-c", '. "$1"; printf %s "$GITHUB_TOKEN"', sh, str(path)],
                                 capture_output=True, text=True, check=True).stdout
            self.assertEqual(out, TOKEN, sh)

    def test_failed_syntax_check_reports_the_lines(self):
        self.h.write(".zshrc", "f() {\n  echo hi\n}\nexport A=1\n")
        s = fake.session(self.h, fake.brew_runner(), check=lambda shell, text: "f()" not in text)
        p = s.build(s.defaults(self.repo))
        self.assertIn("export A=1", p.files["config/zsh/zshrc"])
        self.assertNotIn("echo hi", p.files["config/zsh/zshrc"])
        self.assertIn("zsh -n could not check it", p.report)

    def test_a_failed_scan(self):
        """No shell page and nothing of your files, the problem, and the core "Login shell" to-do."""
        self.h.write(".zshrc", "export EDITOR=vim\n")
        with mock.patch.object(shell.Shell, "scan", side_effect=RuntimeError("boom")):
            p = fake.session(self.h, fake.brew_runner(), {"SHELL": "/bin/bash"}, strict=False)
            facts = p.scan()
        self.assertEqual([(f.migrator, f.phase) for f in p.failures], [("shell", "scan")])
        self.assertNotIn("shell", [page.key for page in p.pages()])
        result = p.build(p.defaults(self.repo))
        self.assertFalse([rel for rel in result.files if rel.startswith(("config/zsh/", "config/bash/"))])
        self.assertNotIn("EDITOR", "".join(result.files.values()))
        self.assertIn("- **Shell lines**: failed while scanning: `RuntimeError: boom`", result.report)
        self.assertIn("### Login shell\n\nYour login shell is /bin/bash. dotkit makes the zsh of Nix the login "
                      "shell, so the config of bash is not used in new terminals.\n", result.report)


if __name__ == "__main__":
    unittest.main()
