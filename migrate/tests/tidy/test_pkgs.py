"""dotkit tidy for installer packages: the receipts, the default verbs, the check, and the apply (TIDY.md, 8.2)."""

import argparse
import contextlib
import io
import os
import subprocess
import unittest
from unittest import mock

from dotkit_migrate.context import tilde
from dotkit_migrate.tidy import actions, cli, pkgs, plan
from dotkit_migrate.tidy.cli import declared_of
from tests import fake
from tests.tidy.mac import TidyMac
from tests.tidy.test_cli import Answer, FakeActions


class PkgMac(TidyMac):
    """A Mac with installer packages. The volume of each receipt is the root of the fake Mac."""

    def __init__(self, test):
        super().__init__(test)
        self.repo = self.h.home / ".dotfiles"
        # Jabra Direct: an app and a file in Application Support.
        self.app("Jabra Direct.app", "com.jabra.directonline", "8.3")
        self.file("Library/Application Support/Jabra/config.json")
        self.receipt("com.jabra.directonline", ["Applications/Jabra Direct.app/Contents/Info.plist",
                                                "Applications/Jabra Direct.app/Contents/Frameworks/X.framework/X",
                                                "Library/Application Support/Jabra/config.json"],
                     location="", version="8.3")
        # Tailscale: a launch daemon inside the app.
        self.app("Tailscale.app", "io.tailscale.ipn.macsys")
        self.receipt("com.tailscale.ipn.macsys", ["Tailscale.app/Contents/Info.plist",
                                                  "Tailscale.app/Contents/Library/LaunchDaemons/x.plist"])
        # A stale receipt, with its location inside a bundle that is gone.
        self.receipt("com.google.Keystone", ["Contents/Info.plist", "Contents/MacOS/x"],
                     location="Library/Google/GoogleUpdater.app", version="0")
        # Two packages that share a file, and one in a system directory.
        self.file("usr/local/lib/libshared.dylib")
        self.file("usr/bin/vendor-tool")
        self.receipt("com.vendor.one", ["usr/local/lib/libshared.dylib"], location="")
        self.receipt("com.vendor.two", ["usr/local/lib/libshared.dylib", "usr/bin/vendor-tool"], location="")
        # The App Store and an installed cask have their own receipts.
        self.app("Pixelmator Pro.app", "com.pixelmatorteam.pixelmator.x", mas=1289583905)
        self.receipt("com.pixelmatorteam.pixelmator.x", ["Pixelmator Pro.app/Contents/Info.plist"])
        self.app("Karabiner-Elements.app", "org.pqrs.Karabiner-Elements.Settings")
        self.receipt("org.pqrs.Karabiner-Elements", ["Karabiner-Elements.app/Contents/Info.plist"])
        self.cask("karabiner-elements", pkgutil=["org.pqrs.Karabiner-Elements"], pkg=True, installed="16.3.0")

    def file(self, rel):
        p = self.h.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        return p

    def receipts(self, t):
        return {r.id: r for r in t.receipts}

    def lines(self):
        ctx = self.context()
        t = self.scan()
        lines, _ = plan.defaults(t, declared_of(ctx, self.repo, {}))
        return t, {x.thing: (x.verb, x.path, x.note) for x in lines if x.thing.startswith("pkg:")}


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.m = PkgMac(self)
        self.t = self.m.scan()
        self.r = self.m.receipts(self.t)

    def test_units_are_bundles_and_files(self):
        root, r = str(self.m.h.root), self.r
        self.assertEqual(r["com.jabra.directonline"].units, [f"{root}/Applications/Jabra Direct.app",
                                                             f"{root}/Library/Application Support/Jabra/config.json"])
        self.assertEqual(r["com.jabra.directonline"].present, r["com.jabra.directonline"].units)
        self.assertEqual(r["com.google.Keystone"].units, [f"{root}/Library/Google/GoogleUpdater.app"])
        self.assertEqual(r["com.google.Keystone"].present, [])
        self.assertNotIn("com.apple.pkg.Something", r)

    def test_system_parts_shared_and_protected(self):
        root, r = str(self.m.h.root), self.r
        self.assertEqual(r["com.tailscale.ipn.macsys"].system_parts,
                         [f"{root}/Applications/Tailscale.app/Contents/Library/LaunchDaemons/x.plist"])
        self.assertEqual((r["com.vendor.one"].present, r["com.vendor.one"].shared),
                         ([], [f"{root}/usr/local/lib/libshared.dylib"]))
        self.assertEqual(r["com.vendor.two"].protected, [f"{root}/usr/bin/vendor-tool"])

    def test_the_app_store_and_installed_casks(self):
        self.assertTrue(self.r["com.pixelmatorteam.pixelmator.x"].mas)
        self.assertEqual(self.r["org.pqrs.Karabiner-Elements"].cask, "karabiner-elements")

    def test_the_apps_get_their_receipts(self):
        apps = {a.name: a for a in self.t.apps}
        self.assertEqual((apps["Jabra Direct.app"].source, apps["Jabra Direct.app"].pkg_id),
                         ("pkg", "com.jabra.directonline"))
        self.assertEqual(apps["Tailscale.app"].system_parts, self.r["com.tailscale.ipn.macsys"].system_parts)
        self.assertEqual((apps["Karabiner-Elements.app"].source, apps["Karabiner-Elements.app"].cask),
                         ("cask", "karabiner-elements"))

    def test_helpers(self):
        self.assertEqual(pkgs.unit_of("/Applications/A.app/Contents/Frameworks/B.framework/B"), "/Applications/A.app")
        self.assertEqual(pkgs.unit_of("/usr/local/bin/x"), "/usr/local/bin/x")
        self.assertEqual(pkgs.part_name("/A.app/Contents/Library/SystemExtensions/x.dext/Info.plist"), "x.dext")
        self.assertEqual(pkgs.on("/Volumes/Data", "/Volumes/Data/Library/x"), "/Library/x")


