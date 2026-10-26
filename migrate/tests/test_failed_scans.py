"""A migrator whose scan fails: setup and `dotkit migrate` go on without it.

For each migrator of MIGRATORS that has facts of its own, its scan raises:
the Session has one Failure, setup has no page of it and the problem in the
report, and `dotkit migrate` adds what the other sources found and says what
it could not check. tests/test_isolation.py covers every migrator and every
phase.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from dotkit_migrate import __main__ as cli
from dotkit_migrate import registry
from dotkit_migrate.migrators import Migrator
from tests import fake
from tests.test_session import used_mac

LISTS = ("nix/brews.txt", "nix/casks.txt", "nix/taps.txt", "nix/mas.txt", "config/mise.toml")


def sources():
    """The migrators with facts of their own: those that override Migrator.scan."""
    return [m for m in registry.MIGRATORS if type(m).scan is not Migrator.scan]


class FailedScanTests(unittest.TestCase):
    def broken(self, m):
        """Its scan raises."""
        return mock.patch.object(type(m), "scan", side_effect=RuntimeError("boom"))

    def mac(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        runner = used_mac(h)
        # A crate that the base does not have, so that `dotkit migrate --all` adds it.
        h.json(".cargo/.crates2.json", {"installs": {"cargo-watch 8.5.2 (registry+https://x)": {}}})
        return h, runner

    def test_setup(self):
        self.assertGreaterEqual(len(sources()), 5)
        for m in sources():
            h, runner = self.mac()
            full = fake.session(h, runner)
            own = {p.key for p in m.pages(full.scan())}
            with self.subTest(key=m.key), self.broken(m):
                p = fake.session(h, runner, strict=False)
                facts = p.scan()
                self.assertEqual(facts.warnings, [])
                self.assertEqual([(f.migrator, f.phase, f.error) for f in p.failures],
                                 [(m.key, "scan", "RuntimeError: boom")])
                self.assertIsNone(facts.get(m.key))
                keys = [page.key for page in p.pages()]
                self.assertFalse(own & set(keys), keys)
                self.assertTrue(set(keys) - own, "the other pages are there")
                result = p.build(p.defaults(str(h.home / ".dotfiles")))
                self.assertIn(f"- **{m.title}**: failed while scanning: `RuntimeError: boom`", result.report)

    def adopt(self, migrators=None):
        """`dotkit migrate --all -y` on used_mac: the exit code, stderr, and the lists after it."""
        h, runner = self.mac()
        repo = h.home / ".dotfiles"
        for rel in LISTS:
            h.write(f".dotfiles/{rel}", "")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(registry, "MIGRATORS", registry.MIGRATORS if migrators is None else migrators):
            code = cli.main(["adopt", "--repo", str(repo), "--all", "-y"],
                            make_context=lambda _home, _base: fake.context_of(h, runner))
        return code, err.getvalue(), {rel: (repo / rel).read_text() for rel in LISTS}

    def test_adopt(self):
        """dotkit migrate says what it could not check, and adds what the registry without it adds."""
        _, _, full = self.adopt()
        for m in sources():
            with self.subTest(key=m.key):
                with self.broken(m):
                    code, err, lists = self.adopt()
                self.assertEqual(code, 0, err)
                self.assertIn(f"Could not check: {m.key} (RuntimeError: boom); nothing of it was added.\n", err)
                _, _, rest = self.adopt(tuple(x for x in registry.MIGRATORS if x is not m))
                self.assertEqual(lists, rest)
                self.assertTrue(any(lists.values()), "the other sources add")
                if type(m).adopt is not Migrator.adopt:
                    self.assertNotEqual(lists, full, "it adds on used_mac")


if __name__ == "__main__":
    unittest.main()
