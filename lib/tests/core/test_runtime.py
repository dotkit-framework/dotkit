"""core/env.py, args.py, ui.py, proc.py, nix.py, and backup.py.

The Nix tests use the fake tools of the scenario tests (tests/cli/fakebin.py).
The lock warning filter is compared with the awk program that it replaced,
copied here.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from dotkit.core import backup, env, nix, proc, ui
from tests import fake
from tests.cli import harness

LIB = Path(__file__).resolve().parents[2]
AWK = "/usr/bin/awk"
LOCK_FILTER = r'''
  /^warning: not writing (modified )?lock file of flake/ { skip = 1; next }
  skip && /^(• |  →|    )/ { next }
  { skip = 0; print; fflush() }
'''
NIX_STDERR = ("warning: not writing modified lock file of flake 'path:/r/nix':\n"
              "• Updated input 'dotkit':\n"
              "    'github:dotkit-framework/dotkit/ccccccc' (2025-10-09)\n"
              "  → 'git+file:///c' (2025-10-10)\n"
              "warning: Git tree '/c' is dirty\n"
              "    indented after another line\n"
              "warning: not writing lock file of flake 'x':\n"
              "• Added input 'y':\n"
              "real error\n"
              "• a bullet after a real line\n"
              "no final newline")


class EnvTests(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir().resolve()
        self.addCleanup(fake.remove, self.dir)
        (self.dir / "repo").mkdir()
        (self.dir / "checkout").mkdir()
        (self.dir / "link").symlink_to(self.dir / "repo")

    def load(self, **environ):
        e = {"HOME": str(self.dir), **environ}
        return env.load(e, dotkit=self.dir / "dotkit"), e

    def test_dotfiles_is_resolved_and_exported(self):
        got, e = self.load(DOTFILES=str(self.dir / "link"))
        self.assertEqual(got.repo, self.dir / "repo")
        self.assertEqual(e["DOTFILES"], str(self.dir / "repo"))
        self.assertEqual(got.flake, f"path:{self.dir}/repo/nix")
        self.assertEqual(got.result, self.dir / "repo/.build/system")
        self.assertEqual(got.override, [])

    def test_errors_in_order(self):
        cases = [
            ({}, 2, "dotkit: DOTFILES is not set. Run dotkit, or ./dotkit in your dotfiles repository."),
            ({"DOTFILES": "", "DOTKIT_DIR": "/nope"}, 2, "dotkit: DOTFILES is not set."),
            ({"DOTFILES": str(self.dir / "nope"), "DOTKIT_DIR": "/nope"}, 1, "dotkit: DOTFILES is not a directory: "),
            ({"DOTFILES": str(self.dir / "repo"), "DOTKIT_DIR": str(self.dir / "nope")}, 2,
             f"dotkit: DOTKIT_DIR is not a directory: {self.dir}/nope"),
        ]
        for environ, code, text in cases:
            with self.subTest(environ=environ):
                with self.assertRaises(ui.Exit) as c:
                    self.load(**environ)
                self.assertEqual(c.exception.code, code)
                self.assertTrue(c.exception.text.startswith(text), c.exception.text)

    def test_dotkit_dir(self):
        got, _ = self.load(DOTFILES=str(self.dir / "repo"), DOTKIT_DIR=str(self.dir / "checkout"))
        self.assertEqual(got.override, ["--override-input", "dotkit", f"git+file://{self.dir}/checkout",
                                        "--no-write-lock-file", "--option", "warn-dirty", "false"])

    def test_dotkit_root(self):
        self.assertTrue((env.DOTKIT / "lib/dotkit/core/env.py").is_file())


CONFIRM = ("import sys; sys.path.insert(0, sys.argv[1]); from dotkit.core import ui\n"
           "try:\n    print(ui.confirm('Go?', False))\nexcept ui.Exit as e:\n    print(e.text)\n")


class ConfirmTests(unittest.TestCase):
    def ask(self, typed):
        argv = [sys.executable, "-I", "-B", "-c", CONFIRM, str(LIB)]
        return harness.run_tty(argv, {**os.environ, "NO_COLOR": "1"}, LIB, typed)

    def test_answers(self):
        cases = [("y\n", "True"), ("Y\n", "True"), ("  y \t\n", "True"), ("yes\n", "False"), ("n\n", "False"),
                 ("\n", "False"), ("\x04", "False")]
        for typed, want in cases:
            with self.subTest(typed=typed):
                code, out, terminal = self.ask(typed)
                self.assertEqual((code, out.strip()), (0, want))
                self.assertEqual(terminal, "Go? [y/N] ")

    def test_colors(self):
        """On a terminal with colors: ? and the question in bold, [y/N] dim (C9)."""
        argv = [sys.executable, "-I", "-B", "-c", CONFIRM, str(LIB)]
        env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "DOTKIT_NO_COLOR")}
        code, out, terminal = harness.run_tty(argv, {**env, "TERM": "xterm"}, LIB, "y\n")
        self.assertEqual((code, out.strip()), (0, "True"))
        self.assertEqual(terminal, "\033[1;33m?\033[0m \033[1mGo?\033[0m \033[2m[y/N]\033[0m ")

    def test_yes_does_not_ask(self):
        self.assertTrue(ui.confirm("Go?", True))

    def test_without_a_terminal(self):
        r = subprocess.run([sys.executable, "-I", "-B", "-c", CONFIRM, str(LIB)], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "dotkit: confirmation needs a terminal. Use -y to continue without one.")


class ProcTests(unittest.TestCase):
    def test_capture_and_streams(self):
        r = proc.run(["sh", "-c", "echo out; echo err >&2; exit 3"], stdout=proc.CAPTURE, stderr=proc.CAPTURE)
        self.assertEqual((r.code, r.out, r.err, r.ok), (3, "out\n", "err\n", False))
        r = proc.run(["sh", "-c", "echo out; echo err >&2"], stdout=proc.CAPTURE, stderr=proc.STDOUT)
        self.assertEqual(r.out, "out\nerr\n")
        r = proc.run(["sh", "-c", "echo out; echo err >&2"], stdout=proc.DEVNULL, stderr=proc.DEVNULL)
        self.assertEqual((r.code, r.out, r.err), (0, "", ""))

    def test_value_is_like_command_substitution(self):
        self.assertEqual(proc.run(["printf", "a\\n\\nb\\n\\n"], stdout=proc.CAPTURE).value, "a\n\nb")

    def test_stdin(self):
        self.assertEqual(proc.run(["cat"], stdin="text ✓\n", stdout=proc.CAPTURE).out, "text ✓\n")
        self.assertEqual(proc.run(["cat"], stdin=proc.DEVNULL, stdout=proc.CAPTURE).out, "")

    def test_cwd_and_env(self):
        d = fake.tempdir().resolve()
        self.addCleanup(fake.remove, d)
        r = proc.run(["sh", "-c", 'pwd; echo "$DK_TEST"'], cwd=d, env={"DK_TEST": "x"}, stdout=proc.CAPTURE)
        self.assertEqual(r.out, f"{d}\nx\n")

    def test_missing_command(self):
        r = proc.run(["dkt-no-such-command"], stderr=proc.DEVNULL)
        self.assertEqual(r.code, 127)

    def test_filter_against_awk(self):
        if not os.access(AWK, os.X_OK):
            self.skipTest("no /usr/bin/awk")
        want = subprocess.run([AWK, LOCK_FILTER], input=NIX_STDERR, capture_output=True, text=True).stdout
        script = ("import sys; sys.path.insert(0, sys.argv[1]); from dotkit.core import nix, proc\n"
                  "sys.exit(proc.run(['sh', '-c', 'cat >&2; echo out'], stdin=sys.argv[2],"
                  " err_filter=nix.LockWarning()).code)\n")
        r = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(LIB), NIX_STDERR], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "out\n", want))

    def test_interrupt_waits_for_the_command(self):
        """Ctrl-C reaches dotkit and the command: run() waits until the command ends, then raises."""
        script = ("import os, signal, sys, threading, time; sys.path.insert(0, sys.argv[1])\n"
                  "from dotkit.core import proc\n"
                  "threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT)).start()\n"
                  "t = time.time()\n"
                  "try:\n"
                  "    proc.run(['sh', '-c', 'sleep 1; echo ended'])\n"
                  "except KeyboardInterrupt:\n"
                  "    print('interrupted after', round(time.time() - t))\n")
        r = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(LIB)], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.stdout, "ended\ninterrupted after 1\n", r.stderr)


class NixTests(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir().resolve()
        self.addCleanup(fake.remove, self.dir)
        (self.dir / "repo").mkdir()
        (self.dir / "checkout").mkdir()

    def nix(self, rules, dotkit_dir=False):
        fakes = harness.install_fakes(self.dir, rules)
        patcher = mock.patch.dict(os.environ, fakes)
        patcher.start()
        self.addCleanup(patcher.stop)
        e = env.Env(repo=self.dir / "repo", dotkit=self.dir, home=self.dir,
                    dotkit_dir=self.dir / "checkout" if dotkit_dir else None)
        return nix.Nix(e)

    def calls(self):
        return [[c["tool"], *c["argv"]] for c in harness.fake_calls(self.dir)]

    def test_build(self):
        n = self.nix([{"tool": "nix", "re": "^build", "actions": [{"link_after": "--out-link", "to": "/sys"}]}])
        self.assertEqual(n.build_system(), 0)
        self.assertEqual(self.calls(), [["nix", "build", f"path:{self.dir}/repo/nix#darwinConfigurations.mac.system",
                                         "--out-link", f"{self.dir}/repo/.build/system"]])

    def test_build_with_dotkit_dir(self):
        n = self.nix([{"tool": "nix", "re": "drvPath", "stdout": "/nix/store/x.drv\n"},
                      {"tool": "nix", "re": "^build"}], dotkit_dir=True)
        self.assertEqual(n.build_system(), 0)
        self.assertEqual(self.calls()[1], ["nix", "build", "/nix/store/x.drv^*", "--out-link",
                                           f"{self.dir}/repo/.build/system"])
        self.assertEqual(self.calls()[0][-7:], n.env.override)

    def test_build_stops_when_the_evaluation_fails(self):
        n = self.nix([{"tool": "nix", "re": "drvPath", "exit": 4}], dotkit_dir=True)
        with mock.patch.object(ui, "raw_err"):
            self.assertEqual(n.build_system(), 4)
        self.assertEqual(len(self.calls()), 1)

    def test_switch_stops_at_the_first_failure(self):
        """A failed nix-env --set stops the switch."""
        link = [{"tool": "nix", "re": "^build", "actions": [{"link_after": "--out-link", "to": "/sys"}]}]
        n = self.nix(link + [{"tool": "sudo", "re": "nix-env", "exit": 5}, {"tool": "sudo", "re": ""}])
        self.assertEqual(n.switch(), 5)
        self.assertEqual(self.calls()[1:], [["sudo", "-H", "/usr/bin/env", "NIX_REMOTE=daemon",
                                             "/sys/sw/bin/nix-env", "-p", "/nix/var/nix/profiles/system", "--set",
                                             "/sys"]])

    def test_switch(self):
        link = [{"tool": "nix", "re": "^build", "actions": [{"link_after": "--out-link", "to": "/sys"}]}]
        n = self.nix(link + [{"tool": "sudo", "re": ""}])
        self.assertEqual(n.switch(), 0)
        self.assertEqual(self.calls()[2], ["sudo", "/sys/sw/bin/darwin-rebuild", "activate"])

    def test_switch_without_a_build(self):
        n = self.nix([{"tool": "nix", "re": "^build", "exit": 1}])
        with mock.patch.object(ui, "raw_err"):
            self.assertEqual(n.switch(), 1)
        self.assertEqual(len(self.calls()), 1)

    def test_eval_raw(self):
        n = self.nix([{"tool": "nix", "re": "brewfile", "stdout": 'brew "jq"\n'}])
        r = n.brewfile()
        self.assertEqual((r.code, r.out), (0, 'brew "jq"\n'))
        self.assertEqual(self.calls(), [["nix", "eval", "--raw",
                                         f"path:{self.dir}/repo/nix#darwinConfigurations.mac.config.homebrew.brewfile"]])


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir().resolve()
        self.addCleanup(fake.remove, self.dir)

    def test_restore_through_a_link(self):
        real, link, missing = self.dir / "real", self.dir / "link", self.dir / "missing"
        real.write_text("old")
        link.symlink_to(real)
        b = backup.Backup([link, missing])
        real.write_text("new")
        missing.write_text("made")
        copies = b.dir
        b.restore()
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_text(), "old")
        self.assertEqual(missing.read_text(), "made", "a file that did not exist is not restored")
        self.assertFalse(copies.exists())
        b.restore()  # a second time does nothing

    def test_discard(self):
        f = self.dir / "f"
        f.write_text("old")
        b = backup.Backup([f])
        f.write_text("new")
        copies = b.dir
        b.discard()
        self.assertEqual(f.read_text(), "new")
        self.assertFalse(copies.exists())

    def test_signals_raise_stopped(self):
        with self.assertRaises(backup.Stopped) as c:
            with backup.on_signals():
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(1)
        self.assertEqual(c.exception.signum, signal.SIGTERM)
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)


if __name__ == "__main__":
    unittest.main()
