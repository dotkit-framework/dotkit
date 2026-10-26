"""The plan of dotkit tidy: the default verbs, the text, and the check of an edited plan (TIDY.md, sections 4, 5)."""

import os
import unittest

from dotkit_migrate.tidy import apps, plan
from dotkit_migrate.tidy.cli import declared_of
from tests.tidy.mac import TidyMac


class DefaultTests(unittest.TestCase):
    """One test per row of the table in TIDY.md, section 4.2."""

    def setUp(self):
        self.m = TidyMac(self)
        self.repo = self.m.h.home / ".dotfiles"
        (self.repo / "nix").mkdir(parents=True)

    def lines(self, casks="", mas=""):
        (self.repo / "nix/casks.txt").write_text(casks)
        (self.repo / "nix/mas.txt").write_text(mas)
        ctx = self.m.context()
        t = apps.scan(ctx)
        lines, listed = plan.defaults(t, declared_of(ctx, self.repo, {}))
        return {os.path.basename(x.path): (x.verb, x.thing, x.note) for x in lines}, listed, t

    def test_an_installed_cask(self):
        m = self.m
        m.app("Slack.app", "com.tinyspeck.slackmacgap")
        m.app("Zed.app", "dev.zed.Zed")
        m.cask("slack", apps=["Slack.app"], installed="4.52.182")
        m.cask("zed", apps=["Zed.app"], installed="1.25.3")
        got, listed, _ = self.lines(casks="slack\n")
        self.assertEqual(got, {"Zed.app": ("add", "cask:zed", "from a cask, not in nix/casks.txt")})
        self.assertEqual(listed, 1)

    def test_app_store_apps(self):
        m = self.m
        m.paths["mas"] = "/opt/homebrew/bin/mas"
        m.commands["mas list"] = "409201541  Pages  (14.5)\n409203825  Numbers  (14.5)\n"
        m.app("Pages.app", "com.apple.iWork.Pages", mas=409201541)
        m.app("Numbers.app", "com.apple.iWork.Numbers", mas=409203825)
        m.app("Pixelmator Pro.app", "com.pixelmatorteam.pixelmator.x", mas=1289583905)
        m.cask("pixelmator-pro", apps=["Pixelmator Pro.app"], quit=["com.pixelmatorteam.pixelmator.x"])
        m.app("Odd.app", "com.example.odd", mas=1)
        m.commands.pop(f"mdls -raw -name kMDItemAppStoreAdamID {m.h.apps / 'Odd.app'}")
        got, listed, _ = self.lines(mas="409201541 Pages\n")
        self.assertEqual(listed, 1)
        self.assertEqual(got["Numbers.app"], ("add", "mas:409203825", "from the App Store, not in nix/mas.txt"))
        self.assertEqual(got["Pixelmator Pro.app"][:2], ("add", "mas:1289583905"))
        self.assertIn("cask pixelmator-pro has it too, but the App Store data and license do not carry over",
                      got["Pixelmator Pro.app"][2])
        self.assertEqual(got["Odd.app"][:2], ("keep", ""))

    def test_app_store_apps_without_mas(self):
        m = self.m
        m.app("Pages.app", "com.apple.iWork.Pages", mas=409201541)
        got, _, _ = self.lines()
        self.assertEqual(got["Pages.app"], ("keep", "", "from the App Store; run `dotkit add brew:mas`, then dotkit "
                                                        "tidy again"))

    def test_apps_that_another_tool_manages(self):
        m = self.m
        m.app("CleanShot X.app", "pl.maketheweb.cleanshotx", where=m.h.apps / "Setapp")
        m.app("Xcode.app", "com.apple.dt.Xcode")
        got, _, _ = self.lines()
        self.assertEqual(got, {"CleanShot X.app": ("keep", "", "from Setapp, which manages it"),
                               "Xcode.app": ("keep", "", "from Apple")})

    def test_an_app_that_is_not_yours(self):
        m = self.m
        os.chmod(m.app("Tailscale.app", "io.tailscale.ipn.macsys"), 0o555)
        m.cask("tailscale-app", quit=["io.tailscale.ipn.macsys"], pkg=True)
        verb, thing, note = self.lines()[0]["Tailscale.app"]
        self.assertEqual((verb, thing), ("keep", ""))
        self.assertTrue(note.endswith("so tidy does not change it; or: replace cask:tailscale-app"), note)

    def test_no_cask_another_edition_and_several_casks(self):
        m = self.m
        m.app("Home VPN.app", plist=False)
        m.app("1Password.app", "com.agilebits.onepassword7")
        m.cask("1password", apps=["1Password.app"], quit=["com.1password.1password"])
        m.app("Bar.app", "com.example.bar")
        m.cask("bar-one", apps=["Bar.app"])
        m.cask("bar-two", apps=["Bar.app"])
        got, _, _ = self.lines()
        self.assertEqual(got["Home VPN.app"], ("keep", "", "by hand; no cask"))
        self.assertEqual(got["1Password.app"], ("keep", "", "by hand; 1password has another bundle ID than "
                                                            "com.agilebits.onepassword7, so the app data does not "
                                                            "follow"))
        self.assertEqual(got["Bar.app"], ("keep", "", "by hand; several casks match: bar-one, bar-two"))

    def test_versions(self):
        m = self.m
        for name, version, cask_version, auto in (("Auto", "1.0", "9.0", True), ("Same", "2.1", "2.1,430", False),
                                                  ("Older", "3.6.14", "3.7.21", False),
                                                  ("Newer", "2.0", "1.9", False), ("Odd", "Build", "4215", False)):
            m.app(f"{name}.app", f"com.example.{name.lower()}", version)
            m.cask(name.lower(), apps=[f"{name}.app"], quit=[f"com.example.{name.lower()}"], version=cask_version,
                   auto_updates=auto)
        got, _, _ = self.lines()
        self.assertEqual(got["Auto.app"], ("adopt", "cask:auto", "by hand; the cask auto-updates"))
        self.assertEqual(got["Same.app"], ("adopt", "cask:same", "by hand; 2.1, the same as the cask"))
        self.assertEqual(got["Older.app"], ("replace", "cask:older", "by hand; 3.6.14, the cask has 3.7.21"))
        self.assertEqual(got["Newer.app"], ("keep", "", "by hand; 2.0, the cask newer has 1.9, so replace would "
                                                        "make it older"))
        self.assertEqual(got["Odd.app"][:2], ("keep", ""))
        self.assertIn("dotkit cannot compare it with 4215", got["Odd.app"][2])

    def test_a_pkg_cask_and_a_running_app(self):
        m = self.m
        m.app("zoom.us.app", "us.zoom.xos", "6.0")
        m.cask("zoom", delete=["/Applications/zoom.us.app"], quit=["us.zoom.xos"], pkg=True)
        m.app("Joplin.app", "net.cozic.joplin-desktop", "3.6.14")
        m.cask("joplin", apps=["Joplin.app"], quit=["net.cozic.joplin-desktop"], version="3.7.21")
        m.procs = [f"{m.h.apps}/Joplin.app/Contents/MacOS/Joplin"]
        got, _, _ = self.lines()
        self.assertEqual(got["zoom.us.app"], ("replace", "cask:zoom", "by hand; the cask zoom runs the vendor "
                                                                      "installer, which can ask for your password"))
        self.assertEqual(got["Joplin.app"][2], "by hand; 3.6.14, the cask has 3.7.21; it runs, and tidy quits it first")

    def test_a_cask_that_names_other_bundle_ids(self):
        """Raspberry Pi Imager: org.raspberrypi in the app, com.raspberrypi in the cask. replace becomes keep."""
        m = self.m
        m.app("Raspberry Pi Imager.app", "org.raspberrypi.imagingutility", "1.9.6")
        m.cask("raspberry-pi-imager", apps=["Raspberry Pi Imager.app"], version="2.0.12",
               zap=["~/Library/Saved Application State/com.raspberrypi.imagingutility.savedState"])
        m.app("MonitorControl.app", "app.monitorcontrol.MonitorControl", "4.4.0")
        m.cask("monitorcontrol", apps=["MonitorControl.app"], auto_updates=True,
               zap=["~/Library/Preferences/me.guillaumeb.MonitorControl.plist"])
        got, _, _ = self.lines()
        self.assertEqual(got["Raspberry Pi Imager.app"][:2], ("keep", ""))
        self.assertTrue(got["Raspberry Pi Imager.app"][2].endswith(
            "the cask names com.raspberrypi.imagingutility, not org.raspberrypi.imagingutility, so check that the "
            "app data follows; or: replace cask:raspberry-pi-imager"))
        self.assertEqual(got["MonitorControl.app"][:2], ("adopt", "cask:monitorcontrol"))

    def test_the_order_of_the_lines(self):
        m = self.m
        m.app("b.app", "com.example.b", "1")
        m.cask("b", apps=["b.app"], version="2")
        m.app("A.app", "com.example.a")
        m.cask("a", apps=["A.app"], auto_updates=True)
        m.app("C.app", "com.example.c")
        _, _, t = self.lines()
        lines, _ = plan.defaults(t, declared_of(m.context(), self.repo, {}))
        self.assertEqual([(x.verb, os.path.basename(x.path)) for x in lines],
                         [("adopt", "A.app"), ("replace", "b.app"), ("keep", "C.app")])


