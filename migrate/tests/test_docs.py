"""The documents and transactions of docs.py: load, add, render, conflicts, checks, commit and rollback."""

import unittest
from pathlib import Path

from dotkit_migrate import docs, report
from dotkit_migrate._compat import tomllib
from tests import fake

TEMPLATE = fake.ROOT / "templates/default"
HOME = "/Users/jane"


def setup_docs(strict: bool = True) -> docs.Docs:
    """Documents for setup. Strict by default, so that a conflict raises; tests of the other mode say so."""
    return docs.Docs("setup", TEMPLATE, Path(HOME, ".dotfiles"), home=HOME, host="mac", strict=strict)


class ListDocTests(unittest.TestCase):
    def test_add_and_render(self):
        d = setup_docs()
        t = d.begin("homebrew")
        brews = t.list("nix/brews.txt")
        self.assertTrue(brews.add("wget"))
        self.assertTrue(brews.add("hashicorp/tap/terraform"))
        self.assertFalse(brews.add("wget"), "the same entry again")
        self.assertEqual(brews.added(), ["wget", "hashicorp/tap/terraform"])
        self.assertEqual(brews.names(), ["wget", "hashicorp/tap/terraform"])
        t.commit()
        files, _ = d.render()
        template = (TEMPLATE / "nix/brews.txt").read_text()
        self.assertEqual(files["nix/brews.txt"], template.rstrip("\n") +
                         "\n\n# From Homebrew on mac, see MIGRATION.md.\nwget\nhashicorp/tap/terraform\n")
        self.assertNotIn("nix/casks.txt", files)

    def test_app_store_header_and_labels(self):
        d = setup_docs()
        t = d.begin("appstore")
        mas = t.list("nix/mas.txt")
        self.assertTrue(mas.add("409183694 Keynote"))
        self.assertTrue(mas.add("1234 Keynote"), "a taken name gets a label")
        self.assertFalse(mas.add("1234 Keynote"), "the same app again, also with its label")
        self.assertFalse(mas.add("1234 Keynote (1234)"))
        self.assertTrue(mas.add("7 Pages"))
        self.assertTrue(mas.add("9 Pages (8)"))
        with self.assertRaisesRegex(ValueError, r"other apps have this name and `Pages \(8\)`"):
            mas.add("8 Pages")
        self.assertTrue(mas.add("1274495053 Notes \u270f\ufe0f # 2"))
        self.assertEqual(mas.names(), ["Keynote", "Keynote (1234)", "Pages", "Pages (8)", "Notes \u270f\ufe0f # 2"])
        t.commit()
        text = d.render()[0]["nix/mas.txt"]
        self.assertTrue(text.endswith("\n# From the App Store on mac, see MIGRATION.md.\n409183694 Keynote\n"
                                      "1234 Keynote (1234)\n7 Pages\n9 Pages (8)\n1274495053 Notes \u270f\ufe0f # 2\n"))

    def test_checks_at_add(self):
        d = setup_docs()
        t = d.begin("x")
        cases = {
            "nix/brews.txt": {"two words": "one word", "a#b": "one word", "": "one word"},
            "nix/taps.txt": {
                "notatap": "its name is not owner/repo",
                "a/b https://example.com/a b.git": "it has a space",
                "a/b https://example.com/a.git#main": "it has a `#`",
                'a/b https://example.com/"x"': 'it has a `"`',
                "a/b https://example.com/a\\b": "it has a `\\\\`",
                "a/b ": "it is empty",
                # The secret check comes first, also before the space.
                fake.fill("a/b https://me:@GH@@git.example.com/homebrew tap.git"): r"it has a secret \(",
            },
            "nix/mas.txt": {"x12 Name": "its ID is not digits", "12 ": "its name is empty",
                            "12 a\rb": "its name has a line break"},
        }
        for path, entries in cases.items():
            for entry, why in entries.items():
                with self.subTest(path=path, entry=entry):
                    with self.assertRaisesRegex(ValueError, why):
                        t.list(path).add(entry)
        with self.assertRaises(ValueError):
            t.list("nix/brews.txt").add("jq", comment="no hash")
        with self.assertRaises(ValueError):
            t.list("nix/other.txt")
        self.assertTrue(t.list("nix/taps.txt").add("acme/tools git@gitlab.com:acme/homebrew-tools.git"))
        t.commit()
        self.assertEqual(d.get("nix/brews.txt").added(), [])

    def test_mas_ids_are_ascii_digits(self):
        self.assertEqual(docs.mas_problem("\u0661\u0662\u0663", "Arabic-Indic digits"), "its ID is not digits")
        self.assertIsNone(docs.mas_problem(123, "Name"))

    def test_tap_problem(self):
        self.assertIsNotNone(docs.tap_name_problem("a/b c"))
        self.assertIsNone(docs.tap_name_problem("a/b"))
        self.assertIsNone(docs.tap_problem("hashicorp/tap"))
        self.assertIsNone(docs.tap_problem("acme/tools git@gitlab.com:acme/homebrew-tools.git"))
        self.assertEqual(docs.tap_problem("a/b https://x/a b"), "it has a space")

    def test_base_text_wins_and_conflicts(self):
        repo = fake.Home()
        self.addCleanup(repo.cleanup)
        repo.write("r/nix/taps.txt", "# mine\nacme/tools https://example.com/tools.git\n")
        repo.write("r/nix/mas.txt", "1 Pages\n")
        d = docs.Docs("adopt", TEMPLATE, repo.home / "r", home=str(repo.home), host="mac", strict=False)
        t = d.begin("homebrew")
        taps = t.list("nix/taps.txt")
        self.assertFalse(taps.add("acme/tools"), "the base text has the tap")
        self.assertTrue(taps.add("x/y https://example.com/y.git"))
        self.assertFalse(taps.add("x/y"), "a conflict: the first value stays")
        self.assertEqual(len(d.conflicts), 1)
        c = d.conflicts[0]
        self.assertEqual((c.path, c.key, c.first, c.second), ("nix/taps.txt", "x/y", "homebrew", "homebrew"))
        self.assertEqual(t.list("nix/mas.txt").names(), ["Pages"])
        self.assertTrue(t.list("nix/mas.txt").add("2 Pages"))
        t.commit()
        files, _ = d.render()
        # Adopt mode: after the existing text, with one comment for the run.
        self.assertEqual(files["nix/taps.txt"], "# mine\nacme/tools https://example.com/tools.git\n"
                                                "# Added by dotkit migrate on mac.\nx/y https://example.com/y.git\n")
        self.assertEqual(files["nix/mas.txt"], "1 Pages\n# Added by dotkit migrate on mac.\n2 Pages (2)\n")

    def test_strict_conflict_raises(self):
        d = setup_docs(strict=True)
        t = d.begin("a")
        t.list("nix/taps.txt").add("x/y")
        t.commit()
        t = d.begin("b")
        with self.assertRaisesRegex(docs.ConflictError, "b sets x/y"):
            t.list("nix/taps.txt").add("x/y https://example.com/y.git")
        t.rollback()

    def test_a_comment_comes_once(self):
        d = setup_docs()
        t = d.begin("x")
        t.list("nix/casks.txt").add("zed", "# Editors.")
        t.list("nix/casks.txt").add("cursor", "# Editors.")
        t.commit()
        self.assertTrue(d.render()[0]["nix/casks.txt"].endswith("see MIGRATION.md.\n# Editors.\nzed\ncursor\n"))


