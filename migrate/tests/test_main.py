"""The command line, in a child process: the locale, the result file, and the stops."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHILD = Path(__file__).resolve().parent / "main_child.py"


def run(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "PYTHON")) and k != "LANG"}
    base["HOME"] = str(root / "home")
    base.update(env or {})
    return subprocess.run([sys.executable, str(CHILD), str(root), *args], capture_output=True, env=base,
                          stdin=subprocess.DEVNULL, timeout=120)


class MainTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="dotkit-migrate-main-"))
        self.home = self.root / "home"

    def test_a_locale_that_is_not_utf8(self):
        """The review case: LC_ALL=en_US.ISO8859-1 and an App Store app named Notes with an emoji."""
        env = {"LC_ALL": "en_US.ISO8859-1", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
        result = self.root / "result"
        out = run(self.root, "setup", "--yes", "--repo", str(self.home / ".dotfiles"), "--result", str(result),
                  env=env)
        self.assertEqual(out.returncode, 0, out.stderr.decode(errors="replace"))
        mas = (self.home / ".dotfiles/nix/mas.txt").read_text(encoding="utf-8")
        self.assertIn("\n1274495053 Notes \u270f\ufe0f\n", mas)
        self.assertEqual(result.read_text(encoding="utf-8").splitlines()[0], str(self.home / ".dotfiles"))
        out = run(self.root, "setup", "--yes", "--dry-run", "--repo", str(self.home / "dry"), env=env)
        self.assertEqual(out.returncode, 0, out.stderr.decode(errors="replace"))
        self.assertIn(b"# Migration from", out.stdout)
        self.assertFalse((self.home / "dry").exists())

    def test_scan_progress_does_not_claim_to_run_npm(self):
        out = run(self.root, "setup", "--yes", "--dry-run", "--repo", str(self.home / "dry"))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(b"runs brew and mas", out.stderr)
        self.assertNotIn(b"npm", out.stderr)

    def test_a_bad_repository_path_stops(self):
        out = run(self.root, "setup", "--yes", "--repo", str(self.home / 'dot"files'))
        self.assertEqual(out.returncode, 2)
        self.assertIn(b"Use a path without", out.stderr)

    def test_a_linked_config_directory_stops_before_anything_is_written(self):
        (self.root / "elsewhere").mkdir()
        self.home.mkdir()
        os.symlink(self.root / "elsewhere", self.home / ".config")
        out = run(self.root, "setup", "--yes", "--repo", str(self.home / ".dotfiles"))
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn(b"~/.config is a link", out.stderr)
        self.assertFalse((self.home / ".dotfiles").exists())

    def test_a_secret_found_at_write_time_stops_cleanly(self):
        out = run(self.root, "setup", "--yes", "--repo", str(self.home / ".dotfiles"),
                  env={"DOTKIT_TEST_FORCE_SECRET": "config/after-the-guard"})
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertEqual(out.stderr.decode().splitlines()[-1],
                         "dotkit setup: the new repository would have a secret in a file that Git tracks, or a "
                         "file that cannot be checked: config/after-the-guard (line 1: a GitHub token). "
                         "Nothing was written.")
        self.assertNotIn(b"Traceback", out.stderr)
        self.assertEqual(sorted(p.name for p in self.home.iterdir() if p.name.startswith(".dotfiles")), [])

    def test_adopt_stops_cleanly_on_a_secret(self):
        repo = self.home / ".dotfiles"
        (repo / "nix").mkdir(parents=True)
        (repo / "nix/brews.txt").write_text("", encoding="utf-8")
        out = run(self.root, "adopt", "--yes", "--repo", str(repo), "--declared", "{}",
                  env={"DOTKIT_TEST_FORCE_SECRET": "nix/brews.txt"})
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertEqual(out.stderr.decode().splitlines()[-1],
                         "dotkit migrate: the repository would have a secret in a file that Git tracks, or a file "
                         "that cannot be checked: nix/brews.txt (line 1: a GitHub token). Nothing was written.")
        self.assertNotIn(b"Traceback", out.stderr)
        self.assertEqual((repo / "nix/brews.txt").read_text(encoding="utf-8"), "")
        self.assertEqual(sorted(p.name for p in (repo / "nix").iterdir()), ["brews.txt"], "no temp file stays")

    def test_adopt(self):
        repo = self.home / ".dotfiles"
        (repo / "nix").mkdir(parents=True)
        (repo / "config").mkdir()
        (repo / "nix/brews.txt").write_text("", encoding="utf-8")
        out = run(self.root, "adopt", "--yes", "--repo", str(repo), "--declared", json.dumps({"taps": []}),
                  env={"LC_ALL": "en_US.ISO8859-1", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"})
        self.assertEqual(out.returncode, 0, out.stderr.decode(errors="replace"))
        self.assertIn("wget", (repo / "nix/brews.txt").read_text(encoding="utf-8"))
        self.assertIn(b"Notes ", out.stdout)


    def test_adopt_twice(self):
        """Two App Store apps with one name: both are added, and the second run has nothing to add."""
        repo = self.home / ".dotfiles"
        (repo / "config").mkdir(parents=True)
        (repo / "nix").mkdir()
        for rel in ("nix/brews.txt", "nix/casks.txt", "nix/taps.txt", "nix/mas.txt"):
            (repo / rel).write_text("", encoding="utf-8")
        env = {"DOTKIT_TEST_MAS_LIST": "409183694 Keynote (14.2)\n1234 Keynote (1.0)\n"}
        out = run(self.root, "adopt", "--yes", "--all", "--repo", str(repo), "--declared", "{}", env=env)
        self.assertEqual(out.returncode, 0, out.stderr.decode(errors="replace"))
        mas = (repo / "nix/mas.txt").read_text(encoding="utf-8")
        self.assertRegex(mas, r"\A# Added by dotkit migrate on .*\.\n409183694 Keynote\n1234 Keynote \(1234\)\n\Z")
        out = run(self.root, "adopt", "--yes", "--all", "--repo", str(repo), "--declared", "{}", env=env)
        self.assertEqual(out.returncode, 0, out.stderr.decode(errors="replace"))
        self.assertEqual(out.stdout, b"The lists have everything that this Mac has.\n")


if __name__ == "__main__":
    unittest.main()
