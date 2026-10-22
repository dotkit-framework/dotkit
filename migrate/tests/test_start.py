"""run.py, the start file of dotkit-migrate, and the Python 3.9 floor of the package."""

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MIGRATE = Path(__file__).resolve().parents[1]


class StartTests(unittest.TestCase):
    def test_isolated_mode(self):
        """dotkit-migrate runs run.py with -I -B: a dotkit_migrate on PYTHONPATH must not replace the package."""
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "dotkit_migrate"
            fake.mkdir()
            (fake / "__init__.py").write_text('raise SystemExit("the package from PYTHONPATH")\n')
            env = dict(os.environ, PYTHONPATH=tmp)
            r = subprocess.run([sys.executable, "-I", "-B", str(MIGRATE / "run.py"), "setup", "--help"], cwd=tmp,
                               env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage: dotkit-migrate setup", r.stdout)

    def test_python_39_syntax(self):
        """The macOS Python is 3.9: each file must parse with the grammar of Python 3.9."""
        files = sorted(MIGRATE.rglob("*.py"))
        self.assertGreater(len(files), 20)
        for f in files:
            with self.subTest(f=str(f.relative_to(MIGRATE))):
                ast.parse(f.read_text(encoding="utf-8"), str(f), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
