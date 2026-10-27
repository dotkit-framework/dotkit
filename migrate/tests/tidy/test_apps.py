"""The scan of dotkit tidy: the apps, their sources, and the casks that match them (TIDY.md, section 3)."""

import os
import unittest

from dotkit_migrate.tidy import apps, pkgs
from tests.tidy.mac import TidyMac


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.m = TidyMac(self)

    def test_an_app_by_hand_gets_the_cask_without_at(self):
        m = self.m
        m.app("Slack.app", "com.tinyspeck.slackmacgap", "4.52.182")
        for token in ("slack", "slack@beta"):
            m.cask(token, apps=["Slack.app"], quit=["com.tinyspeck.slackmacgap"], auto_updates=True)
        slack = m.found(m.scan(), "Slack.app")
        self.assertEqual((slack.source, slack.match, slack.confirmed), ("hand", "slack", True))

    def test_the_bundle_id_selects_one_of_two_casks_with_the_name_of_the_app(self):
        """Telegram.app: telegram (Swift) and telegram-desktop (Qt); the zap paths have the IDs."""
        m = self.m
        m.app("Telegram.app", "ru.keepcoder.Telegram")
        m.cask("telegram", apps=["Telegram.app"], zap=["~/Library/Preferences/ru.keepcoder.Telegram.plist"])
        m.cask("telegram-x", apps=["Telegram.app"], zap=["~/Library/Preferences/org.telegram.x.plist"])
        app = m.found(m.scan(), "Telegram.app")
        self.assertEqual((app.match, app.confirmed), ("telegram", True))

    def test_an_app_artifact_with_a_target_has_the_name_of_the_target(self):
        m = self.m
        m.app("Telegram.app", "ru.keepcoder.Telegram")
        m.app("Telegram Desktop.app", "com.tdesktop.Telegram")
        m.cask("telegram-desktop", apps=["Telegram.app"], target="Telegram Desktop.app",
               zap=["~/Library/Preferences/com.tdesktop.Telegram.plist"])
        t = m.scan()
        self.assertEqual(m.found(t, "Telegram.app").match, "")
        self.assertEqual(m.found(t, "Telegram Desktop.app").match, "telegram-desktop")

    def test_a_cask_whose_uninstall_names_another_bundle_id_is_another_edition(self):
        """1Password 7 and 8: the data does not follow, so the scan proposes no cask."""
        m = self.m
        m.app("1Password.app", "com.agilebits.onepassword7")
        m.cask("1password", apps=["1Password.app"], quit=["com.1password.1password"], auto_updates=True)
        app = m.found(m.scan(), "1Password.app")
        self.assertEqual((app.match, app.editions), ("", ["1password"]))

    def test_zap_paths_with_old_ids_do_not_make_another_edition(self):
        """MonitorControl: the zap paths keep the IDs of old versions; only uninstall IDs name the app."""
        m = self.m
        m.app("MonitorControl.app", "app.monitorcontrol.MonitorControl", "4.4.0")
        m.cask("monitorcontrol", apps=["MonitorControl.app"],
               zap=["~/Library/Preferences/me.guillaumeb.MonitorControl.plist"], auto_updates=True)
        app = m.found(m.scan(), "MonitorControl.app")
        self.assertEqual((app.match, app.confirmed, app.cask_ids),
                         ("monitorcontrol", False, ["me.guillaumeb.MonitorControl"]))

    def test_a_pkg_cask_by_its_uninstall_path_and_the_shortest_token(self):
        """zoom has no app artifact; zoom-for-it-admins deletes the same app."""
        m = self.m
        m.app("zoom.us.app", "us.zoom.xos", "7.2.2 (88465)")
        for token in ("zoom", "zoom-for-it-admins"):
            m.cask(token, delete=["/Applications/zoom.us.app"], quit=["us.zoom.xos"], pkg=True, auto_updates=True)
        t = m.scan()
        self.assertEqual(m.found(t, "zoom.us.app").match, "zoom")
        self.assertTrue(t.casks["zoom"].pkg)

    def test_without_a_name_the_bundle_id_finds_the_cask(self):
        """Tailscale.app has no pkg receipt, and its cask names no app, only the bundle ID."""
        m = self.m
        m.app("Tailscale.app", "io.tailscale.ipn.macsys", "1.104.2")
        m.cask("tailscale-app", quit=["io.tailscale.ipn.macsys"], pkgutil=["com.tailscale.ipn.macsys"], pkg=True)
        self.assertEqual(m.found(m.scan(), "Tailscale.app").match, "tailscale-app")

    def test_several_casks_that_no_rule_separates_are_candidates(self):
        m = self.m
        m.app("Bar.app", "com.example.bar")
        m.cask("bar-one", apps=["Bar.app"])
        m.cask("bar-two", apps=["Bar.app"])
        app = m.found(m.scan(), "Bar.app")
        self.assertEqual((app.match, app.candidates), ("", ["bar-one", "bar-two"]))

    def test_the_token_form_of_the_name_wins(self):
        m = self.m
        m.app("Google Chrome.app", "com.google.Chrome")
        m.cask("google-chrome", apps=["Google Chrome.app"])
        m.cask("chrome-other", apps=["Google Chrome.app"])
        self.assertEqual(m.found(m.scan(), "Google Chrome.app").match, "google-chrome")

    def test_brew_info_of_the_matches_falls_back_to_one_command_per_cask(self):
        m = self.m
        m.app("Slack.app", "com.tinyspeck.slackmacgap")
        m.app("Zed.app", "dev.zed.Zed")
        m.cask("slack", apps=["Slack.app"], version="4.52.182", auto_updates=True)
        m.cask("zed", apps=["Zed.app"], version="1.25.3")
        runner = m.runner()
        t = apps.scan(m.context(runner))
        self.assertEqual((t.casks["slack"].version, t.casks["slack"].auto_updates), ("4.52.182", True))
        self.assertEqual((t.casks["zed"].version, t.casks["zed"].auto_updates), ("1.25.3", False))
        self.assertIn("brew info --json=v2 --cask slack zed", runner.calls)

    def test_without_the_bulk_file_tidy_proposes_no_cask(self):
        m = self.m
        m.write_bulk = False
        m.app("Slack.app", "com.tinyspeck.slackmacgap")
        t = m.scan()
        self.assertEqual(m.found(t, "Slack.app").match, "")
        self.assertIn("Run `brew update`, then dotkit tidy again.", t.warnings[0])

    def test_without_homebrew(self):
        m = self.m
        m.paths.pop("brew")
        m.app("Slack.app", "com.tinyspeck.slackmacgap")
        t = m.scan()
        self.assertEqual((t.brew, m.found(t, "Slack.app").source), ("", "hand"))
        self.assertEqual(t.warnings, ["Homebrew is not on this Mac, so tidy proposes no cask."])


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.m = TidyMac(self)

    def test_sources(self):
        m = self.m
        m.app("Slack.app", "com.tinyspeck.slackmacgap")
        m.cask("slack", apps=["Slack.app"], installed="4.52.182")
        m.app("Pages.app", "com.apple.iWork.Pages", mas=409201541)
        m.app("Xcode.app", "com.apple.dt.Xcode")
        m.app("CleanShot X.app", "pl.maketheweb.cleanshotx", where=m.h.apps / "Setapp")
        m.app("IntelliJ IDEA.app", "com.jetbrains.intellij", where=m.h.home / "Applications/JetBrains Toolbox")
        m.app("CorpVPN.app", "com.corp.vpn")
        m.receipt("com.corp.vpn.pkg", ["Applications/CorpVPN.app/Contents/Info.plist",
                                       "Library/LaunchDaemons/com.corp.vpn.helper.plist"], location="")
        m.app("Old.app", "com.example.old")
        t = m.scan()
        got = {a.name: a.source for a in t.apps}
        self.assertEqual(got, {"Slack.app": "cask", "Pages.app": "mas", "Xcode.app": "system",
                               "CleanShot X.app": "setapp", "IntelliJ IDEA.app": "toolbox", "CorpVPN.app": "pkg",
                               "Old.app": "hand"})
        self.assertEqual(m.found(t, "Slack.app").cask, "slack")
        self.assertEqual(m.found(t, "Pages.app").mas_id, 409201541)
        self.assertEqual(m.found(t, "CorpVPN.app").system_parts,
                         [str(m.h.root / "Library/LaunchDaemons/com.corp.vpn.helper.plist")])

    def test_an_installed_pkg_cask_has_the_app_that_its_bundle_id_finds(self):
        """Google Drive and SF Symbols: the cask names no app, and the package ID of the app differs."""
        m = self.m
        m.app("Google Drive.app", "com.google.drivefs")
        m.receipt("com.google.drivefs.shortcuts.x", ["Google Drive.app/Contents/Info.plist"])
        m.cask("google-drive", quit=["com.google.drivefs"], pkgutil=["com.google.drivefs.arm64"], pkg=True,
               installed="132.0")
        m.app("SF Symbols.app", "com.apple.SFSymbols")
        m.cask("sf-symbols", zap=["~/Library/Preferences/com.apple.SFSymbols.plist"], pkg=True, installed="7")
        t = m.scan()
        self.assertEqual([(a.source, a.cask) for a in t.apps], [("cask", "google-drive"), ("cask", "sf-symbols")])

    def test_an_installed_cask_by_its_package(self):
        m = self.m
        m.app("Karabiner-Elements.app", "org.pqrs.Karabiner-Elements.Settings")
        m.receipt("org.pqrs.Karabiner-Elements", ["Karabiner-Elements.app/Contents/Info.plist"])
        m.cask("karabiner-elements", pkgutil=["org.pqrs.Karabiner-Elements"], pkg=True, installed="16.3.0")
        app = m.found(m.scan(), "Karabiner-Elements.app")
        self.assertEqual((app.source, app.cask), ("cask", "karabiner-elements"))

    def test_what_the_scan_skips(self):
        m = self.m
        m.app("Real.app", "com.example.real")
        os.symlink(m.h.apps / "Real.app", m.h.apps / "Link.app")
        m.app("Docs.app", "com.example.docs", where=m.h.apps / "Chrome Apps.localized")
        m.app("Kitty.app", "net.kovidgoyal.kitty", where=m.h.apps / "Nix Apps")
        m.app("Inner.app", "com.example.inner", where=m.h.apps / "Real.app/Contents/Helpers")
        m.app("Tool.app", "com.example.tool", where=m.h.apps / "Utilities")
        self.assertEqual([a.name for a in m.scan().apps], ["Real.app", "Tool.app"])

    def test_an_app_without_info_plist(self):
        m = self.m
        m.app("Home VPN.app", plist=False)
        app = m.found(m.scan(), "Home VPN.app")
        self.assertEqual((app.bundle_id, app.version, app.source), ("", "", "hand"))

    def test_a_path_with_space_hash_is_skipped(self):
        m = self.m
        m.app("Tool #2.app", "com.example.tool2")
        t = m.scan()
        self.assertEqual((t.apps, len(t.skipped)), ([], 1))

    def test_writable_and_running(self):
        m = self.m
        locked = m.app("Locked.app", "com.example.locked")
        m.app("Busy.app", "com.example.busy")
        m.procs = [f"{m.h.apps}/Busy.app/Contents/MacOS/Busy", f"{m.h.apps}/Busyness.app/Contents/MacOS/x"]
        os.chmod(locked, 0o555)
        t = m.scan()
        self.assertEqual([(a.name, a.writable, a.running) for a in t.apps],
                         [("Busy.app", True, True), ("Locked.app", False, False)])


class HelperTests(unittest.TestCase):
    def test_token_form(self):
        self.assertEqual(apps.token_form("Google Chrome.app"), "google-chrome")
        self.assertEqual(apps.token_form("Notepad++.app"), "notepad-plus-plus")

    def test_pkg_patterns(self):
        self.assertTrue(pkgs.matches("com.google.drivefs.*", "com.google.drivefs.arm64"))
        self.assertTrue(pkgs.matches("a(b", "a(b"))
        self.assertFalse(pkgs.matches("com.example", "com.example.more"))


if __name__ == "__main__":
    unittest.main()
