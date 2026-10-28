"""The decisions of dotkit audit (BEHAVIOR.md, rows Au): the queries of each tool, the severity, the
ignore rules, and the findings. Tables: match.py runs no command."""

from __future__ import annotations

import datetime
import unittest

from dotkit.audit import match
from dotkit.audit.model import Advisory, Finding, Item, git_query, package_query


class SeverityTests(unittest.TestCase):
    def test_cvss3(self):
        rows = [
            ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
            ("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N", 6.1),
            ("CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N", 5.5),
            ("CVSS:3.0/AV:N/AC:H/PR:N/UI:N/S:U/C:N/I:N/A:L", 3.7),
            ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N", 0.0),
            ("CVSS:4.0/AV:L/AC:L/AT:P/PR:L/UI:N/VC:N/VI:N/VA:L/SC:N/SI:N/SA:N", None),
            ("CVSS:3.1/AV:X/AC:L", None),
        ]
        for vector, score in rows:
            with self.subTest(vector=vector):
                self.assertEqual(match.cvss3_score(vector), score)

    def test_severity(self):
        rows = [
            ({"database_specific": {"severity": "MODERATE"}}, "MEDIUM"),
            ({"database_specific": {"severity": "critical"}}, "CRITICAL"),
            ({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]}, "CRITICAL"),
            ({"severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/AV:N"}]}, ""),
            # CVSS 4.0 first: a low 2.0, though the CVSS 3 vector is critical.
            ({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"},
                           {"type": "CVSS_V4", "score": "CVSS:4.0/AV:L/AC:L/AT:P/PR:L/UI:N/VC:N/VI:N/VA:L/SC:N/SI:N/SA:N"}]},
             "LOW"),
            ({}, ""),
        ]
        for record, want in rows:
            with self.subTest(record=record):
                self.assertEqual(match.severity(record), want)

    def test_severity_text(self):
        rows = [(("HIGH", 7.5), "high 7.5"), (("HIGH", None), "high"), (("", None), "unknown"), (("", 0.0), "none 0.0"),
                (("LOW", 2.0), "low 2.0")]
        for (sev, score), want in rows:
            with self.subTest(want=want):
                self.assertEqual(match.severity_text(Advisory("X", [], "", sev, [], score=score)), want)

    def test_advisory(self):
        q = package_query("npm", "x", "1.0")
        record = {"id": "GHSA-1", "aliases": ["CVE-1"], "details": "First line\nmore",
                  "affected": [{"package": {"ecosystem": "npm", "name": "x"},
                                "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "1.2.0"}]}]},
                               {"package": {"ecosystem": "npm", "name": "other"},
                                "ranges": [{"type": "SEMVER", "events": [{"fixed": "9"}]}]}]}
        a = match.advisory(record, q)
        self.assertEqual((a.id, a.aliases, a.summary, a.fixed, a.malicious), ("GHSA-1", ["CVE-1"], "First line", ["1.2.0"], False))
        mal = match.advisory({"id": "MAL-2025-1", "summary": "Malicious code in x"}, q)
        self.assertEqual((mal.malicious, mal.severity), (True, "CRITICAL"))


class RangeTests(unittest.TestCase):
    Q = package_query("GitHub Actions", "o/a")

    def record(self, *ranges, versions=()):
        return {"affected": [{"package": {"ecosystem": "GitHub Actions", "name": "O/A"}, "versions": list(versions),
                              "ranges": [{"type": "ECOSYSTEM", "events": list(r)} for r in ranges]}]}

    def test_in_ranges(self):
        r = self.record([{"introduced": "0"}, {"fixed": "46.0.1"}], [{"introduced": "47.0.0"}, {"last_affected": "47.1"}],
                        [{"introduced": "50"}], versions=["v48.0.0"])
        rows = [("45.0.7", True), ("v45", True), ("46.0.1", False), ("46.0.1-rc.1", True), ("47.1.0", True),
                ("47.2", False), ("v48.0.0", True), ("49", False), ("51.2", True)]
        for version, want in rows:
            with self.subTest(version=version):
                self.assertEqual(match.in_ranges(r, self.Q, version), want)
        other = {"affected": [{"package": {"ecosystem": "npm", "name": "o/a"}, "ranges": [
            {"type": "ECOSYSTEM", "events": [{"introduced": "0"}]}]}]}
        self.assertFalse(match.in_ranges(other, self.Q, "1.0"))

    def test_has_fix(self):
        self.assertTrue(match.has_fix(self.record([{"introduced": "0"}, {"fixed": "2"}]), self.Q))
        self.assertTrue(match.has_fix(self.record([{"introduced": "0"}, {"last_affected": "2"}]), self.Q))
        self.assertFalse(match.has_fix(self.record([{"introduced": "0"}]), self.Q))

    def test_notice(self):
        self.assertEqual(match.notice({"affected": [{"database_specific": {"informational": "unmaintained"}}]}),
                         "unmaintained")
        self.assertEqual(match.notice({"affected": [{}]}), "")


class RuleTests(unittest.TestCase):
    def test_valid(self):
        rules, errors = match.rules({"ignore": [{"id": "CVE-1", "reason": " no ", "tool": "fzf",
                                                 "until": datetime.date(2026, 1, 1)}]}, "audit.toml")
        self.assertEqual(errors, [])
        self.assertEqual(rules, [match.Rule("CVE-1", "no", "fzf", datetime.date(2026, 1, 1), "audit.toml")])

    def test_errors(self):
        rows = [
            ({"ignore": {"id": "x"}}, "audit.toml: ignore must be a list of tables: [[ignore]]."),
            ({"ignore": ["x"]}, "audit.toml: ignore 1 is not a table."),
            ({"ignore": [{"reason": "r"}]}, "audit.toml: ignore 1 has no id."),
            ({"ignore": [{"id": "CVE-1"}]}, "audit.toml: the ignore of CVE-1 has no reason."),
            ({"ignore": [{"id": "CVE-1", "reason": "r", "tool": 1}]}, "audit.toml: the tool of the ignore of CVE-1 is not a string."),
            ({"ignore": [{"id": "CVE-1", "reason": "r", "until": "2026-01-01"}]},
             "audit.toml: the until of the ignore of CVE-1 is not a date, such as 2026-12-31."),
        ]
        for data, error in rows:
            with self.subTest(data=data):
                self.assertEqual(match.rules(data, "audit.toml"), ([], [error]))


class FindingsTests(unittest.TestCase):
    TODAY = datetime.date(2026, 6, 1)

    def run_(self, items, ids, records, rules=(), more=()):
        return match.findings(items, ids, records, list(rules), self.TODAY, list(more))

    def test_aliases_merge(self):
        item = Item("mise", "uv", "1.0", [git_query("x", "1"), package_query("PyPI", "uv", "1.0")])
        records = {"CVE-1": {"id": "CVE-1", "summary": "s"},
                   "GHSA-1": {"id": "GHSA-1", "aliases": ["CVE-1"], "database_specific": {"severity": "HIGH"}},
                   "GHSA-2": {"id": "GHSA-2"}}
        r = self.run_([item], [["CVE-1"], ["GHSA-1", "GHSA-2"]], records)
        self.assertEqual([(f.advisory.id, f.advisory.aliases, f.advisory.severity) for f in r.findings],
                         [("CVE-1", ["GHSA-1"], "HIGH"), ("GHSA-2", [], "")])

    def test_ignore_patch_expired(self):
        a = Item("nix", "fzf", "1", [git_query("x", "1")], patched=frozenset({"CVE-3"}))
        b = Item("nix", "jq", "1", [git_query("y", "1")])
        records = {i: {"id": i} for i in ("CVE-1", "CVE-2", "CVE-3")}
        rules = [match.Rule("CVE-1", "r", "fzf"), match.Rule("CVE-2", "r", "", datetime.date(2026, 5, 31)),
                 match.Rule("CVE-2", "r", "other")]
        brew = Finding(Item("brew", "wget", "1"), Advisory("CVE-1", [], "", "", []))
        r = self.run_([a, b], [["CVE-1", "CVE-2", "CVE-3"], ["CVE-2"]], records, rules, [brew])
        self.assertEqual([f.key for f in r.findings], ["nix/fzf/CVE-2", "nix/jq/CVE-2", "brew/wget/CVE-1"])
        self.assertEqual([(f.key, rule.id) for f, rule in r.ignored], [("nix/fzf/CVE-1", "CVE-1")])
        self.assertEqual([f.key for f in r.patched], ["nix/fzf/CVE-3"])
        self.assertEqual([x.id for x in r.expired], ["CVE-2"])

    def test_verdicts(self):
        """A notice and a verdict of the source are left out with their reason; the verdict of a record holds
        for the record of its alias; "" drops a record; a withdrawn record is left out."""
        def accept(record, query):
            return {"GO-1": "not called", "GO-2": "", "GO-3": None}.get(record["id"])
        item = Item("binaries", "fzf > golang.org/x/net", "v0.57.0", [git_query("x", "1")], accept=accept, parent="fzf")
        records = {"GO-1": {"id": "GO-1", "aliases": ["GHSA-1"]}, "GHSA-1": {"id": "GHSA-1"},
                   "GO-2": {"id": "GO-2"}, "GO-3": {"id": "GO-3"}, "GO-4": {"id": "GO-4", "withdrawn": "2026"},
                   "RUSTSEC-1": {"id": "RUSTSEC-1", "affected": [{"database_specific": {"informational": "unsound"}}]}}
        r = self.run_([item], [["GHSA-1", "GO-1", "GO-2", "GO-3", "GO-4", "RUSTSEC-1"]], records)
        self.assertEqual([f.advisory.id for f in r.findings], ["GO-3"])
        self.assertEqual([(f.advisory.id, why) for f, why in r.left_out], [("GHSA-1", "not called")])
        self.assertEqual([(f.advisory.id, k) for f, k in r.notices], [("RUSTSEC-1", "unsound")])

    def test_rule_of_the_parent(self):
        item = Item("binaries", "mise > zip", "8.6.0", [git_query("x", "1")], parent="mise")
        r = self.run_([item], [["RUSTSEC-1"]], {"RUSTSEC-1": {"id": "RUSTSEC-1"}}, [match.Rule("RUSTSEC-1", "r", "mise")])
        self.assertEqual(len(r.ignored), 1)


if __name__ == "__main__":
    unittest.main()
