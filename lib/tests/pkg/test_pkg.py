"""The pkg commands: the rows of PARITY.md that the parity scenarios cannot reach, and the selection rules.

The scenarios (tests/cli) cover the commands end to end. These tests cover
the rules of resolve.choose as a table, the cut of the descriptions in a
terminal (Pc1), the directories of global tools (X1), the restore on a
signal (Ad13), and that no jq is needed (P5, DV1).
"""

from __future__ import annotations

import io
import os
import signal
import sys
import unittest
from pathlib import Path
from unittest import mock

from dotkit.core import env as env_mod
from dotkit.core import ui
from dotkit.pkg import cli, declared, edit
from dotkit.pkg.lookups import Found
from dotkit.pkg.resolve import Candidates, choose
from tests import fake
from tests.cli import harness

F = Found


class ChooseTests(unittest.TestCase):
    def test_table(self):
        app, cask, formula = F("1", app=True), F("1"), F("1")
        cases = [
            ({"app": app, "formula": formula}, None, "Homebrew has an app and a command-line tool"),
            ({"app": app, "nix": F("9")}, "app", "a macOS app"),
            ({"nix": F("1.2"), "mise": F("1.2")}, "nix", "Nixpkgs has the current release"),
            ({"nix": F("1.10"), "mise": F("1.9")}, "nix", "Nixpkgs has the current release"),
            ({"nix": F("1.2"), "mise": F("v1.3")}, "mise", "Nixpkgs has the older 1.2"),
            ({"nix": F(""), "mise": F("2")}, "nix", "Nixpkgs has the current release"),
            ({"nix": F("1"), "mise": F("")}, "nix", "Nixpkgs has the current release"),
            ({"nix": F("1"), "formula": formula}, "nix", "not in the mise registry"),
            ({"mise": F("1"), "cask": cask}, "mise", "not in Nixpkgs"),
            ({"formula": formula, "cask": cask, "npm": F("1")}, "formula", "not in Nixpkgs or mise"),
            ({"cask": cask}, "cask", "not in Nixpkgs or mise"),
            ({"npm": F("1")}, None, "only npm or PyPI has this name."),
            ({"pypi": F("1")}, None, "only npm or PyPI has this name."),
            ({}, None, "no source has this name."),
        ]
        for found, key, reason in cases:
            with self.subTest(found=sorted(found)):
                got_key, got_reason = choose(Candidates(name="x", found=found))
                self.assertEqual(got_key, key)
                self.assertTrue(got_reason.startswith(reason), got_reason)

    def test_nix_failure_comes_first(self):
        c = Candidates(name="x", found={"app": F(app=True)}, nix_failed=True)
        self.assertEqual(choose(c)[0], None)
        self.assertIn("nix eval failed", choose(c)[1])


class CandidatesTests(unittest.TestCase):
    def lines(self, columns, desc):
        c = Candidates(name="t", found={"nix": F("1.0", desc), "mise": F("", "short")})
        buf = io.BytesIO()
        with mock.patch.object(ui, "terminal_columns", return_value=columns), \
                mock.patch.object(sys, "stdout", io.TextIOWrapper(buf, encoding="utf-8")):
            cli.print_candidates(c, None)
            sys.stdout.flush()
            return buf.getvalue().decode().splitlines()

    def test_cut_to_the_terminal(self):
        long = "x" * 40 + "é" * 40
        self.assertEqual(self.lines(None, long)[0], f"    nix:t{' ' * 25} 1.0{' ' * 11} {long}")
        self.assertEqual(self.lines(100, long)[0].split(" 1.0" + " " * 11 + " ")[1], long[:49] + "…")
        self.assertEqual(self.lines(30, long)[0].split(" 1.0" + " " * 11 + " ")[1], long[:19] + "…")
        self.assertEqual(self.lines(100, "x" * 50)[0][-50:], "x" * 50)
        self.assertEqual(self.lines(100, "")[1], f"    mise:t{' ' * 24} ?{' ' * 13} short")


