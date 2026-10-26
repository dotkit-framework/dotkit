"""fake.check_temp, the last check of the suite (tests/__init__.py), in a temporary directory of its own."""

import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests import fake

# Makes a directory with its own pid in the name, a read-only part in it, and exits without any cleanup.
CHILD = """\
import os, sys, tempfile
d = tempfile.mkdtemp(dir=sys.argv[1], prefix=f"dotkit-migrate-child-{os.getpid()}-")
os.mkdir(os.path.join(d, "ro"))
open(os.path.join(d, "ro", "f"), "w").close()
os.chmod(os.path.join(d, "ro"), 0o555)
print(d, flush=True)
os._exit(0)
"""


class CheckTempTests(unittest.TestCase):
    def setUp(self):
        self.tmp = fake.tempdir("check")
        self.addCleanup(fake.remove, self.tmp)

    def check(self, before):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            removed, kept = fake.check_temp(before, made=[], tmp=str(self.tmp))
        return removed, kept, err.getvalue()

    def dead_pid(self) -> int:
        p = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True,
                           check=True, timeout=60)
        return int(p.stdout)

    def test_a_child_that_exits_without_cleanup_is_a_leak_and_is_removed(self):
        before = fake.temp_entries(str(self.tmp))
        out = subprocess.run([sys.executable, "-c", CHILD, str(self.tmp)], capture_output=True, text=True,
                             check=True, timeout=60)
        left = Path(out.stdout.strip())
        self.assertTrue(left.is_dir())
        removed, kept, err = self.check(before)
        self.assertEqual((removed, kept, err), ([left.name], [], ""))
        self.assertFalse(left.exists())

    def test_a_leak_of_this_process_is_removed(self):
        before = fake.temp_entries(str(self.tmp))
        mine = self.tmp / f"dotkit-migrate-test-{os.getpid()}-abc"
        mine.mkdir()
        self.assertEqual(self.check(before), ([mine.name], [], ""))
        self.assertFalse(mine.exists())

    def test_a_new_entry_of_a_live_process_is_another_run(self):
        alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(alive.wait)
        self.addCleanup(alive.kill)
        before = fake.temp_entries(str(self.tmp))
        theirs = self.tmp / f"dotkit-migrate-test-{alive.pid}-abc"
        theirs.mkdir()
        self.assertEqual(self.check(before), ([], [], ""))
        self.assertTrue(theirs.is_dir())

    def test_an_earlier_entry_with_a_dead_pid_gets_a_note_only(self):
        old = self.tmp / f"dotkit-migrate-test-{self.dead_pid()}-abc"
        old.mkdir()
        removed, kept, err = self.check(fake.temp_entries(str(self.tmp)))
        self.assertEqual((removed, kept), ([], []))
        self.assertIn(f"note: {old} is from an earlier run", err)
        self.assertTrue(old.is_dir())

    def test_a_new_entry_without_a_pid_is_counted_and_not_removed(self):
        before = fake.temp_entries(str(self.tmp))
        for name in ("dotkit-migrate-nopid", "dotkit-migrate-main-abc12345"):
            (self.tmp / name).mkdir()
        self.assertEqual(self.check(before), ([], ["dotkit-migrate-main-abc12345", "dotkit-migrate-nopid"], ""))
        self.assertTrue((self.tmp / "dotkit-migrate-nopid").is_dir())

    def test_a_directory_of_tempdir_that_is_left_is_removed(self):
        made = fake.tempdir("left")
        self.addCleanup(fake.remove, made)
        (made / "ro").mkdir()
        (made / "ro").chmod(0o555)
        removed, kept = fake.check_temp(fake.temp_entries(str(self.tmp)), made=[made], tmp=str(self.tmp))
        self.assertEqual((removed, kept), ([made.name], []))
        self.assertFalse(made.exists())


if __name__ == "__main__":
    unittest.main()