class DefaultTests(unittest.TestCase):
    def setUp(self):
        self.m = PkgMac(self)
        self.t, self.got = self.m.lines()

    def test_default_verbs(self):
        got, root = self.got, str(self.m.h.root)
        self.assertEqual(got["pkg:com.google.Keystone"],
                         ("uninstall", f"{root}/Library/Google/GoogleUpdater.app",
                          "version 0; no files of it are left, so uninstall only forgets the package"))
        self.assertEqual(got["pkg:com.jabra.directonline"],
                         ("keep", f"{root}/Applications/Jabra Direct.app",
                          "version 8.3; uninstall moves 2 items to the Trash"))
        self.assertEqual(got["pkg:com.tailscale.ipn.macsys"][0], "keep")
        self.assertIn("system parts (x.plist) that only the vendor's uninstaller removes",
                      got["pkg:com.tailscale.ipn.macsys"][2])
        self.assertEqual(got["pkg:com.vendor.one"][::2], ("keep", "version 1.0; uninstall only forgets the package; 1 "
                                                                  "that another package has too stay"))
        self.assertEqual(got["pkg:com.vendor.two"][::2],
                         ("keep", "version 1.0; uninstall only forgets the package; 1 that another package has too "
                                  "and 1 in a system directory stay"))
        self.assertNotIn("pkg:com.pixelmatorteam.pixelmator.x", got)
        self.assertNotIn("pkg:org.pqrs.Karabiner-Elements", got)

    def test_the_text(self):
        lines, listed = plan.defaults(self.t, declared_of(self.m.context(), self.m.repo, {}))
        text = plan.render(lines, self.t, listed)
        self.assertIn("and 5 installer packages.", text)
        self.assertLess(text.index("Jabra Direct.app  "), text.index("# Installer packages"))
        parsed, errors = plan.parse(text, self.t)
        self.assertEqual(errors, [])
        self.assertEqual(plan.check(parsed, self.t), ([], []))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.m = PkgMac(self)
        self.t = self.m.scan()

    def errors(self, text):
        parsed, errors = plan.parse(text, self.t)
        return errors + plan.check(parsed, self.t)[0]

    def test_errors(self):
        apps, root = self.m.h.apps, self.m.h.root
        self.assertEqual(self.errors("\n".join([
            f"uninstall pkg:com.tailscale.ipn.macsys {apps}/Tailscale.app",
            f"uninstall pkg:com.pixelmatorteam.pixelmator.x {apps}/Pixelmator Pro.app",
            f"uninstall pkg:org.pqrs.Karabiner-Elements {apps}/Karabiner-Elements.app",
            f"uninstall pkg:com.nope {apps}/Nope.app",
            f"uninstall pkg:com.jabra.directonline {root}/Library",
            f"trash pkg:com.google.Keystone {root}/Library/Google/GoogleUpdater.app",
            f"keep pkg:com.google.Keystone {root}/Library/Google/GoogleUpdater.app",
        ])), [
            "line 1: com.tailscale.ipn.macsys has system parts (x.plist). Use the uninstaller of its vendor.",
            "line 2: com.pixelmatorteam.pixelmator.x is a package of the App Store. The App Store app has its own "
            "line.",
            "line 3: the cask karabiner-elements has the package org.pqrs.Karabiner-Elements. "
            "`dotkit remove cask:karabiner-elements` uninstalls it.",
            "line 4: there is no package com.nope.",
            f"line 5: the path of pkg:com.jabra.directonline is {apps}/Jabra Direct.app.",
            "line 6: trash is not for packages. Use uninstall or keep.",
            "line 7: pkg:com.google.Keystone has another line already.",
        ])

    def test_a_line_that_changes_a_file_of_the_package(self):
        apps = self.m.h.apps
        self.assertEqual(self.errors(f"uninstall pkg:com.jabra.directonline {apps}/Jabra Direct.app\n"
                                     f"trash {apps}/Jabra Direct.app\n"),
                         [f"line 1: uninstall pkg:com.jabra.directonline moves {apps}/Jabra Direct.app, and line 2 "
                          "has trash for it. Keep one of them."])
        self.assertEqual(self.errors(f"uninstall pkg:com.jabra.directonline {apps}/Jabra Direct.app\n"
                                     f"keep {apps}/Jabra Direct.app\n"), [])

    def test_commands(self):
        root, apps = self.m.h.root, self.m.h.apps
        lines = [plan.Line("uninstall", f"{apps}/Jabra Direct.app", "pkg:com.jabra.directonline"),
                 plan.Line("uninstall", f"{root}/Library/Google/GoogleUpdater.app", "pkg:com.google.Keystone")]
        self.assertEqual(plan.commands(lines, self.t), [
            "sudo: move 2 items of it to the Trash, then pkgutil --forget com.jabra.directonline",
            "sudo: pkgutil --forget com.google.Keystone",
        ])


