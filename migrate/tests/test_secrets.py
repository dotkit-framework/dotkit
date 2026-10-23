import time
import unittest

from dotkit_migrate import secrets
from tests import fake


class SecretTests(unittest.TestCase):
    def test_found(self):
        lines = {
            "//registry.npmjs.org/:_authToken=@NPM@": "an npm token",
            "//npm.pkg.github.com/:_authToken=abcdef123456": "_authToken",
            "export GITHUB_TOKEN=@GH@": "a GitHub token",
            "export HOMEBREW_GITHUB_API_TOKEN=abcdef123456": "HOMEBREW_GITHUB_API_TOKEN",
            "  oauth_token: @GHO@": "a GitHub token",
            "export AWS_ACCESS_KEY_ID=@AWS@": "an AWS access key",
            "password = hunter2hunter2": "password",
            'url = "https://jane:s3cretpass@example.com/repo"': "a password in a URL",
            "-----BEGIN OPENSSH PRIVATE KEY-----": "a private key",
            "# old: export X=@GH@": "a GitHub token",
            "export SLACK=@SLACK@": "a Slack token",
            "\textraheader = AUTHORIZATION: basic eHgtYWNjZXNzLXRva2VuOmFiY2RlZg==": "an authorization header",
        }
        for line, what in lines.items():
            self.assertEqual(secrets.line_secret(fake.fill(line)), what, line)

    def test_found_anywhere_in_a_line(self):
        """The review cases: names after typeset, in aliases, in function bodies, in comments."""
        lines = {
            "export PAT=abcdef123456ghijkl": "PAT",
            "export DB_PASS=hunter2hunter2": "DB_PASS",
            "export OPENAI_KEY=abcd1234efgh5678": "OPENAI_KEY",
            "export STRIPE_KEY=@STRIPE@": "a Stripe key",
            "typeset -x API_TOKEN=abcdef123456": "API_TOKEN",
            "declare -x MY_SECRET='abcdef123456'": "MY_SECRET",
            "alias gh2='GITHUB_TOKEN=abcdef123456 gh'": "GITHUB_TOKEN",
            "f() { export API_KEY=abcdef123456; }": "API_KEY",
            "  curl -u admin:hunter2 https://example.com/api": "a user and password",
            '  curl -H "X-Api-Key: abcdef123456" https://example.com': "a key header",
            "# export GITHUB_TOKEN=@HEX40@": "a 40-character hex string",
            "# export OLD_TOKEN=abcdef123456": "OLD_TOKEN",
            "# @HEX40@": "a 40-character hex string",
            "export FOO=@RANDOM@": "a random-looking value",
            "[ -n $X ] && export SERVICE_PASSWORD=abcdef123456": "SERVICE_PASSWORD",
            "export X=1; export DEPLOY_KEY=abcdef123456": "DEPLOY_KEY",
        }
        for line, what in lines.items():
            self.assertEqual(secrets.line_secret(fake.fill(line)), what, line)

    def test_password_settings_options_and_webhooks(self):
        for line in (
            "alias k='kubectl --token=abcdef0123456789abcdef'",
            "alias k='kubectl --api-key=abcdef0123456789abcdef'",
            "export MYSQL_PWD=hunter2hunter2",
            "export WEBHOOK=https://example.com/callback/abcdef",
            "password hunter2hunter2", "passwd hunter2hunter2", "pass hunter2hunter2",
            "secret hunter2hunter2", "token hunter2hunter2",
            "curl --password hunter2hunter2 https://example.com",
            "sshpass -p hunter2hunter2 ssh example.com",
            "https://hooks.slack.com/services/T123/B456/abcdef",
        ):
            self.assertIsNotNone(secrets.line_secret(line), line)
        for line in ('password $MYSQL_PWD', 'sshpass -p "$PASS" ssh host',
                     'curl --password "$PASS" https://example.com'):
            self.assertIsNone(secrets.line_secret(line), line)

    def test_all_sshpass_calls_are_checked(self):
        reference = 'sshpass -p "$FIRST_PASS" ssh host1 true'
        literal = 'sshpass -p hunter2hunter2 ssh host2 true'
        for calls in ((reference, literal), (literal, reference)):
            line = 'deploy() { ' + ' && '.join(calls) + '; }'
            with self.subTest(line=line):
                self.assertEqual(secrets.line_secret(line), "an sshpass password")
        self.assertIsNone(secrets.line_secret(
            reference + ' && sshpass -p "${SECOND_PASS}" ssh host2 true'))

    def test_compact_json_credentials(self):
        for line, name in (
            ('{"token":"hunter2hunter2"}', "token"),
            ('{"api-key":"hunter2hunter2"}', "api-key"),
            ('{"auth":{"password":"hunter2hunter2"}}', "password"),
            ('{"token":"$TOKEN","password":"hunter2hunter2"}', "password"),
            ('{"token":"","password":"hunter2hunter2"}', "password"),
            ("{'token':'hunter2hunter2'}", "token"),
        ):
            with self.subTest(line=line):
                self.assertEqual(secrets.line_secret(line), name)
        for line in ('{"token":"$TOKEN"}', '{"token":""}', '{"token":"abc"}',
                     '{"url":"https://example.com/repo","name":"my-config"}'):
            with self.subTest(line=line):
                self.assertIsNone(secrets.line_secret(line))

    def test_compact_json_files_and_revision_neighbors(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        path = h.write("config.json", '{"token":"hunter2hunter2"}\n')
        self.assertEqual(secrets.scan_file(path), [secrets.Secret(1, "token")])
        for field in ("commit", "rev", "sha", "hash", "revision"):
            revision = '"' + field + '":"@HEX40@"'
            clean = h.write("lazy-lock.json", fake.fill('{"lazy.nvim":{' + revision + '}}'))
            self.assertEqual(secrets.scan_file(clean), [])
            for fields in (revision + ',"token":"hunter2hunter2"',
                           '"token":"hunter2hunter2",' + revision):
                line = fake.fill('{' + fields + '}')
                with self.subTest(line=line):
                    self.assertEqual(secrets.line_secret(line), "token")
                    path = h.write("config.json", line + "\n")
                    self.assertEqual(secrets.scan_file(path), [secrets.Secret(1, "token")])
                    self.assertNotIn("hunter2", repr(secrets.scan_file(path)))

    def test_revisions_are_not_secrets_but_neighbors_are(self):
        for field in ("commit", "rev", "sha", "hash", "revision"):
            line = fake.fill('"' + field + '": "@HEX40@"')
            self.assertIsNone(secrets.line_secret(line), line)
            self.assertIsNotNone(secrets.line_secret(line + fake.fill(', "token": "@HEX40@"')), line)
            self.assertIsNotNone(secrets.line_secret(line + ', "token": "hunter2hunter2"'), line)
            self.assertIsNotNone(secrets.line_secret(line + fake.fill(' # @GH@')), line)
        for url in ('git+https://github.com/x/tool#@HEX40@',
                    'https://github.com/x/tool.git@XX',
                    'https://github.com/x/tool?rev=@HEX40@&branch=main'):
            line = fake.fill(url.replace('XX', '@HEX40@'))
            self.assertIsNone(secrets.line_secret(line), line)
            self.assertIsNotNone(secrets.line_secret(line + fake.fill(' token=@HEX40@')), line)
            self.assertIsNotNone(secrets.line_secret(line + fake.fill(' # @RANDOM@')), line)
        self.assertIsNone(secrets.line_secret('export AWS_PROFILE=MyCompanyProd2024Admin'))
        self.assertIsNotNone(secrets.line_secret(fake.fill('token=@HEX40@')))

    def test_continued_header(self):
        text = 'curl -H "X-Api-Key: ' + "\\\n" + 'hunter2hunter2" https://example.com'
        self.assertTrue(secrets.scan_text(text))

    def test_random_words(self):
        self.assertTrue(secrets.random_word(fake.fill("x = @RANDOM@")))
        for line in ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFake9k2m3n4b5v6c7x8z jane@mac",
                     "sjc20-bb712-804e9ce3-bd15-4298-b1ea-689594927d84-4673F8DA0004",
                     "ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE=fg=243", "font-jetbrains-mono-nerd-font",
                     "export NODE_OPTIONS=--max-old-space-size=4096", "/opt/homebrew/opt/postgresql@16/bin"):
            self.assertFalse(secrets.random_word(line), line)

    def test_unscannable_files(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        big = h.write("big", "x" * 1_000_001)
        binary = h.home / "bin.dat"
        binary.write_bytes(b"abc\0def")
        fifo = h.home / "fifo"
        import os
        os.mkfifo(fifo)
        for p in (big, binary, fifo, h.home / "missing"):
            with self.assertRaises(secrets.Unscannable, msg=str(p)):
                secrets.scan_file(p)
            self.assertTrue(secrets.has_secret(p))
        self.assertFalse(secrets.has_secret(h.write("ok", "font-size = 14\n")))

    def test_not_secrets(self):
        for line in (
            "export GITHUB_TOKEN=$(security find-generic-password -s gh -w)",
            'export NPM_TOKEN="${NPM_TOKEN}"',
            "//registry.npmjs.org/:_authToken=${NPM_TOKEN}",
            "export PASSWORD_STORE_DIR=~/.password-store",
            "export EDITOR=vim",
            "export GIT_AUTHOR_NAME='Jane Appleseed'",
            "credential.helper = osxkeychain",
            "# a comment about tokens",
            "export TOKEN=",
            "export API_KEY=abc",
            "url = https://github.com/jane/repo",
        ):
            self.assertIsNone(secrets.line_secret(line), line)

    def test_a_name_inside_the_value_of_another(self):
        """Every name=/name: position is checked, also one inside the value of an earlier match (step 10a)."""
        lines = {
            "export JAVA_OPTS=-Dapi.password=hunter2hunter2": "Dapi.password",
            "export PIP_INDEX_URL=https://pypi.example.com/simple?token=hunter2hunter2": "token",
            "Note: PASSWORD=hunter2hunter2": "PASSWORD",
            "run: PGPASSWORD=hunter2hunter2 psql": "PGPASSWORD",
            "x=TOKEN=hunter2hunter2": "TOKEN",
        }
        for line, what in lines.items():
            self.assertEqual(secrets.line_secret(line), what, line)
        for line in ("export PATH=$HOME/bin:$PATH", "export LESS=-R", "alias ll='ls -l'"):
            self.assertIsNone(secrets.line_secret(line), line)

    def test_a_large_file_is_still_fast(self):
        """A smoke test: many ordinary lines, 1 MB, took about 0.45 s (0.37 s before step 10a).

        The long lines of test_long_names_stay_linear test the slower path, each with its own bound.
        """
        line = "export PATH=$HOME/bin:$PATH; alias ll='ls -l' # a=b c=d e: f url=https://x.example.com/a?b=c&d=e\n"
        text = line * (1_000_000 // len(line))
        start = time.perf_counter()
        self.assertEqual(secrets.scan_text(text), [])
        self.assertLess(time.perf_counter() - start, 10.0)

    def test_long_names_stay_linear(self):
        """Names and URL schemes are bounded, so a long line of their characters is scanned in linear time."""
        for line in ("x=" + "-".join(["foo", "bar", "baz"] * 2300)[:20_000], "x=" + "a-" * 10_000):
            start = time.perf_counter()
            secrets.line_secret(line)
            self.assertLess(time.perf_counter() - start, 0.5, line[:20])
        name = "a" * 190 + "_PASSWORD"
        self.assertEqual(secrets.line_secret(f"export {name}=hunter2hunter2"), name)
        # A URL scheme has at most 32 characters: a long run of scheme characters in front still ends at "://".
        self.assertEqual(secrets.line_secret("see " + "a-" * 500 + "https://jane:hunter2hunter2@example.com"),
                         "a password in a URL")

    def test_scan_text_has_lines_not_values(self):
        found = secrets.scan_text(fake.fill("a=1\nexport GITHUB_TOKEN=@GH@\n"))
        self.assertEqual(found, [secrets.Secret(2, "a GitHub token")])
        self.assertNotIn("ghp_", repr(found))

    def test_known_files(self):
        self.assertTrue(secrets.known_secret_file(".config/gh/hosts.yml"))
        self.assertTrue(secrets.known_secret_file(".config/gcloud/credentials.db"))
        self.assertFalse(secrets.known_secret_file(".config/gh/config.yml"))


if __name__ == "__main__":
    unittest.main()