class MiseDocTests(unittest.TestCase):
    def build(self, ops, base=None):
        d = setup_docs()
        t = d.begin("x")
        m = t.mise()
        if base is not None:
            self.assertTrue(m.set_base(base))
        out = ops(m)
        t.commit()
        return d, out

    def test_tools(self):
        def ops(m):
            return [m.add_tool("npm:typescript", "latest", "# From npm, see MIGRATION.md."),
                    m.add_tool("npm:eslint", "latest", "# From npm, see MIGRATION.md."),
                    m.add_tool("python", ["3.12.2", "3.11.8"]),
                    m.add_tool("uv", {"version": "latest", "minimum_release_age": "1d", "n": 2, "on": True}),
                    m.add_tool("jq", "latest"), m.add_tool("jq", "1.7"), m.has_tool("jq"), m.has_tool("python")]
        d, out = self.build(ops, base='[tools]\njq = "latest"\n\n[settings]\nexperimental = true\n')
        self.assertEqual(out, [True, True, True, True, False, False, True, True])
        self.assertEqual(d.conflicts, [], "the base text wins without a conflict")
        files, _ = d.render()
        text = files["config/mise.toml"]
        self.assertIn('[tools]\njq = "latest"\n# From npm, see MIGRATION.md.\n"npm:typescript" = "latest"\n'
                      '"npm:eslint" = "latest"\n"python" = ["3.12.2", "3.11.8"]\n'
                      '"uv" = { version = "latest", minimum_release_age = "1d", n = 2, on = true }\n\n[settings]\n',
                      text)
        self.assertTrue(text.startswith((TEMPLATE / "config/mise.toml").read_text().split("[tools]")[0]))
        self.assertIn("\n# From ~/.config/mise/config.toml.\n", text)
        self.assertEqual(tomllib.loads(text)["settings"], {"experimental": True})
        self.assertEqual(d.get("config/mise.toml").added(), ["npm:typescript", "npm:eslint", "python", "uv"])

    def test_checks_at_add(self):
        d = setup_docs()
        t = d.begin("x")
        m = t.mise()
        for key, value in (("", "1"), ("a\nb", "1"), ("a", 1), ("a", ["1", 2]), ("a", {"x": [1]}),
                           ("a", {"x": {"y": 1}}), ("a", None)):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    m.add_tool(key, value)
        t.rollback()

    def test_render_never_fails_on_accepted_entries(self):
        def ops(m):
            m.add_tool('we"ird\\key\t', 'a "quoted" \\ value\nwith a line break \x01')
            m.add_tool("t", {"a b": "x", "c": False})
        d, _ = self.build(ops)
        files, _ = d.render()
        data = tomllib.loads(files["config/mise.toml"])
        self.assertEqual(data["tools"]['we"ird\\key\t'], 'a "quoted" \\ value\nwith a line break \x01')
        self.assertEqual(data["tools"]["t"], {"a b": "x", "c": False})
        self.assertNotIn("mise", d.report.sections)

    def test_a_base_that_does_not_parse(self):
        d, _ = self.build(lambda m: m.add_tool("npm:x", "latest", "# c"), base="[tools\n")
        files, _ = d.render()
        self.assertNotIn("config/mise.toml", files, "the template, unchanged")
        sec = d.report.sections["mise"]
        self.assertTrue(sec.blocks[0].intro.startswith("config/mise.toml would not parse ("))
        self.assertEqual(sec.items, ['`"npm:x" = "latest"`'])

    def test_unchanged_is_not_written(self):
        d, _ = self.build(lambda m: m.add_tool("node", "24"), base="")
        self.assertIn("config/mise.toml", d.render()[0])
        d, _ = self.build(lambda m: None)
        self.assertEqual(d.render()[0], {})

    def test_set_base_first_then_add_tool(self):
        d = setup_docs()
        t = d.begin("config_files")
        self.assertTrue(t.mise().set_base('[tools]\njq = "1"\n'))
        t.commit()
        t = d.begin("node_tools")
        self.assertFalse(t.mise().add_tool("jq", "latest"))
        t.commit()
        self.assertEqual(d.conflicts, [])

    def test_add_tool_then_set_base(self):
        """The order does not matter: a key that set_base brings and a tool entry has is a conflict."""
        d = setup_docs()
        t = d.begin("node_tools")
        t.mise().add_tool("jq", "latest")
        t.commit()
        t = d.begin("config_files")
        with self.assertRaisesRegex(docs.ConflictError, "node_tools sets jq"):
            t.mise().set_base('[tools]\njq = "1"\n')
        t.rollback()
        self.assertEqual(d.conflicts, [])
        # Not strict: the base text wins, and the entry is not rendered.
        d = setup_docs(strict=False)
        t = d.begin("node_tools")
        t.mise().add_tool("jq", "latest")
        t.mise().add_tool("npm:x", "latest")
        t.commit()
        t = d.begin("config_files")
        self.assertTrue(t.mise().set_base('[tools]\njq = "1"\n'))
        t.commit()
        self.assertEqual([(c.key, c.first, c.second, c.kept, c.refused) for c in d.conflicts],
                         [("jq", "config_files", "node_tools", '"1"', '"latest"')])
        text = d.render()[0]["config/mise.toml"]
        self.assertEqual(tomllib.loads(text)["tools"], {"jq": "1", "npm:x": "latest"})
        self.assertEqual(text.count("jq"), 1)

    def test_settings_come_after_set_base(self):
        d = setup_docs()
        t = d.begin("x")
        t.mise().set_setting("jobs", 4)
        with self.assertRaises(ValueError):
            t.mise().set_base("[settings]\n")
        t.rollback()

    def test_set_setting(self):
        d, out = self.build(lambda m: [m.set_setting("experimental", True), m.set_setting("jobs", 4),
                                       m.set_setting("experimental", False)],
                            base='[tools]\njq = "latest"\n\n[settings]\nexperimental = true\n\n[env]\nA = "1"\n')
        self.assertEqual(out, [False, True, False])
        text = d.render()[0]["config/mise.toml"]
        self.assertIn("[settings]\nexperimental = true\njobs = 4\n\n[env]", text)
        d, out = self.build(lambda m: [m.set_setting("idiomatic_version_file_enable_tools", ["node"]),
                                       m.add_tool("jq", "latest")], base="")
        text = d.render()[0]["config/mise.toml"]
        self.assertEqual(tomllib.loads(text), {"settings": {"idiomatic_version_file_enable_tools": ["node"]},
                                               "tools": {"jq": "latest"}})
        self.assertTrue(text.endswith('\n[settings]\nidiomatic_version_file_enable_tools = ["node"]\n\n'
                                      '[tools]\n"jq" = "latest"\n'))

    def test_set_setting_refusals(self):
        for base in ("settings = { experimental = true }\n", "settings.experimental = true\n", "[tools\n"):
            with self.subTest(base=base):
                d, out = self.build(lambda m: m.set_setting("jobs", 4), base=base)
                self.assertFalse(out)

    KEY = "idiomatic_version_file_enable_tools"

    def test_replace_setting(self):
        base = f'[settings]\n{self.KEY} = ["python"]  # mine\nexperimental = true\n'
        d, out = self.build(lambda m: m.replace_setting(self.KEY, ["node", "python"], expect=["python"]),
                            base=base)
        self.assertTrue(out)
        text = d.render()[0]["config/mise.toml"]
        self.assertIn(f'[settings]\n{self.KEY} = ["node", "python"]  # mine\nexperimental = true\n', text)

    def test_replace_setting_refusals(self):
        new, old = ["node", "python"], ["python"]
        cases = {
            "a dotted key at the top level": f'settings.{self.KEY} = ["python"]\n',
            "an inline table": f'settings = {{ {self.KEY} = ["python"] }}\n',
            "a value on several lines": f'[settings]\n{self.KEY} = [\n  "python",\n]\n',
            "another value": f'[settings]\n{self.KEY} = ["ruby"]\n',
            "no such key": '[settings]\nexperimental = true\n',
            "in a subtable": f'[settings.other]\n{self.KEY} = ["python"]\n',
        }
        for why, base in cases.items():
            with self.subTest(why):
                d, out = self.build(lambda m: m.replace_setting(self.KEY, new, expect=old), base=base)
                self.assertFalse(out)
                self.assertEqual(d.get("config/mise.toml").state.text.split("config.toml.\n", 1)[1], base)
        d, out = self.build(lambda m: m.replace_setting(self.KEY, old, expect=old),
                            base=f'[settings]\n{self.KEY} = ["python"]\n')
        self.assertFalse(out, "the same value changes nothing")

    def test_replace_setting_undo(self):
        """An edit whose result is not the old data with only that key changed is undone."""
        base = f'[settings]\nnote = """\n{self.KEY} = ["python"]\n"""\n{self.KEY} = ["python"]\n'
        d, out = self.build(lambda m: m.replace_setting(self.KEY, ["node"], expect=["python"]), base=base)
        self.assertFalse(out)
        text = d.get("config/mise.toml").state.text
        self.assertTrue(text.endswith(base))
        self.assertEqual(tomllib.loads(text)["settings"]["note"], f'{self.KEY} = ["python"]\n')

    def test_adopt_mode_is_add_tool_only(self):
        repo = fake.Home()
        self.addCleanup(repo.cleanup)
        repo.write("r/config/mise.toml", '[tools]\n"npm:typescript" = "latest"\n')
        d = docs.Docs("adopt", TEMPLATE, repo.home / "r", home=str(repo.home), strict=True)
        t = d.begin("x")
        m = t.mise()
        self.assertFalse(m.add_tool("npm:typescript", "latest"))
        self.assertTrue(m.add_tool("pipx:ruff", "latest"))
        for op in (lambda: m.set_base(""), lambda: m.set_setting("a", 1), lambda: m.replace_setting("a", 1, 2)):
            with self.assertRaises(ValueError):
                op()
        for op in (t.links, lambda: t.shell_rc(".zshrc"), lambda: t.file("x"), t.shell_nix):
            with self.assertRaises(ValueError):
                op()
        t.commit()
        self.assertEqual(d.render()[0]["config/mise.toml"],
                         '[tools]\n"npm:typescript" = "latest"\n"pipx:ruff" = "latest"\n')

    def test_insert_tools(self):
        self.assertEqual(docs.insert_tools("[tools]\n", ['"a" = "1"']), '[tools]\n"a" = "1"\n')
        self.assertEqual(docs.insert_tools('[tools]\nx = "1"\n\n[settings]\ny = 1\n', ['"a" = "1"']),
                         '[tools]\nx = "1"\n"a" = "1"\n\n[settings]\ny = 1\n')
        self.assertEqual(docs.insert_tools("[env]\nA = '1'\n", ['"a" = "1"']),
                         "[env]\nA = '1'\n\n[tools]\n\"a\" = \"1\"\n")