class PkgActions(FakeActions):
    def __init__(self, brew, fail_pkg=(), **kw):
        super().__init__(brew, **kw)
        self.fail_pkg = set(fail_pkg)

    def pkg_uninstall(self, pkg_id, units, trash_dir):
        self._call(f"pkg {pkg_id} {len(units)} -> {os.path.basename(trash_dir)}")
        if pkg_id in self.fail_pkg:
            return False, "could not move x"
        return True, ""


class ApplyTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch("socket.gethostname", return_value="test-mac.local"),
                      mock.patch("time.strftime", return_value="2026-10-27 10.00.00")):
            patch.start()
            self.addCleanup(patch.stop)
        self.m = PkgMac(self)

    def run_tidy(self, text, **kw):
        p = self.m.h.root / "plan.txt"
        p.write_text(text)
        a = argparse.Namespace(repo=str(self.m.repo), declared=None, dry_run=False, yes=False, base=None, plan=str(p))
        ctx = self.m.context()
        made = []

        def make_actions(brew):
            made.append(PkgActions(brew, **kw))
            return made[0]

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch("sys.stdin", Answer("y\n")):
            code = cli.cmd_tidy(a, lambda home, b: ctx, make_actions)
        return code, out.getvalue(), err.getvalue(), made[0] if made else None

    def test_uninstall_packages(self):
        apps, root = self.m.h.apps, self.m.h.root
        self.m.procs = [f"{apps}/Jabra Direct.app/Contents/MacOS/Jabra"]
        code, out, err, acts = self.run_tidy(
            f"uninstall pkg:com.jabra.directonline {apps}/Jabra Direct.app\n"
            f"uninstall pkg:com.google.Keystone {root}/Library/Google/GoogleUpdater.app\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(acts.calls, ["quit com.jabra.directonline",
                                      "pkg com.jabra.directonline 2 -> com.jabra.directonline 2026-10-27 10.00.00",
                                      "pkg com.google.Keystone 0 -> com.google.Keystone 2026-10-27 10.00.00"])
        self.assertIn("Uninstalled:\n  pkg:com.jabra.directonline\n  pkg:com.google.Keystone", out)
        self.assertIn("In the Trash:\n  ~/.Trash/com.jabra.directonline 2026-10-27 10.00.00", out)
        self.assertIn("sudo: move 2 items of it to the Trash, then pkgutil --forget com.jabra.directonline", out)

    def test_a_failed_package(self):
        apps = self.m.h.apps
        code, out, _, acts = self.run_tidy(f"uninstall pkg:com.jabra.directonline {apps}/Jabra Direct.app\n",
                                           fail_pkg=["com.jabra.directonline"])
        self.assertEqual(code, 1)
        self.assertIn("could not move x. Run dotkit tidy again, or use the uninstaller of its vendor.", out)
        self.assertNotIn("In the Trash", out)
        self.assertEqual(acts.calls[-1], "pkg com.jabra.directonline 2 -> com.jabra.directonline 2026-10-27 10.00.00")

    def test_the_trash_directory_of_a_real_run(self):
        """Actions.pkg_uninstall gets the directory in ~/.Trash, with the package ID and the time."""
        apps = self.m.h.apps
        with mock.patch.object(cli, "apply_pkg", return_value=(True, "")) as apply_pkg:
            self.run_tidy(f"uninstall pkg:com.jabra.directonline {apps}/Jabra Direct.app\n")
        self.assertEqual(tilde(apply_pkg.call_args[0][2], str(self.m.h.home)),
                         "~/.Trash/com.jabra.directonline 2026-10-27 10.00.00")


