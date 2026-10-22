"""The command line, in a child process: the locale, the result file, and the stops."""

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
        darwin = (self.home / ".dotfiles/nix/darwin.nix").read_text(encoding="utf-8")
        self.assertIn('"Notes \u270f\ufe0f" = 1274495053;', darwin)
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


if __name__ == "__main__":
    unittest.main()