class Sandbox(unittest.TestCase):
    """A dotfiles repository and a dotkit with the fake tools, for the pkg code in this process."""

    def setUp(self):
        self.dir = fake.tempdir().resolve()
        self.addCleanup(fake.remove, self.dir)
        for rel, text in {**harness.BASE, **harness.REPO}.items():
            p = self.dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        (self.dir / "home").mkdir()
        self.env = env_mod.Env(repo=self.dir / "repo", dotkit=self.dir / "dotkit", home=self.dir / "home")

    def fakes(self, rules, path=None):
        fakes = harness.install_fakes(self.dir, rules)
        if path is not None:
            fakes["PATH"] = path
        patcher = mock.patch.dict(os.environ, fakes)
        patcher.start()
        self.addCleanup(patcher.stop)

    def quiet(self):
        """stdout and stderr of the code into buffers, as UTF-8 bytes."""
        out, err = io.BytesIO(), io.BytesIO()
        for name, buf in (("stdout", out), ("stderr", err)):
            p = mock.patch.object(sys, name, io.TextIOWrapper(buf, encoding="utf-8"))
            p.start()
            self.addCleanup(p.stop)
        return out, err


class NoJqTests(Sandbox):
    def test_list_without_jq(self):
        """PARITY.md, P5 and DV1: the commands do not need jq."""
        self.fakes([{"tool": "nix", "re": "masApps", "stdout": '{"Keynote": 1}'}], path=str(self.dir / "bin"))
        out, _ = self.quiet()
        self.assertEqual(cli.main(self.env, ["list", "brew"]), 0)
        sys.stdout.flush()
        self.assertIn("brew  Keynote", out.getvalue().decode())


class ExistingCommandTests(Sandbox):
    def test_the_directories_come_before_mise(self):
        """PARITY.md, X1: the global directories in order, then `mise which`."""
        dirs = [self.dir / d for d in ("profile", "system", "user", "brew")]
        for d in dirs:
            d.mkdir()
        (dirs[1] / "tool").write_text("")
        (dirs[1] / "tool").chmod(0o755)
        (dirs[3] / "tool").write_text("")
        (dirs[3] / "tool").chmod(0o755)
        (dirs[0] / "notexec").write_text("")
        self.fakes([{"tool": "mise", "re": "^which notexec$", "stdout": "/mise/notexec\n"}])
        with mock.patch.object(declared, "global_bin_dirs", return_value=[str(d) for d in dirs]):
            self.assertEqual(declared.existing_command(self.env, "tool"), f"{dirs[1]}/tool")
            self.assertEqual(declared.existing_command(self.env, "notexec"), "/mise/notexec")
            self.assertIsNone(declared.existing_command(self.env, "missing"))

    def test_the_real_directories(self):
        dirs = declared.global_bin_dirs(Path("/h"))
        self.assertEqual(dirs[1:], ["/run/current-system/sw/bin", "/h/.nix-profile/bin", "/opt/homebrew/bin"])
        self.assertRegex(dirs[0], r"^/etc/profiles/per-user/[^/]+/bin$")


class SignalTests(Sandbox):
    def test_ctrl_c_in_the_build_restores_the_lists(self):
        """PARITY.md, Ad13: SIGINT while the build runs."""
        self.fakes([{"tool": "nix", "re": "^build"}])
        self.quiet()
        packages = self.dir / "repo/nix/packages.txt"

        def change():
            packages.write_text("changed\n")

        with mock.patch("dotkit.core.nix.Nix.build_system", side_effect=KeyboardInterrupt):
            with self.assertRaises(ui.Exit) as c:
                edit.apply_nix(self.env, change)
        self.assertEqual((c.exception.code, c.exception.text), (130, "\ndotkit: stopped. The Nix files are restored."))
        self.assertEqual(packages.read_text(), "jq\n")

    def test_sigterm_in_the_edit_restores_the_lists(self):
        self.quiet()
        casks = self.dir / "repo/nix/casks.txt"

        def change():
            casks.write_text("half")
            os.kill(os.getpid(), signal.SIGTERM)

        with self.assertRaises(ui.Exit) as c:
            edit.apply_nix(self.env, change)
        self.assertEqual(c.exception.code, 130)
        self.assertEqual(casks.read_text(), "zed")
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)

    def test_a_failed_edit_restores_the_lists(self):
        self.quiet()
        packages = self.dir / "repo/nix/packages.txt"

        def change():
            packages.write_text("half")
            raise PermissionError("read-only")

        with self.assertRaises(ui.Exit) as c:
            edit.apply_nix(self.env, change)
        self.assertEqual((c.exception.code, c.exception.text),
                         (1, "dotkit: could not change the Nix files. They are restored."))
        self.assertEqual(packages.read_text(), "jq\n")


if __name__ == "__main__":
    unittest.main()
