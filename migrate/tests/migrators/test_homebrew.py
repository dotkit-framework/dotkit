"""The homebrew migrator: formulae, casks, and taps."""

import unittest

from dotkit_migrate import secrets
from dotkit_migrate.migrators import homebrew
from dotkit_migrate.model import AdoptOptions
from tests import fake


class HomebrewTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.repo = str(self.h.home / ".dotfiles")

    def scan(self, runner):
        facts = fake.session(self.h, runner).scan()
        self.assertEqual(facts.warnings, [])
        return homebrew.facts_of(facts)

    def test_homebrew(self):
        r = fake.brew_runner(
            formulae=["wget", "ripgrep", "hashicorp/tap/terraform"],
            casks=["ghostty", "raycast", "homebrew/cask-fonts/font-fira-code"],
            taps=[
                {"name": "homebrew/core", "remote": "https://github.com/Homebrew/homebrew-core"},
                {"name": "homebrew/cask-fonts", "remote": "https://github.com/Homebrew/homebrew-cask-fonts"},
                {"name": "hashicorp/tap", "remote": "https://github.com/hashicorp/homebrew-tap"},
                {"name": "acme/tools", "remote": "git@gitlab.com:acme/homebrew-tools.git"},
            ],
        )
        s = self.scan(r)
        self.assertEqual(s.formulae, ["wget", "ripgrep", "hashicorp/tap/terraform"])
        self.assertEqual(s.casks, ["ghostty", "raycast", "homebrew/cask-fonts/font-fira-code"])
        self.assertEqual([(t.name, t.custom) for t in s.taps], [("hashicorp/tap", False), ("acme/tools", True)])
        self.assertEqual(s.old_taps, ["homebrew/cask-fonts"])

    def test_no_homebrew(self):
        s = self.scan(fake.FakeRunner())
        self.assertEqual((s.brew, s.formulae, s.casks, s.taps), ("", [], [], []))

    def test_old_taps_keep_their_intro(self):
        runner = fake.brew_runner(taps=[
            {"name": "acme/tools", "remote": "git@gitlab.com:acme/homebrew-tools.git"},
            {"name": "homebrew/cask-fonts"},
        ])
        p = fake.session(self.h, runner)
        r = p.build(p.defaults(self.repo)).report
        custom = r.index("These taps are not on GitHub, so the list has their URL. Check it.")
        merged = r.index("Homebrew merged these taps into its own. Remove them.")
        self.assertLess(custom, r.index("`acme/tools`: git@gitlab.com"))
        self.assertLess(r.index("`acme/tools`: git@gitlab.com"), merged)
        self.assertLess(merged, r.index("`homebrew/cask-fonts`: `brew untap homebrew/cask-fonts`"))
        self.assertEqual(r.count("### Homebrew taps"), 1)

    def test_a_tap_url_with_a_secret_is_not_shown(self):
        url = fake.fill("https://me:@GH@@git.example.com/homebrew tap.git")
        runner = fake.brew_runner(taps=[{"name": "corp/tap", "remote": url}])
        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = fake.session(h, runner)
        p.scan()
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertNotIn("nix/taps.txt", result.files)
        self.assertIn("- `corp/tap`: its URL has a secret (", result.report)
        self.assertIn("), so it is not in nix/taps.txt. Take the credential out of the URL (for example with an "
                      "SSH URL or a git credential helper), then add the tap to nix/taps.txt. Do not put the URL "
                      "with the credential into a file that Git tracks.\n", result.report)
        adopted = p.adopt(h.home / ".dotfiles", {}, AdoptOptions())
        self.assertEqual(adopted.files, {})
        out = "\n".join(adopted.output)
        self.assertIn("Homebrew taps, not added:\n  `corp/tap`: its URL has a secret", out)
        for text in (result.report, "\n".join(result.summary), out):
            self.assertNotIn(fake.TOKENS["@GH@"], text)
            self.assertNotIn("git.example.com", text)

    def test_a_tap_name_with_a_space_is_not_written(self):
        """The name rule comes first: `a/b c` is not the tap a/b with the URL c."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        runner = fake.brew_runner(taps=[{"name": "a/b c"},
                                        {"name": "ok/tap", "remote": "https://github.com/ok/homebrew-tap"}])
        p = fake.session(h, runner)
        s = p.scan()
        self.assertFalse(homebrew.facts_of(s).taps[0].custom)
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertTrue(result.files["nix/taps.txt"].endswith(", see MIGRATION.md.\nok/tap\n"))
        self.assertIn("- `a/b c`: it cannot go into nix/taps.txt: its name is not owner/repo of letters, digits, "
                      "`_`, `.`, and `-`.\n", result.report)
        adopted = p.adopt(h.home / ".dotfiles", {}, AdoptOptions())
        self.assertRegex(adopted.files["nix/taps.txt"], r"\A# Added by dotkit migrate on .*\.\nok/tap\n\Z")

    def test_taps_that_cannot_go_into_the_list(self):
        runner = fake.brew_runner(taps=[
            {"name": "ok/tap", "remote": "https://github.com/ok/homebrew-tap"},
            {"name": "acme/space", "remote": "https://example.com/a b.git"},
            {"name": "acme/hash", "remote": "https://example.com/a.git#main"},
        ])
        p = fake.session(self.h, runner)
        result = p.build(p.defaults(self.repo))
        self.assertTrue(result.files["nix/taps.txt"].endswith(", see MIGRATION.md.\nok/tap\n"))
        self.assertNotIn("acme/", result.files["nix/taps.txt"])
        for name, url, why in (("acme/space", "https://example.com/a b.git", "it has a space"),
                               ("acme/hash", "https://example.com/a.git#main", "it has a `#`")):
            self.assertIn(f"- `{name}`: its URL cannot go into nix/taps.txt ({why}). Add it by hand to "
                          f"`homebrew.taps` in nix/darwin.nix as "
                          f'`{{ name = "{name}"; clone_target = "{url}"; }}`.\n', result.report)
        self.assertIn("1 Homebrew tap -> nix/taps.txt", result.summary)


    def test_a_tap_whose_line_is_flagged(self):
        """`corp/token <URL>` looks like a secret: every text names the tap only, with `brew tap-info`."""
        runner = fake.brew_runner(taps=[
            {"name": "corp/token", "remote": "https://git.example.com/corp/homebrew-tap.git"},
            {"name": "ok/tap", "remote": "https://github.com/ok/homebrew-tap"}])
        p = fake.session(self.h, runner)
        self.assertIsNotNone(secrets.line_secret("corp/token https://git.example.com/corp/homebrew-tap.git"))
        todo = ("`corp/token`: dotkit's secret check flags its line for nix/taps.txt, so it is not added. Add it by "
                "hand; `brew tap-info corp/token` shows its URL.")
        left = "`corp/token` (tap): add it to nix/taps.txt by hand; `brew tap-info corp/token` shows its URL."
        for item in (todo, left):
            self.assertIsNone(secrets.line_secret(f"- {item}"))
        selected = p.build(p.defaults(self.repo))
        self.assertIn(f"- {todo}\n", selected.report)
        self.assertTrue(selected.files["nix/taps.txt"].endswith(", see MIGRATION.md.\nok/tap\n"))
        c = p.defaults(self.repo)
        c.picked["taps"].discard("tap:corp/token")
        deselected = p.build(c)
        self.assertIn(f"- {left}\n", deselected.report)
        for plan in (selected, deselected):
            self.assertNotIn("(a line with a secret was here)", plan.report)
            self.assertNotIn("git.example.com", plan.report)
        for _ in range(2):
            adopted = p.adopt(self.h.home / ".dotfiles", {}, AdoptOptions())
            self.assertEqual(adopted.output[-2:], ["Homebrew taps, not added:", f"  {todo}"])


if __name__ == "__main__":
    unittest.main()
