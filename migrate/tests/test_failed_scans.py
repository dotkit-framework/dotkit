"""A migrator whose scan fails: setup and `dotkit migrate` go on without it.

For each of the sources that adopt reads, its scan raises: the scan has one
warning, setup has no page of it and the warning in the report, and
`dotkit migrate` adds what the other sources found.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from dotkit_migrate import __main__ as cli
from dotkit_migrate import registry
from tests import fake
from tests.test_session import used_mac

# The page keys of each migrator, and what `dotkit migrate --all` adds from it on used_mac.
SOURCES = {
    "homebrew": (("formulae", "casks", "taps"), ("nix/brews.txt", "wget")),
    "appstore": (("mas",), ("nix/mas.txt", "Xcode")),
    "node_tools": (("npm",), ("config/mise.toml", "npm:typescript")),
    "python_tools": (("pipx",), ("config/mise.toml", "pipx:ruff")),
    "built_tools": (("cargo", "go"), ("config/mise.toml", "cargo:cargo-watch")),
}


class FailedScanTests(unittest.TestCase):
    def broken(self, key: str):
        """Its scan raises."""
        migrator = next(m for m in registry.MIGRATORS if m.key == key)
        return mock.patch.object(type(migrator), "scan", side_effect=RuntimeError("boom"))

    def mac(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        runner = used_mac(h)
        # A crate that the base does not have, so that `dotkit migrate --all` adds it.
        h.json(".cargo/.crates2.json", {"installs": {"cargo-watch 8.5.2 (registry+https://x)": {}}})
        return h, runner

    def test_setup(self):
        for key, (pages, _) in SOURCES.items():
            with self.subTest(key=key), self.broken(key):
                h, runner = self.mac()
                p = fake.session(h, runner)
                facts = p.scan()
                self.assertEqual(facts.warnings, [f"{key}: RuntimeError: boom"])
                self.assertIsNone(facts.get(key))
                keys = [page.key for page in p.pages()]
                self.assertFalse(set(pages) & set(keys), keys)
                self.assertIn("files", keys, "the other pages are there")
                result = p.build(p.defaults(str(h.home / ".dotfiles")))
                self.assertIn(f"- {key}: RuntimeError: boom\n", result.report)

    def test_adopt(self):
        for key in SOURCES:
            with self.subTest(key=key), self.broken(key):
                h, runner = self.mac()
                repo = h.home / ".dotfiles"
                for rel in ("nix/brews.txt", "nix/casks.txt", "nix/taps.txt", "nix/mas.txt", "config/mise.toml"):
                    h.write(f".dotfiles/{rel}", "")
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main(["adopt", "--repo", str(repo), "--all", "-y"],
                                    make_context=lambda _home, _base: fake.context_of(h, runner))
                self.assertEqual(code, 0, err.getvalue())
                for other, (_, (rel, marker)) in SOURCES.items():
                    text = (repo / rel).read_text()
                    if other == key:
                        self.assertNotIn(marker, text)
                    else:
                        self.assertIn(marker, text, other)


if __name__ == "__main__":
    unittest.main()
