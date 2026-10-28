"""dotkit own: a nix/ file that changes while the question waits (Ow9). The scenarios cannot change a file then."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from dotkit.commands import own
from dotkit.core import ui
from dotkit.core.env import Env
from dotkit.core.manifest import Manifest
from tests import fake
from tests.cli import fixtures as fx

HOME_NIX = '{\n  dotkit.links = {\n    ".config/starship.toml" = "config/my-starship.toml";\n  };\n}\n'


class ChangedFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir().resolve()
        self.addCleanup(fake.remove, self.dir)
        for rel, text in {"repo/nix/home.nix": HOME_NIX, "dotkit/config/herdr.toml": "a = 1\n",
                          "dotkit/config/starship.toml": "", "repo/config/my-starship.toml": ""}.items():
            p = self.dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        (self.dir / "home").mkdir()
        self.env = Env(repo=self.dir / "repo", dotkit=self.dir / "dotkit", home=self.dir / "home")
        data = fx.manifest({".config/herdr/config.toml": "dotkit/config/herdr.toml",
                            ".config/starship.toml": "dotkit/config/starship.toml"},
                           {".config/starship.toml": "repo/config/my-starship.toml"}, root=str(self.dir))
        self.manifest = Manifest.parse(json.loads(data))
        self.messages = []
        for name in ("out", "note"):
            patcher = mock.patch.object(ui, name, lambda text="": self.messages.append(text))
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_own(self, args, release, changed):
        home_nix = self.env.repo / "nix/home.nix"

        def confirm(question, yes):
            home_nix.write_text(changed)
            return True
        with mock.patch.object(Manifest, "evaluate", lambda env: self.manifest), \
                mock.patch.object(ui, "confirm", confirm), mock.patch.object(own.change.nix, "Nix") as nix:
            with self.assertRaises(ui.Exit) as e:
                own.own(self.env, args, release, False, False)
        nix.return_value.build_system.assert_not_called()
        self.assertEqual(e.exception.text, "dotkit: could not change the Nix files. They are restored.")
        # The edit of the user stays, and dotkit changed nothing.
        self.assertEqual(home_nix.read_text(), changed)
        self.assertTrue(any("changed after the plan" in m for m in self.messages), self.messages)

    def test_own(self):
        self.run_own(["herdr"], False, HOME_NIX.replace("{\n  dotkit", "{\n  # mine\n  dotkit"))
        self.assertFalse((self.env.repo / "config/herdr.toml").exists())

    def test_release(self):
        """Another line in the place of the planned one: no line is deleted."""
        self.run_own(["starship"], True,
                     HOME_NIX.replace('    ".config/starship.toml"', '    ".x" = "y";\n    ".config/starship.toml"'))


if __name__ == "__main__":
    unittest.main()