class FlaggedTests(unittest.TestCase):
    """The views say whether the secret check flags the exact line that an entry would be."""

    def test_flagged(self):
        d = setup_docs()
        t = d.begin("x")
        self.assertTrue(t.mise().flagged("pipx:detect-secrets", "latest"))
        self.assertFalse(t.mise().flagged("pipx:ruff", "latest"))
        self.assertTrue(t.list("nix/mas.txt").flagged("123 Token: Keeper"))
        self.assertFalse(t.list("nix/mas.txt").flagged("123 Keynote"))
        self.assertFalse(t.list("nix/brews.txt").flagged("wget"))
        t.rollback()
        # The tool line as MiseDoc writes it is the line that was checked.
        t = d.begin("x")
        t.mise().add_tool("pipx:detect-secrets", "latest")
        t.commit()
        self.assertIn('\n"pipx:detect-secrets" = "latest"\n', d.render()[0]["config/mise.toml"])


class LinksAndFilesTests(unittest.TestCase):
    def test_links(self):
        d = setup_docs(strict=False)
        t = d.begin("x")
        links = t.links()
        self.assertTrue(links.link(".npmrc", None))
        self.assertTrue(links.link(".config/ghostty/config.local", "config/ghostty.conf"))
        self.assertTrue(links.link('.config/"odd"', "config/odd"))
        self.assertFalse(links.link(".zshrc.local", "config/other"), "the template links it already")
        self.assertFalse(links.link(".npmrc", None))
        self.assertFalse(links.link(".npmrc", "config/npmrc"))
        self.assertEqual(len(d.conflicts), 1)
        for target, source in (("/abs", None), ("../x", None), ("", None), ("a", "../b"), ("a", "/b")):
            with self.subTest(target=target, source=source):
                with self.assertRaises(ValueError):
                    links.link(target, source)
        t.commit()
        self.assertEqual(d.links(), {".npmrc": None, ".config/ghostty/config.local": "config/ghostty.conf",
                                     '.config/"odd"': "config/odd"})
        text = d.render()[0]["nix/home.nix"]
        self.assertIn('    ".zshrc.local" = "config/zsh/zshrc";\n'
                      '    ".config/\\"odd\\"" = "config/odd";\n'
                      '    ".config/ghostty/config.local" = "config/ghostty.conf";\n'
                      '    # Yours stays in place, see MIGRATION.md.\n    ".npmrc" = null;\n', text)

    def test_shell_rc(self):
        d = setup_docs()
        t = d.begin("shell")
        rc = t.shell_rc(".zshrc")
        self.assertEqual(rc.path, "config/zsh/zshrc")
        self.assertTrue(rc.add_part("# From ~/.zshrc.", "export EDITOR=vim\n"))
        self.assertFalse(rc.add_part("# From ~/.zshrc.", "export EDITOR=vim\n"))
        with self.assertRaises(ValueError):
            rc.add_part("no comment", "x\n")
        with self.assertRaises(ValueError):
            t.shell_rc(".zlogin")
        t.shell_rc(".zprofile").ensure()
        t.shell_rc(".bash_profile")
        t.commit()
        files, secret_lines = d.render()
        self.assertEqual(secret_lines, "")
        self.assertEqual(files["config/zsh/zshrc"], (TEMPLATE / "config/zsh/zshrc").read_text() +
                         "\n# From ~/.zshrc.\nexport EDITOR=vim\n")
        self.assertTrue(files["config/zsh/zprofile"].startswith("# Your lines for zsh login shells"))
        self.assertNotIn("config/bash/bash_profile", files, "nothing added, not ensured")

    def test_secrets(self):
        """~/.secrets: its variables stay, the same value is no-op, another value is refused and not a Conflict."""
        d = docs.Docs("setup", TEMPLATE, Path(HOME, ".dotfiles"), home=HOME, strict=True,
                      secrets={"OLD": "v1", "SAME": "s"})
        t = d.begin("shell")
        s = t.secrets()
        self.assertTrue(s.export("SAME", "s", "export SAME=s", "~/.zshrc"))
        self.assertFalse(s.export("OLD", "v2", "export OLD=v2", "~/.zshrc"))
        self.assertTrue(s.export("NEW", "n", "export NEW=n", "~/.zshrc"))
        self.assertTrue(s.export("NEW", "n", "export NEW='n'", "~/.zshrc"), "the same value")
        self.assertFalse(s.export("NEW", "m", "export NEW=m", "~/.zshrc"))
        self.assertEqual(s.free_name("OLD", "v1"), "OLD")
        self.assertEqual(s.free_name("OLD", "v2"), "OLD_2")
        self.assertEqual(s.free_name("FRESH", "x"), "FRESH")
        for name, line in (("1X", "export 1X=1"), ("A-B", "x"), ("OK", "two\nlines")):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    s.export(name, "v", line, "~/.zshrc")
        t.commit()
        t = d.begin("config_files")
        t.secrets().export("TOK", "t", "export TOK='t'", "~/.npmrc")
        t.commit()
        files, secret_lines = d.render()
        self.assertEqual(secret_lines, "# Added by the dotkit migration. See MIGRATION.md.\n# From ~/.zshrc.\n"
                                       "export NEW=n\n# From ~/.npmrc.\nexport TOK='t'\n")
        self.assertEqual(d.secret_names(), ["NEW", "TOK"])
        self.assertEqual(d.conflicts, [])
        self.assertEqual(files, {})
        adopt = docs.Docs("adopt", TEMPLATE, Path(HOME, ".dotfiles"), home=HOME)
        t = adopt.begin("shell")
        with self.assertRaises(ValueError):
            t.secrets()
        t.rollback()

    def test_shell_nix_file_and_copies(self):
        d = setup_docs(strict=False)
        t = d.begin("core")
        self.assertTrue(t.shell_nix().set_keymap("vi"))
        with self.assertRaises(ValueError):
            t.shell_nix().set_keymap("emacs-ish")
        self.assertTrue(t.file("nix/user.nix").set("rec { }\n"))
        self.assertFalse(t.file("nix/user.nix").set("rec { }\n"))
        self.assertFalse(t.file("nix/user.nix").set("other\n"))
        self.assertEqual(d.conflicts[-1].path, "nix/user.nix")
        for path in ("config/mise.toml", "config/zsh/zshrc", "nix/brews.txt", "MIGRATION.md", "../x"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    t.file(path)
        self.assertTrue(t.copies.add("config/nvim", Path(HOME, ".config/nvim")))
        self.assertFalse(t.copies.add("config/nvim", Path(HOME, ".config/nvim")))
        n = len(d.conflicts)
        # A path that is taken is not copied, and that is not a conflict.
        self.assertFalse(t.copies.add("config/nvim", Path(HOME, ".nvim")), "an earlier copy")
        self.assertEqual(t.copies.source("config/nvim"), Path(HOME, ".config/nvim"))
        self.assertFalse(t.copies.add("nix/user.nix", Path(HOME, "x")), "a file of a document")
        self.assertFalse(t.copies.add("config/mise.toml", Path(HOME, "x")), "a fixed document path")
        self.assertIsNone(t.copies.source("config/mise.toml"))
        self.assertEqual(len(d.conflicts), n)
        # A whole file where a copy is: two documents claim the path.
        self.assertFalse(t.file("config/nvim").set("x\n"))
        self.assertEqual(d.conflicts[-1].path, "config/nvim")
        for rel, src in (("config/x", Path("/etc/x")), ("config/x", Path("rel")), ("../x", Path(HOME, "x")),
                         ("config/x", Path(HOME + "-other/x"))):
            with self.subTest(rel=rel, src=src):
                with self.assertRaises(ValueError):
                    t.copies.add(rel, src)
        t.commit()
        files, _ = d.render()
        self.assertIn('    keymap = "vi";', files["nix/shell.nix"])
        self.assertEqual(files["nix/user.nix"], "rec { }\n")
        self.assertEqual(d.copies.items(), {"config/nvim": Path(HOME, ".config/nvim")})
        d2 = setup_docs()
        t = d2.begin("core")
        t.shell_nix().set_keymap("emacs")
        t.commit()
        self.assertEqual(d2.render()[0], {})

    def test_overlapping_copies(self):
        """A path inside a copied directory, or a directory that would contain a taken path, is taken."""
        d = setup_docs()
        t = d.begin("other_config")
        src = Path(HOME, ".config/x")
        self.assertTrue(t.copies.add("config/nvim", src))
        self.assertTrue(t.file("config/merged/gitconfig").set("[core]\n"))
        cases = {
            "config/nvim/lua": ("inside", "config/nvim"),
            "config": ("contains", "config/bash/bash_profile"),
            "config/zsh": ("contains", "config/zsh/zprofile"),
            "config/bash": ("contains", "config/bash/bash_profile"),
            "config/merged": ("contains", "config/merged/gitconfig"),
            "nix": ("contains", "nix/brews.txt"),
            ".": ("contains", ".gitignore"),
        }
        for rel, why in cases.items():
            with self.subTest(rel=rel):
                self.assertEqual(t.copies.taken(rel), why)
                self.assertFalse(t.copies.add(rel, src))
        self.assertEqual(d.conflicts, [], "a taken path is not a conflict")
        for rel in ("config/nvim2", "config/bash2", "config/zsh2", "configs"):
            with self.subTest(rel=rel):
                self.assertIsNone(t.copies.taken(rel), "path parts, not string prefixes")
                self.assertTrue(t.copies.add(rel, src))
        self.assertEqual(t.copies.taken("config/zsh2/x"), ("inside", "config/zsh2"))
        self.assertEqual(t.copies.taken("config/zsh2"), ("is", "config/zsh2"))
        self.assertEqual(t.copies.taken("config/zshx"), None)
        # A whole file inside a copied directory is a conflict, which strict mode raises.
        with self.assertRaises(docs.ConflictError):
            t.file("config/nvim/init.lua").set("x\n")
        t.commit()
        self.assertEqual(sorted(d.copies.items()), ["config/bash2", "config/nvim", "config/nvim2", "config/zsh2",
                                                    "configs"])
        lax = setup_docs(strict=False)
        t = lax.begin("other_config")
        t.copies.add("config/nvim", src)
        self.assertFalse(t.file("config/nvim/init.lua").set("x\n"))
        self.assertEqual([(c.path, c.first, c.second) for c in lax.conflicts],
                         [("config/nvim/init.lua", "other_config", "other_config")])
        self.assertEqual(lax.conflicts[0].kept, f"a copy of {src} at config/nvim")
        t.commit()
        self.assertNotIn("config/nvim/init.lua", lax.render()[0])

    def test_overlap_by_earlier_copies_only(self):
        d = setup_docs()
        t = d.begin("other_config")
        self.assertTrue(t.copies.add("config/foo/bar", Path(HOME, ".foo")))
        self.assertEqual(t.copies.taken("config/foo"), ("contains", "config/foo/bar"))
        self.assertFalse(t.copies.add("config/foo", Path(HOME, ".config/foo")))
        self.assertEqual(d.conflicts, [])

    def test_paths_compare_like_apfs(self):
        """Without case and without Unicode normalization differences; messages keep the spelling."""
        d = setup_docs()
        t = d.begin("other_config")
        nfc, nfd = "config/caf\u00e9", "config/cafe\u0301"
        self.assertTrue(t.copies.add(nfc, Path(HOME, ".config/x")))
        for rel in (nfd, "config/CAF\u00c9", "Config/Caf\u00e9"):
            with self.subTest(rel=rel):
                self.assertEqual(t.copies.taken(rel), ("is", rel))
                self.assertEqual(t.copies.source(rel), Path(HOME, ".config/x"))
                self.assertFalse(t.copies.add(rel, Path(HOME, ".config/y")))
        self.assertEqual(t.copies.taken(nfd + "/init.lua"), ("inside", nfc))
        self.assertEqual(t.copies.taken("Config/Zsh"), ("contains", "config/zsh/zprofile"))
        self.assertIsNone(t.copies.taken("Local/x"), "local/ is a path like any other")
        self.assertEqual(t.copies.taken("Config/Mise.toml"), ("is", "Config/Mise.toml"))
        with self.assertRaises(docs.ConflictError):
            t.file("CONFIG/Caf\u00e9").set("x\n")

    def test_template_files_and_user_nix_are_taken(self):
        d = setup_docs()
        self.assertIn("README.md", d.template_files)
        self.assertIn("nix/flake.nix", d.template_files)
        t = d.begin("other_config")
        for rel in ("README.md", "readme.md", "dotkit", "nix/flake.nix", "nix/user.nix", "nix/darwin.nix"):
            with self.subTest(rel=rel):
                self.assertEqual(t.copies.taken(rel), ("is", rel))
                self.assertFalse(t.copies.add(rel, Path(HOME, "x")))
        self.assertEqual(d.conflicts, [])
        # nix/user.nix is still a whole file that core sets.
        self.assertTrue(t.file("nix/user.nix").set("rec { }\n"))

    def test_a_whole_file_that_overlaps_a_copy(self):
        """A whole file at, inside, or around a copy is a conflict, which strict mode raises."""
        for rel in ("config/tool", "config/tool/x", "config"):
            with self.subTest(rel=rel):
                d = setup_docs()
                t = d.begin("config_files")
                t.copies.add("config/tool", Path(HOME, ".config/tool"))
                with self.assertRaises(docs.ConflictError):
                    t.file(rel).set("x\n")
                lax = setup_docs(strict=False)
                t = lax.begin("config_files")
                t.copies.add("config/tool", Path(HOME, ".config/tool"))
                self.assertFalse(t.file(rel).set("x\n"))
                self.assertEqual([(c.path, c.key, c.first) for c in lax.conflicts], [(rel, "(path)", "config_files")])
                t.commit()
                self.assertNotIn(rel, lax.render()[0])
        lax = setup_docs(strict=False)
        t = lax.begin("config_files")
        t.copies.add("config/tool", Path(HOME, ".config/tool"))
        self.assertTrue(t.file("config/tool2").set("x\n"), "path parts, not string prefixes")

    def test_drop(self):
        """The secret guard drops a copy, its links, and a whole file at its path."""
        d = setup_docs()
        t = d.begin("x")
        t.copies.add("config/tool", Path(HOME, ".config/tool"))
        t.links().link(".config/tool", "config/tool")
        t.links().link(".npmrc", None)
        t.file("config/gitconfig").set("[core]\n")
        t.links().link(".gitconfig", "config/gitconfig")
        t.commit()
        d.drop("config/tool")
        self.assertEqual(d.copies.items(), {})
        self.assertEqual(d.links(), {".npmrc": None, ".gitconfig": "config/gitconfig"})
        d.drop("config/gitconfig")
        self.assertEqual(d.links(), {".npmrc": None})
        files, _ = d.render()
        self.assertNotIn("config/gitconfig", files)
        self.assertNotIn('".gitconfig"', files["nix/home.nix"])


class ReportDocTests(unittest.TestCase):
    def test_blocks_deduplicate(self):
        d = setup_docs()
        t = d.begin("x")
        r = t.report
        r.todo("Homebrew taps", "First intro.", ["`x`"])
        r.todo("Homebrew taps", "Second intro.", ["`x`"])
        r.todo("Homebrew taps", "First intro.", ["`x`", "`x`"])
        t.commit()
        text = report.render(d.report, "mac", "~/.dotfiles")
        self.assertIn("First intro.\n\n- `x`\n\nSecond intro.\n\n- `x`\n", text)
        self.assertEqual(text.count("- `x`"), 2)
        self.assertEqual(d.report.count, 2)
        t = d.begin("y")
        t.report.todo("Logins", "Nothing to list.", [])
        # Repeats in one call collapse too.
        t.report.todo("Config files", "One intro.", ["`y`", "`y`"])
        t.report.todo("Shell lines: Done by dotkit", "", ["`z`"])
        t.commit()
        self.assertEqual(d.report.count, 4, "the lines that dotkit does already do not count")
        self.assertEqual(d.report.sections["Config files"].items, ["`y`"])

    def test_sections_are_checked(self):
        d = setup_docs()
        t = d.begin("x")
        for title in ("Nonsense", "Shell lines: Nonsense"):
            with self.subTest(title=title):
                with self.assertRaises(ValueError):
                    t.report.todo(title, "", ["x"])
        t.rollback()

    def test_carried_and_blockers(self):
        d = setup_docs()
        t = d.begin("x")
        t.report.carried("2 Homebrew formulae", "nix/brews.txt")
        t.report.blocker("~/.config is a link.")
        t.report.blocker("~/.config is a link.")
        t.commit()
        self.assertEqual(d.report.carried_rows, [("2 Homebrew formulae", "nix/brews.txt")])
        self.assertEqual(d.report.blockers, ["~/.config is a link."])
        self.assertIn("| 2 Homebrew formulae | nix/brews.txt |", report.render(d.report, "mac", "~/x"))


class TxnTests(unittest.TestCase):
    def work(self, t: docs.Txn) -> None:
        t.list("nix/brews.txt").add("wget")
        t.mise().add_tool("npm:x", "latest")
        t.links().link(".npmrc", None)
        t.shell_rc(".zshrc").add_part("# From ~/.zshrc.", "export A=1\n")
        t.file("config/gitconfig").set("[core]\n")
        t.copies.add("config/nvim", Path(HOME, ".config/nvim"))
        t.report.carried("1 Homebrew formula", "nix/brews.txt")
        t.report.todo("Config files", "", ["x"])

    def test_nothing_before_commit(self):
        d = setup_docs()
        t = d.begin("a")
        self.work(t)
        self.assertEqual(t.list("nix/brews.txt").added(), ["wget"], "the view sees its pending entries")
        self.assertEqual(d.get("nix/brews.txt").added(), [])
        self.assertEqual(d.copies.items(), {})
        self.assertEqual(d.report.carried_rows, [])
        self.assertEqual(d.report.sections, {})
        self.assertEqual(d.links(), {})
        t.commit()
        files, _ = d.render()
        self.assertEqual(sorted(files), ["config/gitconfig", "config/mise.toml", "config/zsh/zshrc",
                                         "nix/brews.txt", "nix/home.nix"])
        self.assertEqual(len(d.report.carried_rows), 1)
        self.assertEqual(list(d.copies.items()), ["config/nvim"])

    def test_rollback(self):
        d = setup_docs(strict=False)
        t = d.begin("a")
        t.list("nix/brews.txt").add("jq")
        t.list("nix/taps.txt").add("x/y")
        t.list("nix/taps.txt").add("x/y https://example.com/y.git")
        t.commit()
        self.assertEqual(len(d.conflicts), 1)
        before = list(d.conflicts)
        t = d.begin("b")
        self.work(t)
        t.list("nix/taps.txt").add("x/y https://example.com/z.git")
        self.assertEqual(len(d.conflicts), 2)
        t.rollback()
        self.assertEqual(d.conflicts, before, "a rollback leaves the conflicts as they were")
        files, local = d.render()
        self.assertEqual(sorted(files), ["nix/brews.txt", "nix/taps.txt"])
        self.assertNotIn("wget", files["nix/brews.txt"])
        self.assertEqual((d.copies.items(), d.links(), d.report.carried_rows, d.report.sections),
                         ({}, {}, [], {}))
        # After a rollback, the next owner sees only what was committed.
        t = d.begin("c")
        self.assertTrue(t.list("nix/brews.txt").add("wget"))
        self.assertFalse(t.list("nix/brews.txt").add("jq"))
        t.commit()

    def test_one_transaction_at_a_time(self):
        d = setup_docs()
        t = d.begin("a")
        with self.assertRaises(AssertionError):
            d.begin("b")
        with self.assertRaises(AssertionError):
            d.render()
        t.commit()
        with self.assertRaises(AssertionError):
            t.list("nix/brews.txt").add("wget")
        with self.assertRaises(AssertionError):
            t.commit()
        d.begin("b").rollback()

    def test_the_keymap_is_the_core(self):
        d = setup_docs()
        t = d.begin("shell")
        with self.assertRaises(AssertionError):
            t.shell_nix()
        t.rollback()

    def test_docs_need_the_repository(self):
        for mode in ("setup", "adopt"):
            with self.assertRaises(AssertionError):
                docs.Docs(mode, TEMPLATE, None, home=HOME, strict=True)


if __name__ == "__main__":
    unittest.main()
