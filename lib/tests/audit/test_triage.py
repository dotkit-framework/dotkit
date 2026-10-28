"""What the report lists (BEHAVIOR.md, Au16): triage.py, the fix check against the upgrades of the sources,
the KEV catalog, and the version that an upgrade installs per source."""

from __future__ import annotations

import datetime
import json
import os
import types
import unittest
from unittest import mock

from dotkit.audit import kev, match, sources, triage
from dotkit.audit.model import FIX_AVAILABLE, FIX_NONE, Advisory, Finding, Item, package_query
from dotkit.audit.sources import actions, brew, mise
from dotkit.core import proc
from tests import fake


def finding(name="jq", source="nix", severity="HIGH", rid="CVE-1", **kw) -> Finding:
    return Finding(Item(source, name, "1.0", [package_query("npm", name, "1.0")]),
                   Advisory(rid, [], "", severity, []), **kw)


class SelectTests(unittest.TestCase):
    def test_table(self):
        mal = Finding(Item("packages", "node > x", "1"), Advisory("MAL-1", [], "", "CRITICAL", [], True))
        kev_dep = finding("node > y", "packages", "LOW", exploited=True)
        fixable = finding("jq", fix=FIX_AVAILABLE)
        unknown = finding("bat")
        no_fix = finding("fzf", severity="LOW", fix=FIX_NONE)
        dep = finding("mise > zip", "binaries", "CRITICAL")
        low = finding("tig", severity="LOW", fix=FIX_AVAILABLE)
        all_findings = [mal, kev_dep, fixable, unknown, no_fix, dep, low]
        v = triage.select(all_findings)
        self.assertEqual([f.item.name for f in v.findings], ["node > x", "node > y", "jq", "bat", "tig"])
        self.assertEqual(([f.item.name for f in v.no_fix], [f.item.name for f in v.dependencies], v.below),
                         (["fzf"], ["mise > zip"], []))
        v = triage.select(all_findings, min_severity="high")
        # Malicious and known exploited always; the rest below high is counted.
        self.assertEqual([f.item.name for f in v.findings], ["node > x", "node > y", "jq", "bat"])
        self.assertEqual([f.item.name for f in v.below], ["fzf", "tig"])
        self.assertEqual(len(triage.select(all_findings, all_=True).findings), 7)

    def test_rank(self):
        self.assertEqual([triage.rank(f) for f in (
            Finding(Item("nix", "x", "1"), Advisory("MAL-1", [], "", "CRITICAL", [], True)),
            finding(severity="LOW", exploited=True), finding(severity="CRITICAL"), finding(severity=""))],
            [-2, -1, 0, 4])

    def test_one_per_key_keeps_the_fix(self):
        a = finding("node", "mise", fix=FIX_AVAILABLE, upgrade="22.2.0")
        b = finding("node", "mise")
        b.item.version = "20.9.0"
        out = triage.one_per_key([a, b, finding("jq")])
        self.assertEqual([(f.item.name, f.item.version, f.fix, f.upgrade) for f in out],
                         [("node", "1.0, 20.9.0", FIX_AVAILABLE, "22.2.0"), ("jq", "1.0", "", "")])


class CheckFixesTests(unittest.TestCase):
    def test_upgrades(self):
        """mise knows the upgrade of two tools: one without the advisory, one with it; the third tool has no
        known upgrade. A dependency and a finding that its source checked (brew) are not asked about."""
        targets = {"jq": Item("mise", "jq", "2.0", [package_query("npm", "jq", "2.0")]),
                   "fzf": Item("mise", "fzf", "1.0", [package_query("npm", "fzf", "1.0")])}
        module = types.SimpleNamespace(upgrades=lambda ctx, items: [targets.get(i.name) for i in items])
        fixed, same, unknown = finding("jq", "mise"), finding("fzf", "mise"), finding("bat", "mise")
        dep = finding("mise > zip", "binaries")
        checked = finding("wget", "brew", fix=FIX_NONE)

        def ask(items):
            # The upgrade of fzf still has CVE-1; the one of jq does not.
            r = match.Result()
            r.findings = [Finding(i, Advisory("CVE-1", [], "", "", [])) for i in items if i.name == "fzf"]
            return r
        with mock.patch.dict(sources.MODULES, {"mise": module}):
            self.assertTrue(triage.check_fixes(None, [fixed, same, unknown, dep, checked], ask))
        self.assertEqual([(f.fix, f.upgrade) for f in (fixed, same, unknown, dep, checked)],
                         [(FIX_AVAILABLE, "2.0"), (FIX_NONE, "1.0"), ("", ""), ("", ""), (FIX_NONE, "")])
        self.assertFalse(triage.check_fixes(None, [dep], ask))


