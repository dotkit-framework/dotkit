"""The appstore migrator: the apps that mas lists, or the App Store receipts without mas."""

import unittest

from dotkit_migrate.migrators import appstore
from tests import fake


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


if __name__ == "__main__":
    unittest.main()
