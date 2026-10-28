"""The overlaps with the base: doctor (Ov8), which reads /run/current-system, and the limits of the diff (Ov4)."""

from __future__ import annotations

import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from dotkit.commands import overlap
from dotkit.core import mise_toml
from dotkit.core.env import Env
from dotkit.core.manifest import Manifest
from tests import fake
from tests.cli import fixtures as fx
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
        self.env = Env(repo=self.dir / "repo", dotkit=self.dir / "dotkit", home=self.dir / "home")
        # The state file in the home of the sandbox.
        environ = mock.patch.dict(overlap.os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        overlap.os.environ.pop("XDG_STATE_HOME", None)
        # The text without colors, also where stdout is a terminal (the sandbox of nix flake check).
        patcher = mock.patch.object(overlap.ui, "_no_color", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.system = self.dir / "system"

    def write(self, rel: str, text: str) -> Path:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def manifest(self, base: dict, links: dict) -> None:
        self.write("system/etc/dotkit/links.json", fx.manifest(base, links, root=str(self.dir)))

    def doctor(self) -> tuple:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(overlap.ui, "out", lambda text="": out.write(text + "\n")), \
                mock.patch.object(overlap.ui, "note", lambda text: err.write(text + "\n")):
            code = overlap.doctor(self.env, self.system)
        return code, out.getvalue(), err.getvalue()


class DoctorTests(Sandbox):
    def test_none(self):
        self.manifest({}, {})
        self.assertEqual(self.doctor(), (0, "Your dotfiles do not repeat or replace any part of the dotkit base.\n", ""))

    def test_lists_all_and_keeps_the_state(self):
        self.write("repo/nix/packages.txt", "fd\n")
        self.write("dotkit/config/npmrc", "a=1\n")
        self.write("repo/config/npmrc", "a=1\n")
        self.manifest({".npmrc": "dotkit/config/npmrc"}, {".npmrc": "repo/config/npmrc"})
        state = self.write("home/.local/state/dotkit/overlaps.json", json.dumps({"version": 1, "seen": {"nix:fd": ""}}))
        before = state.read_text()
        code, out, err = self.doctor()
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out.splitlines(), [
            "Your dotfiles repeat or replace these parts of the dotkit base:",
            "  nix/packages.txt  fd is in the dotkit base too. Remove yours: dotkit remove nix:fd",
            "  ~/.npmrc          your config/npmrc is the same as the base file. Give it back: dotkit own --release ~/.npmrc",
        ])
        self.assertEqual(state.read_text(), before)

    def test_without_manifest(self):
        self.write("repo/nix/packages.txt", "fd\n")
        code, out, err = self.doctor()
        self.assertEqual(code, 0)
        self.assertEqual(err, f"{self.system} has no link manifest, so the config files are not compared. "
                              "Run dotkit apply.\n")
        self.assertIn("fd is in the dotkit base too", out)


class AcknowledgeTests(Sandbox):
    """After dotkit own, the next report does not show the owned file as new (Ow9, Ov7)."""

    def test_acknowledge(self):
        from dotkit.commands.own import HEADER
        self.write("dotkit/config/herdr.toml", "# dotkit base. herdr reads one\n# config file.\n\na = 1\n")
        self.write("repo/config/herdr.toml", HEADER + "\na = 1\n")
        self.write("repo/nix/packages.txt", "fd\n")
        self.manifest({".config/herdr/config.toml": "dotkit/config/herdr.toml"},
                      {".config/herdr/config.toml": "repo/config/herdr.toml"})
        overlap.acknowledge(self.env, self.system, ["link:.config/herdr/config.toml"])
        err = io.StringIO()
        with mock.patch.object(overlap.ui, "note", lambda text: err.write(text + "\n")):
            overlap.report(self.env, self.system, dry_run=True)
        self.assertEqual(err.getvalue().splitlines(), [
            "dotkit: your dotfiles repeat or replace parts of the dotkit base:",
            "  nix/packages.txt  fd is in the dotkit base too. Remove yours: dotkit remove nix:fd",
            "And 1 known overlap with the dotkit base. dotkit doctor lists it.",
        ])

    def test_without_a_manifest(self):
        overlap.acknowledge(self.env, self.system, ["link:.x"])
        self.assertFalse((self.dir / "home/.local/state/dotkit/overlaps.json").exists())


class MovedTests(Sandbox):
    """The fingerprint of a directory that an apply moved aside (Ov5): its lstat, not its files."""

    def test_directory(self):
        d = self.dir / "home/.config/nvim.before-dotkit"
        (d / "deep/plugin").mkdir(parents=True)
        os.utime(d, ns=(1, 1_000))
        first = overlap._content(d)
        (d / "deep/plugin/x.lua").write_text("x")
        os.utime(d, ns=(1, 1_000))
        self.assertEqual(overlap._content(d), first)
        (d / "init.lua").write_text("y")
        os.utime(d, ns=(1, 2_000))
        self.assertNotEqual(overlap._content(d), first)


class CompareTests(Sandbox):
    def compare(self, a: str, b: str) -> overlap.Comparison:
        return overlap.compare(str(self.write("a", a)), str(self.write("b", b)), "a", "b")

    def test_same_without_trailing_spaces(self):
        self.assertEqual(self.compare("x\ny\n", "x  \ny\n\n\n").kind, "same")

    def test_the_headers_do_not_count(self):
        """An owned copy that only has the header of dotkit own is the same as the base file (Ov4)."""
        self.assertEqual(self.compare("# dotkit base. herdr reads one\n# config file.\n\na = 1\n",
                                      "# From the dotkit base (dotkit own). After a dotkit upgrade, dotkit doctor "
                                      "shows\n# how this file differs from the base file.\n\na = 1\n").kind, "same")
        self.assertEqual(self.compare("# mine\na = 1\n", "a = 1\n").kind, "diff")

    def test_similar_shows_the_diff(self):
        lines = [f"{i}\n" for i in range(20)]
        c = self.compare("".join(lines), "".join(lines[:5] + ["five\n"] + lines[6:]))
        self.assertEqual((c.kind, c.changed), ("diff", 2))
        self.assertEqual(c.detail[:2], ["--- a", "+++ b"])

    def test_at_most_40_changed_lines(self):
        base = "".join(f"{i}\n" for i in range(100))
        self.assertEqual(self.compare(base, base + "".join(f"new {i}\n" for i in range(40))).kind, "diff")
        self.assertEqual(self.compare(base, base + "".join(f"new {i}\n" for i in range(41))).kind, "different")

    def test_at_least_half_the_same(self):
        self.assertEqual(self.compare("a\nb\n", "a\nx\n").kind, "diff")
        c = self.compare("a\nb\nc\n", "a\nx\ny\nz\n")
        self.assertEqual(c.kind, "different")
        self.assertTrue(c.detail[0].startswith("29% of the lines are the same. Compare them: diff -u "))

    def test_not_text(self):
        a, b = self.write("a", ""), self.dir / "b"
        b.write_bytes(b"\xff\xfe")
        self.assertEqual(overlap.compare(str(a), str(b), "a", "b").kind, "unknown")
        self.assertEqual(overlap.compare(str(a), str(self.dir / "missing"), "a", "b").kind, "unknown")

    def test_a_change_makes_it_new(self):
        first = self.compare("a\n", "b\n").fingerprint
        self.assertEqual(self.compare("a\n", "b\n").fingerprint, first)
        self.assertNotEqual(self.compare("a\n", "c\n").fingerprint, first)


class ResolveTests(unittest.TestCase):
    """The arguments of dotkit own (Ow2): the paths that the scenarios cannot pass, and the names."""

    def test_resolve(self):
        from dotkit.commands import own
        m = Manifest.parse(json.loads(fx.manifest({".config/herdr/config.toml": "dotkit/config/herdr.toml",
                                                   ".config/herdr/git-status.sh": "dotkit/scripts/herdr/git-status.sh",
                                                   ".bunfig.toml": "dotkit/config/bunfig.toml",
                                                   ".gemrc": "dotkit/config/gemrc"}, {}, root="/d")))
        home = Path("/Users/jane")
        self.assertEqual(own.resolve("/Users/jane/.gemrc", m, home), [".gemrc"])
        self.assertEqual(own.resolve("/Users/jane/x/../.gemrc", m, home), [".gemrc"])
        self.assertEqual(own.resolve("/etc/gemrc", m, home), ["/etc/gemrc"])
        self.assertEqual(own.resolve("~/.config/x", m, home), [".config/x"])
        self.assertEqual(own.resolve("herdr", m, home), [".config/herdr/config.toml"])
        self.assertEqual(own.resolve("bunfig", m, home), [".bunfig.toml"])
        self.assertEqual(own.resolve("bunfig.toml", m, home), [".bunfig.toml"])
        self.assertEqual(own.resolve("git-status", m, home), [])


class TomlValueTests(unittest.TestCase):
    def test_values(self):
        self.assertEqual(mise_toml.toml_value("lts"), '"lts"')
        self.assertEqual(mise_toml.toml_value(["22", "20"]), '["22", "20"]')
        self.assertEqual(mise_toml.toml_value({"version": "latest", "minimum_release_age": "1d"}),
                         '{ version = "latest", minimum_release_age = "1d" }')
        self.assertEqual(mise_toml.toml_value({"a.b": True, "n": 1}), '{ "a.b" = true, n = 1 }')
        self.assertEqual(mise_toml.toml_value({}), "{}")
        self.assertEqual(mise_toml.toml_value(1.5), "1.5")
        self.assertEqual(mise_toml.toml_value(__import__("datetime").date(2026, 1, 2)), '"2026-01-02"')


if __name__ == "__main__":
    unittest.main()
