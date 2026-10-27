"""lift.py and secrets_file.py: the secrets of config files as variables of ~/.secrets."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from dotkit.migrate import lift, secrets_file
from tests import fake

GH, NPM = fake.TOKENS["@GH@"], fake.TOKENS["@NPM@"]


class NpmrcTests(unittest.TestCase):
    def test_settings(self):
        t = lift.npmrc(f"; c\n# {GH}\nregistry = https://npm.acme.dev/\n//npm.acme.dev/:_authToken = \"{NPM}\"\n"
                       "//npm.acme.dev/:username=jane\n//npm.acme.dev/:_password=aHVudGVyMmh1bnRlcjI=\n"
                       "@acme:registry=https://npm.acme.dev/\n_authToken=" + NPM + "\nsave-exact=true\n")
        self.assertEqual([(s.key, s.value, s.var) for s in t.settings], [
            ("registry", "https://npm.acme.dev/", ""),
            ("//npm.acme.dev/:_authToken", NPM, "NPM_ACME_DEV_NPM_TOKEN"),
            ("//npm.acme.dev/:username", "jane", ""),
            ("//npm.acme.dev/:_password", "aHVudGVyMmh1bnRlcjI=", "NPM_ACME_DEV_NPM_PASSWORD"),
            ("@acme:registry", "https://npm.acme.dev/", ""),
            ("_authToken", NPM, "NPM_TOKEN"),
        ])
        self.assertEqual(t.other, ["save-exact"])

    def test_a_registry_without_a_token_is_another_setting(self):
        t = lift.npmrc("registry=https://mirror.example.com/\n")
        self.assertEqual((t.settings, t.other), ([], ["registry"]))

    def test_references_stay(self):
        t = lift.npmrc("//npm.pkg.github.com/:_authToken=${GITHUB_TOKEN}\n")
        self.assertEqual([(s.key, s.value, s.var) for s in t.settings],
                         [("//npm.pkg.github.com/:_authToken", "${GITHUB_TOKEN}", "")])

    def test_a_secret_elsewhere(self):
        for text in (f"proxy=http://jane:{NPM}@proxy:8080\n", f"@acme:registry=https://jane:{NPM}@npm.acme.dev/\n",
                     f"weird line {GH}\n"):
            with self.subTest(text=text):
                self.assertIsNone(lift.npmrc(text))


class GitconfigTests(unittest.TestCase):
    def test_settings_and_sections(self):
        text = (f'[user]\n\tname = Jane\n[url "https://x:{GH}@github.com/"]\n\tinsteadOf = https://github.com/\n'
                f'\t# for {GH}\n\tpushInsteadOf\n[http "https://example.com"]\n'
                f'\textraHeader = "Authorization: Bearer {GH}" ; ci\n[github]\n\ttoken={GH}\n# {GH}\n[core]\n'
                '\teditor = vim # mine\n')
        out, taken = lift.gitconfig(text)
        self.assertEqual(out, '[user]\n\tname = Jane\n[http "https://example.com"]\n[github]\n[core]\n'
                              '\teditor = vim # mine\n')
        self.assertEqual(taken, [
            (f"url.https://x:{GH}@github.com/.insteadOf", "https://github.com/"),
            (f"url.https://x:{GH}@github.com/.pushInsteadOf", "true"),
            ("http.https://example.com.extraHeader", f"Authorization: Bearer {GH}"),
            ("github.token", GH),
        ])

    def test_values(self):
        for raw, value in (('"a b" c', "a b c"), ('a\\tb', "a\tb"), ('"x ; y" ; z', "x ; y"), ("  a  b  ", "a  b"),
                           ('"q\\"q"', 'q"q')):
            with self.subTest(raw=raw):
                self.assertEqual(lift._git_value(raw), value)
        for raw in ('"open', "a\\", "a\\x"):
            with self.subTest(raw=raw):
                self.assertIsNone(lift._git_value(raw))

    def test_what_it_cannot_move(self):
        for text in (f"token = {GH}\n", f"[github]\n\ttoken = {GH}\\\n\tmore\n", f"[a]\n\tb = c\\\n{GH}\n"):
            with self.subTest(text=text):
                self.assertIsNone(lift.gitconfig(text))

    def test_git_reads_the_variables(self):
        """The real Git, with the settings as GIT_CONFIG_* variables of a ~/.secrets file."""
        _, taken = lift.gitconfig(f'[url "https://x:{GH}@github.com/"]\n\tinsteadOf = https://github.com/\n'
                                  f'[http]\n\textraHeader = "Authorization: Bearer {GH}"\n')
        lines = [secrets_file.export_line("GIT_CONFIG_COUNT", str(len(taken)))]
        for i, (k, v) in enumerate(taken):
            lines += [secrets_file.export_line(f"GIT_CONFIG_KEY_{i}", k),
                      secrets_file.export_line(f"GIT_CONFIG_VALUE_{i}", v)]
        with tempfile.TemporaryDirectory(prefix="dotkit-migrate-test-") as d:
            path = Path(d, "secrets")
            path.write_text("\n".join(lines) + "\n")
            env = {"HOME": d, "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1"}
            out = subprocess.run(["/bin/sh", "-c", '. "$1"; git config --get http.extraheader; '
                                  'git ls-remote --get-url https://github.com/a/b', "sh", str(path)],
                                 capture_output=True, text=True, env=env, cwd=d).stdout.splitlines()
        self.assertEqual(out, [f"Authorization: Bearer {GH}", f"https://x:{GH}@github.com/a/b"])


class SecretsFileTests(unittest.TestCase):
    def test_read(self):
        text = ("export A='x y'\nB=\"q\\\"q\"\ntypeset -x C=plain\n# export D=no\nexport E=$HOME/x\nexport F=a'b'\"c\"\n"
                "export A=again\n")
        self.assertEqual(secrets_file.read(text), {"A": "again", "B": 'q"q', "C": "plain", "E": "$HOME/x",
                                                   "F": "abc"})

    def test_export_line_round_trip(self):
        for value in ("plain", "with space", "it's", "a\"b$c`d\\e", ""):
            with self.subTest(value=value):
                line = secrets_file.export_line("V", value)
                self.assertEqual(secrets_file.read(line), {"V": value})
                for sh in ("/bin/sh", "/bin/bash", "/bin/zsh"):
                    out = subprocess.run([sh, "-c", line + '; printf %s "$V"'], capture_output=True,
                                         text=True, check=True).stdout
                    self.assertEqual(out, value, sh)
        with self.assertRaises(ValueError):
            secrets_file.export_line("1A", "x")


if __name__ == "__main__":
    unittest.main()
