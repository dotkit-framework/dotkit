"""The appstore migrator: the apps that mas lists, or the App Store receipts without mas."""

import unittest

from dotkit_migrate import secrets
from dotkit_migrate.migrators import appstore
from dotkit_migrate.model import AdoptOptions
from tests import fake

MASKED = "(a line with a secret was here)"


class AppStoreTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def scan(self, runner=None):
        facts = fake.session(self.h, runner or fake.brew_runner()).scan()
        self.assertEqual(facts.warnings, [])
        return appstore.facts_of(facts)

    def test_app_store(self):
        s = self.scan(fake.brew_runner(mas="497799835  Xcode       (16.0)\n  123   (1.0)\n409183694 Keynote (14.2)\n"))
        self.assertTrue(s.mas)
        self.assertEqual(s.apps, [(497799835, "Xcode"), (409183694, "Keynote")])
        receipt = self.h.apps / "Pages.app/Contents/_MASReceipt/receipt"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("")
        (self.h.apps / "Other.app/Contents").mkdir(parents=True)
        s = self.scan()
        self.assertFalse(s.mas)
        self.assertEqual(s.store_apps, ["Pages"])

    def test_two_apps_with_one_name(self):
        """The second app gets a label."""
        p = fake.session(self.h, fake.brew_runner(mas="409183694 Keynote (14.2)\n1234 Keynote (1.0)\n"
                                                      "497799835 Xcode (16.0)\n"))
        result = p.build(p.defaults(str(self.h.home / ".dotfiles")))
        self.assertTrue(result.files["nix/mas.txt"].endswith(
            ", see MIGRATION.md.\n409183694 Keynote\n1234 Keynote (1234)\n497799835 Xcode\n"))
        self.assertIn("- `Keynote` (ID 1234): another app has this name, so nix/mas.txt lists it as "
                      "`Keynote (1234)`.\n", result.report)
        self.assertIn("3 App Store apps -> nix/mas.txt", result.summary)


    def test_an_app_whose_line_is_flagged(self):
        """`123 Token: Keeper` looks like a secret: every text names it by its ID, and nothing is masked."""
        runner = fake.brew_runner(mas="123 Token: Keeper (1.0)\n497799835 Xcode (16.0)\n")
        p = fake.session(self.h, runner)
        repo = str(self.h.home / ".dotfiles")
        todo = ("The App Store app with ID 123: dotkit's secret check flags its line in nix/mas.txt, so it is not "
                "added. Add it by hand: `dotkit add mas:123`.")
        left = "The App Store app with ID 123: `dotkit add mas:123`"
        for item in (todo, left):
            self.assertIsNone(secrets.line_secret(f"- {item}"))
        selected = p.build(p.defaults(repo))
        self.assertIn(f"- {todo}\n", selected.report)
        self.assertNotIn("123 Token", selected.files["nix/mas.txt"])
        c = p.defaults(repo)
        c.picked["mas"].discard("mas:123")
        deselected = p.build(c)
        self.assertIn(f"- {left}\n", deselected.report)
        for plan in (selected, deselected):
            self.assertNotIn(MASKED, plan.report)
            self.assertNotIn("Token: Keeper", plan.report)
        # Adopt: the to-do comes back on each run, until the app is added by hand.
        for _ in range(2):
            adopted = p.adopt(self.h.home / ".dotfiles", {}, AdoptOptions())
            self.assertEqual(adopted.output[-2:], ["App Store apps, not added:", f"  {todo}"])


if __name__ == "__main__":
    unittest.main()