class ActionsTests(unittest.TestCase):
    """Actions.pkg_uninstall with the real mkdir, mv, and chown, without sudo, on temp files; pkgutil is a fake."""

    def setUp(self):
        self.root = fake.tempdir()
        self.addCleanup(fake.remove, self.root)
        self.calls = []
        real_run = subprocess.run

        def run(argv, check=False, **kw):
            assert argv[0] == "/usr/bin/sudo", argv
            argv = argv[1:]
            self.calls.append(" ".join(os.path.basename(a) if i == 0 else a for i, a in enumerate(argv)))
            if argv[0] == "/usr/sbin/pkgutil":
                return mock.Mock(returncode=0 if argv[-1] != "com.fails" else 1)
            return real_run(argv, check=check, **kw)

        patch = mock.patch("subprocess.run", run)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_units_go_under_the_trash_directory_and_the_package_is_forgotten(self):
        app = self.root / "Applications/Vendor.app"
        (app / "Contents").mkdir(parents=True)
        (app / "Contents/Info.plist").write_text("x")
        conf = self.root / "Library/Vendor/conf.json"
        conf.parent.mkdir(parents=True)
        conf.write_text("x")
        trash = str(self.root / "home/.Trash/com.vendor 2026-10-27 10.00.00")
        ok, why = actions.Actions("brew").pkg_uninstall("com.vendor", [str(app), str(conf)], trash)
        self.assertEqual((ok, why), (True, ""))
        self.assertFalse(app.exists() or conf.exists())
        self.assertTrue(os.path.isfile(trash + str(app) + "/Contents/Info.plist"))
        self.assertTrue(os.path.isfile(trash + str(conf)))
        self.assertEqual(self.calls[-1], "pkgutil --forget com.vendor")
        self.assertIn(f"chown -R {os.getuid()} {trash}", self.calls)

    def test_a_unit_that_cannot_move_keeps_the_package(self):
        trash = str(self.root / "home/.Trash/com.vendor x")
        ok, why = actions.Actions("brew").pkg_uninstall("com.vendor", [str(self.root / "missing.txt")], trash)
        self.assertEqual((ok, why), (False, f"could not move {self.root / 'missing.txt'}"))
        self.assertFalse(any(c.startswith("pkgutil") for c in self.calls))

    def test_a_stale_package_is_only_forgotten(self):
        self.assertEqual(actions.Actions("brew").pkg_uninstall("com.stale", [], "unused"), (True, ""))
        self.assertEqual(self.calls, ["pkgutil --forget com.stale"])
        self.assertEqual(actions.Actions("brew").pkg_uninstall("com.fails", [], "unused"),
                         (False, "pkgutil --forget failed"))


if __name__ == "__main__":
    unittest.main()
