"""The dotkit commands: the rows of PARITY.md that the parity scenarios cannot reach.

apply runs /opt/homebrew/bin/brew and history reads /nix/var/nix/profiles by
their absolute paths, so these tests point the module constants at a
temporary directory (A8, H2). upgrade restores the lock files on a signal
(U15), and --plan of a directory that does not exist is a usage error (B1).
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from dotkit import commands
from dotkit.commands import apply, recovery, upgrade
from dotkit.core import backup, env as env_mod, nix, ui
from tests import fake
from tests.cli import harness


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir().resolve()
        self.addCleanup(fake.remove, self.dir)
        for rel, text in {**harness.BASE, **harness.REPO}.items():
            p = self.dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        (self.dir / "home").mkdir()
        (self.dir / "repo/.build").mkdir()
        self.env = env_mod.Env(repo=self.dir / "repo", dotkit=self.dir / "dotkit", home=self.dir / "home")
        self.out, self.err = io.BytesIO(), io.BytesIO()
        for name, buf in (("stdout", self.out), ("stderr", self.err)):
            p = mock.patch.object(sys, name, io.TextIOWrapper(buf, encoding="utf-8"))
            p.start()
            self.addCleanup(p.stop)

    def fakes(self, rules):
        p = mock.patch.dict(os.environ, harness.install_fakes(self.dir, rules))
        p.start()
        self.addCleanup(p.stop)

    def text(self):
        sys.stdout.flush()
        sys.stderr.flush()
        return self.out.getvalue().decode(), self.err.getvalue().decode()

    def calls(self):
        return [[c["tool"], *c["argv"]] for c in harness.fake_calls(self.dir)]


REPORT = ("Would uninstall formulae:\nold-tool\nWould `brew cleanup`:\n/Users/x/Library/Caches/Homebrew/a.tar.gz\n"
          "Run `brew bundle cleanup --force` to make these changes.\n")
ONLY_DOWNLOADS = ("Would `brew cleanup`:\n/Users/x/Library/Caches/Homebrew/a.tar.gz\n"
                  "Run `brew bundle cleanup --force` to make these changes.\n")


class PreviewTests(Sandbox):
    """PARITY.md, A7 and A8: the Homebrew cleanup preview of apply -n."""

    def preview(self, cleanup, output, code):
        brew = self.dir / "brew"
        brew.write_text(f"#!/bin/sh\necho \"$@\" > {self.dir}/brew-args\ncat <<'EOF'\n{output}EOF\nexit {code}\n")
        brew.chmod(0o755)
        self.fakes([{"tool": "nix", "re": "onActivation.cleanup", "stdout": cleanup},
                    {"tool": "nix", "re": "homebrew.brewfile", "stdout": 'brew "jq"\n'}])
        with mock.patch.object(apply, "BREW", str(brew)):
            return apply.preview_brew_cleanup(self.env, nix.Nix(self.env))

    def test_cleanup_none_runs_no_brew(self):
        self.assertEqual(self.preview("none", "", 0), 0)
        self.assertFalse((self.dir / "brew-args").exists())
        self.assertEqual(self.text(), ("", ""))

    def test_removals_and_downloads(self):
        self.assertEqual(self.preview("uninstall", REPORT, 1), 0)
        self.assertEqual(self.text()[0], "Homebrew removes:\nWould uninstall formulae:\nold-tool\n")
        brewfile = self.dir / "repo/.build/Brewfile"
        self.assertEqual(brewfile.read_text(), 'brew "jq"\n')
        self.assertEqual((self.dir / "brew-args").read_text(), f"bundle cleanup --file={brewfile}\n")

    def test_only_downloads(self):
        self.assertEqual(self.preview("zap", ONLY_DOWNLOADS, 1), 0)
        self.assertEqual(self.text(), ("", ""))

    def test_no_report(self):
        self.assertEqual(self.preview("uninstall", "", 0), 0)
        self.assertEqual(self.text(), ("", ""))

    def test_failure(self):
        with self.assertRaises(ui.Exit) as c:
            self.preview("uninstall", "Error: no Brewfile\n", 1)
        self.assertEqual(c.exception.text, "dotkit: the Homebrew cleanup preview failed with exit code 1.")
        self.assertEqual(self.text()[0], "Homebrew removes:\nError: no Brewfile\n")

    def test_failure_after_a_report(self):
        with self.assertRaises(ui.Exit) as c:
            self.preview("uninstall", REPORT, 2)
        self.assertEqual(c.exception.code, 1)
        self.assertIn("exit code 2.", c.exception.text)


class HistoryTests(Sandbox):
    """PARITY.md, H2."""

    def profiles(self, *links):
        d = self.dir / "profiles"
        d.mkdir()
        (d / "gen").mkdir()
        for name in links:
            (d / name).symlink_to(d / "gen")
        return d

    def test_previous_generation(self):
        d = self.profiles("system-41-link", "system-42-link")
        (d / "system").symlink_to("system-42-link")
        self.fakes([{"tool": "nix", "re": "^store diff-closures"}])
        with mock.patch.object(recovery, "PROFILES", str(d)):
            self.assertEqual(recovery.history(self.env, []), 0)
        # The output of nix goes to the real stdout, not to the buffer of this test.
        self.assertEqual(self.text()[0], "system-41-link -> system-42-link\n")
        self.assertEqual(self.calls(), [["nix", "store", "diff-closures", f"{d}/system-41-link",
                                         f"{d}/system-42-link"]])

    def test_no_previous_generation(self):
        d = self.profiles("system-1-link")
        (d / "system").symlink_to("system-1-link")
        with mock.patch.object(recovery, "PROFILES", str(d)), self.assertRaises(ui.Exit) as c:
            recovery.history(self.env, [])
        self.assertEqual(c.exception.text, "dotkit: there is no previous generation.")

    def test_no_system_link(self):
        d = self.profiles()
        with mock.patch.object(recovery, "PROFILES", str(d)):
            self.assertEqual(recovery.history(self.env, []), 1)


class UpgradeSignalTests(Sandbox):
    """PARITY.md, U15: a signal after the lock files changed restores them."""

    def test_signals(self):
        lock = self.dir / "repo/nix/flake.lock"
        before = lock.read_text()
        new = json.dumps({"nodes": {"root": {"inputs": {"dotkit": "dotkit"}},
                                    "dotkit": {"locked": {"lastModified": 1761000000, "rev": "d" * 14}}}})
        self.fakes([{"tool": "nix", "re": "^flake update", "actions": [{"write": str(lock), "text": new}]}])
        for exc in (KeyboardInterrupt(), backup.Stopped(15)):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(nix.Nix, "build_system", side_effect=exc):
                    with self.assertRaises(type(exc)):
                        upgrade.upgrade(self.env, ["nix"], False, True)
                self.assertEqual(lock.read_text(), before)


class UpgradeBrewfileTests(Sandbox):
    def test_a_failed_brewfile_stops_before_brew_bundle(self):
        """PARITY.md, B4."""
        outdated = {"formulae": [{"name": "wget", "installed_versions": ["1"], "current_version": "2"}],
                    "casks": []}
        self.fakes([{"tool": "brew", "re": "^update"},
                    {"tool": "brew", "re": "^outdated", "stdout": json.dumps(outdated)},
                    {"tool": "nix", "re": "brewfile", "exit": 3, "stderr": "error: eval\n"},
                    {"tool": "brew", "re": "^bundle"}])
        self.assertEqual(upgrade.upgrade(self.env, ["brew"], False, True), 3)
        self.assertEqual([c[:2] for c in self.calls()], [["brew", "update"], ["brew", "outdated"], ["nix", "eval"]])


class PlanTests(unittest.TestCase):
    def test_plan_path(self):
        d = fake.tempdir().resolve()
        self.addCleanup(fake.remove, d)
        cwd = os.getcwd()
        os.chdir(d)
        self.addCleanup(os.chdir, cwd)
        (d / "sub").mkdir()
        self.assertEqual(commands.plan_path("p.txt"), f"{d}/p.txt")
        self.assertEqual(commands.plan_path("sub/p.txt"), f"{d}/sub/p.txt")
        self.assertEqual(commands.plan_path("sub/"), f"{d}/sub")
        with self.assertRaises(ui.Exit) as c:
            commands.plan_path("nope/p.txt")
        self.assertEqual((c.exception.code, c.exception.text),
                         (2, "dotkit: 'nope/p.txt' is not in a directory that exists. Use dotkit --help."))


if __name__ == "__main__":
    unittest.main()
