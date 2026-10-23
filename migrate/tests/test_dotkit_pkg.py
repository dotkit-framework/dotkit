"""`dotkit add mas:ID` of scripts/dotkit-pkg, with a fake mas and a fake nix, in the macOS Bash 3.2.

The sandbox of `nix flake check` has no scripts/, so these are skipped there;
CI runs them in the check job.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import fake

SCRIPT = fake.ROOT / "scripts/dotkit-pkg"
BASH = "/bin/bash"
JQ = shutil.which("jq")

MAS = """\
#!/bin/sh
[ "$1" = list ] && printf '%s\\n' '409183694  Keynote  (14.2)' '1234  Keynote  (1.0)' '55  Back\\slash  (1.0)'
"""
# Shows the list as the edit left it, then fails the build, so dotkit-pkg restores it.
NIX = """\
#!/bin/sh
cat "$DOTFILES/nix/mas.txt" >&2
exit 1
"""


@unittest.skipUnless(SCRIPT.is_file() and JQ and shutil.which("perl"), "no scripts/dotkit-pkg, jq, or perl")
class DotkitPkgTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="dotkit-migrate-test-"))
        self.addCleanup(shutil.rmtree, self.dir)
        self.repo = self.dir / "repo"
        (self.repo / "nix").mkdir(parents=True)
        (self.repo / "config").mkdir()
        for rel in ("nix/packages.txt", "nix/brews.txt", "nix/casks.txt", "config/mise.toml"):
            (self.repo / rel).write_text("", encoding="utf-8")
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        for name, text in (("mas", MAS), ("nix", NIX)):
            (self.bin / name).write_text(text, encoding="utf-8")
            (self.bin / name).chmod(0o755)

    def run_pkg(self, *args: str) -> subprocess.CompletedProcess:
        env = {"HOME": str(self.dir), "DOTFILES": str(self.repo), "LC_ALL": "en_US.UTF-8",
               "PATH": f"{self.bin}:{os.path.dirname(JQ)}:/usr/bin:/bin"}
        return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=env,
                              stdin=subprocess.DEVNULL, timeout=60)

    def mas_id_of(self, name: str, file: Path) -> subprocess.CompletedProcess:
        func = re.search(r"(?ms)^mas_id_of\(\) \{.*?^\}", SCRIPT.read_text(encoding="utf-8")).group(0)
        return subprocess.run([BASH, "-c", f'set -euo pipefail\n{func}\nmas_id_of "$1" "$2"\necho "status=$?"',
                               "_", name, str(file)], capture_output=True, text=True, timeout=60)

    def test_mas_id_of_reads_a_long_file_to_the_end(self):
        """The first line matches; awk still reads the rest, more than a pipe buffer."""
        f = self.dir / "mas.txt"
        f.write_text("7 Keynote\n" + "".join(f"{i} App {i}\n" for i in range(100, 8100)) + "8 Keynote\n",
                     encoding="utf-8")
        self.assertGreater(f.stat().st_size, 65536)
        out = self.mas_id_of("Keynote", f)
        self.assertEqual((out.returncode, out.stdout), (0, "7\nstatus=0\n"), out.stderr)

    def test_mas_id_of_a_name_with_a_backslash(self):
        f = self.dir / "mas.txt"
        f.write_text("# apps\n  5   Back\\slash  \n6 Backslash\n", encoding="utf-8")
        out = self.mas_id_of("Back\\slash", f)
        self.assertEqual((out.returncode, out.stdout), (0, "5\nstatus=0\n"), out.stderr)

    def test_add_a_name_that_is_taken(self):
        mas = self.repo / "nix/mas.txt"
        mas.write_text("# apps\n409183694 Keynote\n", encoding="utf-8")
        out = self.run_pkg("add", "-n", "mas:1234")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "mas:1234 -> nix/mas.txt (Keynote (1234))\n"
                                     "nix/mas.txt has another app named Keynote; added as 'Keynote (1234)'.\n")
        out = self.run_pkg("add", "-y", "mas:1234")
        self.assertIn("\n409183694 Keynote\n1234 Keynote (1234)\n", out.stderr, "the line that the edit wrote")
        self.assertIn("The Nix files are restored.", out.stderr)
        self.assertEqual(mas.read_text(encoding="utf-8"), "# apps\n409183694 Keynote\n")

    def test_add_two_apps_with_one_name(self):
        (self.repo / "nix/mas.txt").write_text("", encoding="utf-8")
        out = self.run_pkg("add", "-y", "mas:409183694", "mas:1234", "mas:55")
        self.assertIn("409183694 Keynote\n1234 Keynote (1234)\n55 Back\\slash\n", out.stderr)


if __name__ == "__main__":
    unittest.main()