class TextTests(unittest.TestCase):
    def setUp(self):
        self.m = TidyMac(self)
        m = self.m
        m.app("Slack.app", "com.tinyspeck.slackmacgap")
        m.cask("slack", apps=["Slack.app"], quit=["com.tinyspeck.slackmacgap"], auto_updates=True)
        m.app("Old Thing.app", "com.example.old", where=m.h.home / "Applications")
        m.app("Zed.app", "dev.zed.Zed")
        m.cask("zed", apps=["Zed.app"], installed="1.0")
        m.app("Joplin.app", "net.cozic.joplin-desktop", "3.6.14")
        m.cask("joplin", apps=["Joplin.app"], quit=["net.cozic.joplin-desktop"], version="3.7.21")
        m.cask("zoom", delete=["/Applications/zoom.us.app"], pkg=True)
        os.chmod(m.app("Locked.app", "com.example.locked"), 0o555)
        for n in range(1, 4):
            m.app(f"Extra{n}.app", f"com.example.extra{n}")
        self.repo = m.h.home / ".dotfiles"
        self.t = m.scan()
        self.lines, self.listed = plan.defaults(self.t, declared_of(m.context(), self.repo, {}))

    def test_render_and_parse_again(self):
        text = plan.render(self.lines, self.t, self.listed)
        self.assertIn("# dotkit tidy: 8 apps.", text)
        self.assertIn("\nadopt    cask:slack   " + str(self.m.h.apps / "Slack.app"), text)
        self.assertIn("\nkeep                  ~/Applications/Old Thing.app", text)
        parsed, errors = plan.parse(text, self.t)
        self.assertEqual(errors, [])
        self.assertEqual([(x.verb, x.thing, x.path) for x in parsed], [(x.verb, x.thing, x.path) for x in self.lines])
        self.assertEqual(plan.check(parsed, self.t), ([], []))

    def check(self, text):
        """As cli.py does: brew info of the casks that you chose, then the check."""
        parsed, errors = plan.parse(text, self.t)
        self.t.casks.update(apps.cask_infos(self.m.context(), self.t, [x.token for x in parsed
                                                                       if x.token and x.token not in self.t.casks]))
        errs, notes = plan.check(parsed, self.t)
        return errors + errs, notes

    def test_errors(self):
        where = self.m.h.apps
        text = "\n".join([
            "# a comment",
            "",
            "fix cask:slack /Applications/Slack.app",
            "adopt cask:slack",
            f"keep {where}/Nothing.app",
            f"adopt {where}/Slack.app",
            f"adopt cask:no-such {where}/Extra1.app",
            f"adopt cask:zoom {where}/Extra2.app",
            f"trash {where}/Zed.app",
            f"trash {where}/Locked.app",
            f"add cask:joplin {where}/Joplin.app",
            f"add mas:1 {where}/Extra3.app",
            f"keep {where}/Zed.app",
            f"adopt cask:zed {where}/Zed.app  # the comment is ignored",
            "keep ~/Applications/Old Thing.app",
            "keep ~/Applications/Old Thing.app",
        ])
        errors, _ = self.check(text)
        self.assertEqual(errors, [
            "line 3: `fix` is not a verb. Use adopt, replace, add, uninstall, keep, trash.",
            "line 4: the line has no path.",
            "line 5: " + str(where / "Nothing.app") + " is not an app or a tool of the scan.",
            "line 6: adopt needs a cask: adopt cask:<token> <path>.",
            "line 7: Homebrew has no cask `no-such`.",
            "line 8: the cask zoom runs an installer, so it has nothing to adopt. Use replace.",
            "line 9: the cask zed has the app. Use `dotkit remove cask:zed` to uninstall it.",
            "line 10: Locked.app is owned by " + self.m.found(self.t, "Locked.app").owner +
            ", so tidy cannot move it to the Trash.",
            "line 11: add is for an app from a cask or the App Store. Use adopt or replace.",
            "line 12: Extra3.app is not the App Store app 1.",
            "line 13: " + str(where / "Zed.app") + " has another line already.",
            "line 14: " + str(where / "Zed.app") + " has another line already.",
            "line 16: ~/Applications/Old Thing.app has another line already.",
        ])

    def test_a_cask_that_has_the_app_already(self):
        errors, _ = self.check(f"adopt cask:zed {self.m.h.apps}/Zed.app\n")
        self.assertEqual(errors, ["line 1: the cask zed has the app already. Use add or keep."])

    def test_notes(self):
        errors, notes = self.check(f"replace cask:zed {self.m.h.apps}/Slack.app\n")
        self.assertEqual((errors, notes), ([], ["line 1: you chose the cask zed; the scan proposed slack."]))

    def test_commands(self):
        x = plan.Line("trash", str(self.m.h.home / "Applications/Old Thing.app"))
        self.assertEqual(plan.commands([x], self.t), ["trash ~/'Applications/Old Thing.app'"])


class VersionTests(unittest.TestCase):
    def test_compare(self):
        self.assertEqual(plan.compare("7.2.2 (88465)", "7.2.2.88465"), 0)
        self.assertEqual(plan.compare("2.1", "2.1.0,430"), 0)
        self.assertEqual(plan.compare("1.9.6", "2.0.12"), -1)
        self.assertEqual(plan.compare("10.0", "9.9"), 1)
        self.assertIsNone(plan.compare("", "1.0"))


if __name__ == "__main__":
    unittest.main()
