"""`dotkit add mas:ID` of scripts/dotkit-pkg, with a fake mas and a fake nix, in the macOS Bash 3.2.

The sandbox of `nix flake check` has no scripts/, so these are skipped there;
CI runs them in the check job.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests import fake

SCRIPT = fake.ROOT / "scripts/dotkit-pkg"
BASH = "/bin/bash"
JQ = shutil.which("jq")

# mas 7.0.0, from real output: `mas list` (the four real lines, then two made-up
# lines in the same columns), `mas info --json` (JSON Lines; one real line,
# without its description, release notes, and icon and screenshot URLs), and
# `mas info` (aligned labels with U+2581 fill). An unknown ID fails as the real
# mas does. ID 2 is made up: a JSON object without a name.
MAS = r"""#!/bin/sh
if [ "$1" = list ]; then
  cat <<'EOF'
 361285480  Keynote         (15.4)
 409203825  Numbers         (14.5)
 409201541  Pages           (14.5)
1289583905  Pixelmator Pro  (3.8)
      1234  Keynote         (1.0)
        55  Back\slash      (1.0)
EOF
elif [ "$1 $2 $3" = "info --json 497799835" ]; then
  cat <<'EOF'
{"adamID":497799835,"appStorePageURL":"https://apps.apple.com/nl/app/xcode/id497799835?mt=12&uo=4","averageUserRating":2.723570000000000046469494918710552155971527099609375,"averageUserRatingForCurrentVersion":2.723570000000000046469494918710552155971527099609375,"bundleID":"com.apple.dt.Xcode","categories":["Ontwikkelaarstools"],"categoryIDs":["6026"],"censoredName":"Xcode","contentAdvisoryRating":"4+","contentRating":"4+","currency":"EUR","currentVersionReleaseDate":"2026-10-15T20:22:08Z","developerAppStorePageURL":"https://apps.apple.com/nl/developer/apple/id284417353?mt=12&uo=4","developerID":284417353,"developerName":"Apple","fileSizeBytes":"3083229147","formattedPrice":"Gratis","isVPPDeviceBasedLicensingEnabled":true,"kind":"mac-software","languageCodesISO2A":["EN"],"minimumOSVersion":"26.6","name":"Xcode","originalVersionReleaseDate":"2012-02-16T14:10:23Z","price":0.00,"primaryCategoryID":6026,"primaryCategoryName":"Developer Tools","sellerName":"Apple Distribution International","sellerURL":"http://developer.apple.com/xcode","userRatingCount":785,"userRatingCountForCurrentVersion":785,"version":"27.1","wrapperType":"software"}
EOF
elif [ "$1 $2" = "info 497799835" ]; then
  cat <<'EOF'
App ▁▁▁▁▁▁▁▁ Xcode
Version ▁▁▁▁ 27.1
Price ▁▁▁▁▁▁ Gratis
By ▁▁▁▁▁▁▁▁▁ Apple Distribution International
Released ▁▁▁ 2026-10-15
Minimum OS ▁ 26.6
Size ▁▁▁▁▁▁▁ 3,083 MB
From ▁▁▁▁▁▁▁ https://apps.apple.com/nl/app/xcode/id497799835?mt=12&uo=4
EOF
elif [ "$1 $2 $3" = "info --json 2" ]; then
  echo '{"adamID":2}'
else
  [ "$2" != --json ] || echo
  echo "Error: No apps found in the App Store for ADAM ID ${3:-$2}" >&2
  exit 1
fi
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
        self.dir = fake.tempdir()
        self.addCleanup(fake.remove, self.dir)
        self.tmp = self.dir / "tmp"  # the mktemp files of dotkit-pkg
        self.tmp.mkdir()
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
        env = {"HOME": str(self.dir), "TMPDIR": str(self.tmp), "DOTFILES": str(self.repo), "LC_ALL": "en_US.UTF-8",
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
        out = self.run_pkg("add", "-y", "mas:361285480", "mas:1234", "mas:55")
        self.assertIn("361285480 Keynote\n1234 Keynote (1234)\n55 Back\\slash\n", out.stderr)

    def test_add_an_installed_app_with_a_name_of_two_words(self):
        (self.repo / "nix/mas.txt").write_text("", encoding="utf-8")
        out = self.run_pkg("add", "-n", "mas:1289583905")
        self.assertEqual((out.returncode, out.stdout), (0, "mas:1289583905 -> nix/mas.txt (Pixelmator Pro)\n"),
                         out.stderr)

    def test_add_an_app_that_is_not_installed(self):
        """The name comes from `mas info --json`, not from the text of `mas info`."""
        (self.repo / "nix/mas.txt").write_text("", encoding="utf-8")
        out = self.run_pkg("add", "-n", "mas:497799835")
        self.assertEqual((out.returncode, out.stdout), (0, "mas:497799835 -> nix/mas.txt (Xcode)\n"), out.stderr)
        out = self.run_pkg("add", "-y", "mas:497799835")
        self.assertIn("\n497799835 Xcode\n", "\n" + out.stderr, "the line that the edit wrote")

    def test_add_an_app_whose_name_mas_does_not_give(self):
        mas = self.repo / "nix/mas.txt"
        mas.write_text("# apps\n", encoding="utf-8")
        out = self.run_pkg("add", "-y", "mas:2")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("mas:2: cannot read the name of App Store app 2 from mas. Add it to nix/mas.txt as `2 Name`.",
                      out.stderr)
        self.assertEqual(mas.read_text(encoding="utf-8"), "# apps\n")

    def test_add_an_unknown_id(self):
        mas = self.repo / "nix/mas.txt"
        mas.write_text("# apps\n", encoding="utf-8")
        out = self.run_pkg("add", "-y", "mas:1")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("mas:1: mas finds no App Store app with the ID 1.", out.stderr)
        self.assertEqual(mas.read_text(encoding="utf-8"), "# apps\n")


if __name__ == "__main__":
    unittest.main()
