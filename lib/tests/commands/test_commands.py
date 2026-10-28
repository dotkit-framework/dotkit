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
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import argparse

from dotkit import cli
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
            self.assertEqual(recovery.history(self.env), 0)
        # The output of nix goes to the real stdout, not to the buffer of this test.
        self.assertEqual(self.text()[0], "system-41-link -> system-42-link\n")
        self.assertEqual(self.calls(), [["nix", "store", "diff-closures", f"{d}/system-41-link",
                                         f"{d}/system-42-link"]])

    def test_no_previous_generation(self):
        d = self.profiles("system-1-link")
        (d / "system").symlink_to("system-1-link")
        with mock.patch.object(recovery, "PROFILES", str(d)), self.assertRaises(ui.Exit) as c:
            recovery.history(self.env)
        self.assertEqual(c.exception.text, "dotkit: there is no previous generation.")

    def test_no_system_link(self):
        d = self.profiles()
        with mock.patch.object(recovery, "PROFILES", str(d)):
            with self.assertRaises(ui.Exit) as c:
                recovery.history(self.env)
        self.assertIn("is not a link to a generation", c.exception.text)


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
        self.assertEqual(cli.plan_path("p.txt"), f"{d}/p.txt")
        self.assertEqual(cli.plan_path("sub/p.txt"), f"{d}/sub/p.txt")
        self.assertEqual(cli.plan_path("sub/"), f"{d}/sub")
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "'nope/p.txt' is not in a directory that exists"):
            cli.plan_path("nope/p.txt")


if __name__ == "__main__":
    unittest.main()


LIB = Path(__file__).resolve().parents[2]


class ProcessTests(unittest.TestCase):
    """The defects of the first review: they need a real dotkit process, a real child, and real signals."""

    def setUp(self):
        self.dir = fake.tempdir().resolve()
        self.addCleanup(fake.remove, self.dir)
        for rel, text in {**harness.BASE, **harness.REPO}.items():
            p = self.dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        (self.dir / "home").mkdir()
        (self.dir / "bin").mkdir()
        self.lock = self.dir / "repo/nix/flake.lock"
        self.before = self.lock.read_text()

    def tool(self, name, script):
        p = self.dir / "bin" / name
        p.write_text("#!/bin/sh\n" + script)
        p.chmod(0o755)

    def start(self, *args, **kw):
        env = {"HOME": str(self.dir / "home"), "DOTFILES": str(self.dir / "repo"), "LC_ALL": "en_US.UTF-8",
               "PATH": f"{self.dir / 'bin'}:/usr/bin:/bin"}
        argv = [sys.executable, "-I", "-B", str(LIB / "run.py"), "dotkit", *args]
        return subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, **kw)

    def new_lock(self):
        return json.dumps(harness_lock(1761000000, "d" * 14))

    def test_sigterm_waits_for_the_child_before_the_restore(self):
        """A child that writes the lock file late must not change it after the restore."""
        self.tool("nix", f"echo started > {self.dir}/started\nsleep 1\necho '{self.new_lock()}' > {self.lock}\n")
        p = self.start("upgrade", "-n", "nix", stdout=subprocess.DEVNULL)
        _wait_for(self.dir / "started")
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=30)
        time.sleep(1.5)
        self.assertEqual(p.returncode, 128 + signal.SIGTERM)
        self.assertEqual(self.lock.read_text(), self.before)

    def test_closed_pipe_restores_the_lock(self):
        """dotkit upgrade -n | head: the reader goes away before the plan is written."""
        self.tool("nix", f"echo '{self.new_lock()}' > {self.lock}\n")
        r, w = os.pipe()
        os.close(r)
        p = self.start("upgrade", "-n", "nix", stdout=w)
        os.close(w)
        _, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 128 + signal.SIGPIPE, err)
        self.assertNotIn(b"Traceback", err)
        self.assertEqual(self.lock.read_text(), self.before)

    def test_messages_instead_of_tracebacks(self):
        cases = [
            ("repo/nix/flake.lock", "<<<<<<< HEAD\n", ["__complete", "inputs"], "flake.lock is not valid JSON"),
            ("repo/nix/flake.lock", "[]\n", ["upgrade", "frob"], "flake.lock is not a JSON object."),
        ]
        for rel, text, args, want in cases:
            with self.subTest(args=args):
                (self.dir / rel).write_text(text)
                p = self.start(*args, stdout=subprocess.DEVNULL)
                _, err = p.communicate(timeout=30)
                self.assertEqual(p.returncode, 1)
                self.assertIn(want, err.decode())
                self.assertNotIn("Traceback", err.decode())
        packages = self.dir / "repo/nix/packages.txt"
        packages.chmod(0)
        self.addCleanup(packages.chmod, 0o644)
        p = self.start("list", "nix", stdout=subprocess.DEVNULL)
        _, err = p.communicate(timeout=30)
        self.assertEqual((p.returncode, err.decode()),
                         (1, f"dotkit: [Errno 13] Permission denied: '{packages}'\n"))

    def test_brew_outdated_without_a_json_object(self):
        self.tool("brew", 'case "$1" in outdated) echo "[]";; esac\n')
        p = self.start("upgrade", "-n", "brew", stdout=subprocess.DEVNULL)
        _, err = p.communicate(timeout=30)
        self.assertEqual((p.returncode, err.decode()), (1, "Checking Homebrew...\n"
                                                           "dotkit: brew outdated --json=v2 did not print a JSON object.\n"))


def harness_lock(modified, rev):
    return {"nodes": {"root": {"inputs": {"dotkit": "dotkit"}},
                      "dotkit": {"locked": {"lastModified": modified, "rev": rev}}}, "root": "root", "version": 7}


def _wait_for(path, timeout=30):
    end = time.time() + timeout
    while not path.exists():
        if time.time() > end:
            raise TimeoutError(path)
        time.sleep(0.05)


class CompletionImportTests(unittest.TestCase):
    """The completion runs on each Tab: it must not load the modules of the other commands (CLI-PLAN.md, 5.4)."""

    HEAVY = ("questionary", "prompt_toolkit", "wcwidth", "concurrent.futures", "dotkit.pkg.lookups",
             "dotkit.pkg.resolve", "dotkit.pkg.picker", "dotkit.migrate", "argparse")

    def test_complete_loads_only_the_readers(self):
        d = fake.tempdir().resolve()
        self.addCleanup(fake.remove, d)
        for rel, text in {**harness.BASE, **harness.REPO}.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        script = ("import os, sys\nsys.path.insert(0, sys.argv[1])\nwhat = sys.argv[2]\n"
                  "from dotkit.__main__ import main\n"
                  "code = main(['__complete', what])\n"
                  f"heavy = {self.HEAVY!r}\n"
                  "print(code, sorted(m for m in sys.modules if m.startswith(heavy)))\n")
        for what in ("tools-all", "inputs"):
            with self.subTest(what=what):
                r = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(LIB), what],
                                   env={"DOTFILES": str(d / "repo"), "HOME": str(d)}, capture_output=True, text=True,
                                   timeout=60)
                self.assertEqual(r.stdout.splitlines()[-1], "0 []", r.stderr)
