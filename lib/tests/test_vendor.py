"""dotkit/_vendor matches vendor.txt, and the vendored packages load from there on this Python."""

import ast
import re
import unittest
from pathlib import Path

import dotkit

VENDOR = Path(dotkit.VENDOR)


def pins():
    out = {}
    for line in (VENDOR / "vendor.txt").read_text().splitlines():
        m = re.match(r"([A-Za-z0-9_.-]+)==(\S+) --hash=sha256:[0-9a-f]{64}$", line)
        if m:
            out[m.group(1).replace("-", "_").lower()] = m.group(2)
    return out


class VendorTests(unittest.TestCase):
    def test_pins_match_the_unpacked_wheels(self):
        found = {}
        for d in VENDOR.glob("*.dist-info"):
            name, version = d.name[:-len(".dist-info")].rsplit("-", 1)
            found[name.lower()] = version
        self.assertEqual(found, pins())
        self.assertEqual(len(pins()), len((VENDOR / "vendor.txt").read_text().split("--hash")) - 1,
                         "each line of vendor.txt has a version and a hash")

    def test_packages_load_from_vendor(self):
        import prompt_toolkit
        import questionary
        import tomli
        import wcwidth
        for module in (prompt_toolkit, questionary, tomli, wcwidth):
            with self.subTest(module=module.__name__):
                self.assertEqual(Path(module.__file__).resolve().parents[1], VENDOR.resolve())
        self.assertEqual(prompt_toolkit.__version__, pins()["prompt_toolkit"])

    def test_python_39_grammar(self):
        for f in VENDOR.rglob("*.py"):
            ast.parse(f.read_text(encoding="utf-8"), str(f), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