class KevTests(unittest.TestCase):
    CATALOG = json.dumps({"vulnerabilities": [{"cveID": "CVE-2024-1"}, {"cveID": "CVE-2024-2"}]})

    def setUp(self):
        self.dir = fake.tempdir()
        self.addCleanup(fake.remove, self.dir)

    def download(self, text):
        def run(argv, **kw):
            self.calls.append(argv)
            if text is not None:
                with open(argv[argv.index("-o") + 1], "w") as f:
                    f.write(text)
            return proc.Result(0 if text is not None else 6)
        return run

    def test_cache_for_a_day(self):
        self.calls = []
        with mock.patch.object(kev.proc, "run", self.download(self.CATALOG)):
            self.assertEqual(kev.load(self.dir), {"CVE-2024-1", "CVE-2024-2"})
            self.assertEqual(kev.load(self.dir), {"CVE-2024-1", "CVE-2024-2"})
        self.assertEqual(len(self.calls), 1)
        old = (self.dir / "kev.json").stat().st_mtime - kev.MAX_AGE - 1
        os.utime(self.dir / "kev.json", (old, old))
        # An old catalog stays when the download fails.
        with mock.patch.object(kev.proc, "run", self.download(None)):
            self.assertEqual(kev.load(self.dir), {"CVE-2024-1", "CVE-2024-2"})

    def test_no_catalog(self):
        self.calls = []
        with mock.patch.object(kev.proc, "run", self.download("not json")):
            self.assertIsNone(kev.load(self.dir))

    def test_mark(self):
        f = finding(rid="GHSA-1")
        f.advisory.aliases = ["CVE-2024-1"]
        triage.mark_exploited([f], frozenset({"CVE-2024-1"}))
        self.assertTrue(f.exploited)


class UpgradeVersionTests(unittest.TestCase):
    def test_brew_fix_status(self):
        rows = [("1.25.0", "1.25.0", ["1.26.0"], FIX_NONE), ("1.25.0", "1.26.1", ["1.26.0"], FIX_AVAILABLE),
                ("1.25.0", "1.25.1", ["1.26.0"], FIX_NONE), ("1.25.0", "1.26.0", [], FIX_AVAILABLE),
                ("1.25.0", "", [], "")]
        for installed, stable, fixed, want in rows:
            with self.subTest(stable=stable, fixed=fixed):
                self.assertEqual(brew.fix_status(installed, stable, fixed), want)

    def test_newest_tag(self):
        refs = ("a\trefs/tags/v45.0.7\nb\trefs/tags/v46.0.1\nc\trefs/tags/v46.0.1^{}\nd\trefs/tags/v47\n"
                "e\trefs/tags/v46.1.0-rc.1\nf\trefs/tags/v9.3.0\n")
        self.assertEqual(actions.newest_tag(refs), "v46.0.1")
        self.assertEqual(actions.newest_tag(""), "")

    def test_mise_latest_args(self):
        rows = [("node", "24", "24.1.0", "7d", ["latest", "--minimum-release-age", "7d", "node@24"]),
                ("node", ["22", "20"], "20.9.0", "", ["latest", "node@20"]),
                ("uv", {"version": "latest", "minimum_release_age": "1d"}, "0.13.6", "7d",
                 ["latest", "--minimum-release-age", "1d", "uv"]),
                ("node", "lts", "24.1.0", "", ["latest", "node@lts"])]
        for key, value, version, age, want in rows:
            with self.subTest(value=value):
                self.assertEqual(mise.latest_args(key, value, version, age), want)


if __name__ == "__main__":
    unittest.main()
