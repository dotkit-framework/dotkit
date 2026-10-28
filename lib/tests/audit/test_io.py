"""The parts of dotkit audit that the scenarios do not reach (BEHAVIOR.md, rows Au): the pages and the
batches of OSV, the cache, the issues of gh, and the notification, which runs /usr/bin/osascript."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import List
from unittest import mock

from dotkit.audit import cli, issues, osv
from dotkit.audit.model import Advisory, Finding, Item
from dotkit.core import proc
from dotkit.core.env import Env
from tests import fake


class Calls:
    """A fake proc.run: replies in order, and logs (argv, stdin)."""

    def __init__(self, replies: List[proc.Result]):
        self.replies = list(replies)
        self.log: List[tuple] = []

    def __call__(self, argv, stdin=None, **kw):
        self.log.append((list(argv), stdin))
        return self.replies.pop(0) if self.replies else proc.Result(0, "{}")


def ok(data) -> proc.Result:
    return proc.Result(0, json.dumps(data))


class OsvTests(unittest.TestCase):
    def test_pages(self):
        queries = [{"commit": "a"}, {"commit": "b"}]
        calls = Calls([ok({"results": [{"vulns": [{"id": "A-1", "modified": "m1"}], "next_page_token": "t"},
                                       {"vulns": [{"id": "B-1", "modified": "m2"}, {"id": "../x"}]}]}),
                       ok({"results": [{"vulns": [{"id": "A-2", "modified": "m3"}]}]})])
        with mock.patch.object(osv.proc, "run", calls):
            got = osv.query(queries)
        self.assertEqual(got, [[("A-1", "m1"), ("A-2", "m3")], [("B-1", "m2")]])
        self.assertEqual(json.loads(calls.log[1][1]), {"queries": [{"commit": "a", "page_token": "t"}]})

    def test_batches(self):
        queries = [{"commit": str(i)} for i in range(osv.BATCH + 1)]
        calls = Calls([ok({"results": [{}] * osv.BATCH}), ok({"results": [{}]})])
        with mock.patch.object(osv.proc, "run", calls):
            self.assertEqual(len(osv.query(queries)), osv.BATCH + 1)
        self.assertEqual([len(json.loads(stdin)["queries"]) for _, stdin in calls.log], [osv.BATCH, 1])

    def test_wrong_count(self):
        with mock.patch.object(osv.proc, "run", Calls([ok({"results": []})])):
            with self.assertRaises(osv.ui.Exit) as e:
                osv.query([{"commit": "a"}])
        self.assertEqual(e.exception.text, "dotkit: https://api.osv.dev/v1/querybatch returned 0 results for 1 queries.")

    def test_cache(self):
        tmp = fake.tempdir()
        self.addCleanup(fake.remove, tmp)
        (tmp / "A-1.json").write_text(json.dumps({"batch_modified": "m1", "record": {"id": "A-1", "cached": True}}))
        (tmp / "B-1.json").write_text(json.dumps({"batch_modified": "old", "record": {"id": "B-1"}}))
        curl = Downloads({"B-1": {"id": "B-1", "modified": "new"}, "C-1": {"id": "C-1"}})
        with mock.patch.object(osv.proc, "run", curl):
            got = osv.records({"A-1": "m1", "B-1": "new", "C-1": "m"}, tmp)
        self.assertTrue(got["A-1"]["cached"])
        self.assertEqual((got["B-1"]["modified"], got["C-1"]["id"]), ("new", "C-1"))
        # One curl for both, in parallel.
        self.assertEqual(len(curl.log), 1)
        self.assertEqual(curl.log[0][-6:], ["-o", str(tmp / "B-1.download"), "https://api.osv.dev/v1/vulns/B-1",
                                            "-o", str(tmp / "C-1.download"), "https://api.osv.dev/v1/vulns/C-1"])
        self.assertIn("--parallel", curl.log[0])
        self.assertEqual(json.loads((tmp / "B-1.json").read_text())["batch_modified"], "new")
        self.assertEqual(sorted(p.name for p in tmp.iterdir()), ["A-1.json", "B-1.json", "C-1.json"])

    def test_cache_time_of_the_batch(self):
        """The batch gives microseconds, the advisory nanoseconds: the cache compares the time of the batch."""
        tmp = fake.tempdir()
        self.addCleanup(fake.remove, tmp)
        curl = Downloads({"A-1": {"id": "A-1", "modified": "2026-10-01T00:00:03.167701747Z"}})
        with mock.patch.object(osv.proc, "run", curl):
            osv.records({"A-1": "2026-10-01T00:00:03.167701Z"}, tmp)
            osv.records({"A-1": "2026-10-01T00:00:03.167701Z"}, tmp)
        self.assertEqual(len(curl.log), 1)

    def test_an_old_download_does_not_count(self):
        tmp = fake.tempdir()
        self.addCleanup(fake.remove, tmp)
        (tmp / "A-1.download").write_text(json.dumps({"id": "A-1", "old": True}))
        with mock.patch.object(osv.proc, "run", Downloads({}, err="curl: (6) Could not resolve host")):
            with self.assertRaises(osv.ui.Exit):
                osv.records({"A-1": "m"}, tmp)

    def test_a_download_fails(self):
        tmp = fake.tempdir()
        self.addCleanup(fake.remove, tmp)
        curl = Downloads({"A-1": {"id": "A-1"}}, err="curl: (22) The requested URL returned error: 404")
        with mock.patch.object(osv.proc, "run", curl):
            with self.assertRaises(osv.ui.Exit) as e:
                osv.records({"A-1": "m", "B-1": "m"}, tmp)
        self.assertEqual(e.exception.text, "dotkit: cannot read 1 of 2 advisories from https://api.osv.dev/v1/vulns: "
                                           "curl: (22) The requested URL returned error: 404")


class Downloads:
    """A fake curl for osv.records: writes the record of each -o FILE URL that it has."""

    def __init__(self, records: dict, err: str = ""):
        self.records, self.err, self.log = records, err, []

    def __call__(self, argv, **kw):
        self.log.append(list(argv))
        for i, a in enumerate(argv):
            if a == "-o":
                rid = argv[i + 2].rsplit("/", 1)[1]
                if rid in self.records:
                    Path(argv[i + 1]).write_text(json.dumps(self.records[rid]))
        return proc.Result(22 if self.err else 0, "", self.err)


def finding(name="fzf", rid="CVE-1", version="1.0", source="nix") -> Finding:
    return Finding(Item(source, name, version), Advisory(rid, [], "A bug", "HIGH", []))


class OnePerKeyTests(unittest.TestCase):
    def test_versions_join(self):
        a, b, c = finding("node", version="22.1.0"), finding("node", version="20.9.0"), finding("node", version="22.1.0")
        out = cli._one_per_key([a, b, c, finding("jq")])
        self.assertEqual([(f.item.name, f.item.version) for f in out], [("node", "20.9.0, 22.1.0"), ("jq", "1.0")])


class IssueTests(unittest.TestCase):
    REPO = Path("/repo")

    def sync(self, existing, findings, complete=("nix", "brew", "mise", "dotkit"), unchecked=()):
        calls = Calls([proc.Result(0), ok(existing)])
        with mock.patch.object(issues.proc, "run", calls):
            counts = issues.sync(self.REPO, findings, list(complete), set(unchecked))
        self.calls = calls
        return counts, [argv[1:4] for argv, _ in calls.log[2:]]

    def issue(self, number, f, state="OPEN", body=None):
        return {"number": number, "state": state, "title": issues.title(f), "body": issues.body(f) if body is None else body}

    def test_open_reopen_update_close(self):
        new, back, changed, gone = finding("a"), finding("b"), finding("c", version="2.0"), finding("d")
        existing = [self.issue(1, back, "CLOSED"), self.issue(2, finding("c")), self.issue(3, gone),
                    # An older issue of the same key: the newest counts.
                    self.issue(9, back, "OPEN")]
        counts, calls = self.sync(existing, [new, back, changed])
        self.assertEqual(counts, {"opened": 1, "reopened": 1, "updated": 1, "closed": 1})
        self.assertEqual(calls, [["issue", "create", "--title"], ["issue", "reopen", "1"], ["issue", "edit", "2"],
                                 ["issue", "close", "3"]])

    def test_in_the_repository(self):
        self.sync([], [finding()])
        self.assertEqual({kw for argv, _ in self.calls.log for kw in [argv[1]]}, {"label", "issue"})
        with mock.patch.object(issues.proc, "run") as run:
            run.return_value = proc.Result(0, "[]")
            issues.sync(self.REPO, [], ["nix"])
        self.assertEqual({c.kwargs["cwd"] for c in run.call_args_list}, {self.REPO})

    def test_same_body_without_newline(self):
        f = finding()
        counts, calls = self.sync([self.issue(1, f, body=issues.body(f).rstrip("\n"))], [f])
        self.assertEqual((counts, calls), ({"opened": 0, "reopened": 0, "updated": 0, "closed": 0}, []))

    def test_key_with_spaces(self):
        f = finding("input zsh-abbr", source="dotkit")
        counts, calls = self.sync([self.issue(1, f)], [f])
        self.assertEqual((counts["opened"], calls), (0, []))

    def test_alias_keys(self):
        """OSV adds the CVE alias of PSF-1, and the CVE sorts first: the issue of PSF-1 is the same finding. A
        second open issue of the same finding closes."""
        old = finding("python", "PSF-1", source="mise")
        other = finding("python", "GHSA-1", source="mise")
        now = Finding(old.item, Advisory("CVE-1", ["GHSA-1", "PSF-1"], "A bug", "HIGH", []))
        counts, calls = self.sync([self.issue(4, old), self.issue(5, other)], [now])
        self.assertEqual(counts, {"opened": 0, "reopened": 0, "updated": 1, "closed": 1})
        self.assertEqual(calls, [["issue", "edit", "5"], ["issue", "close", "4"]])

    def test_failed_source_keeps_issues(self):
        counts, calls = self.sync([self.issue(1, finding())], [], complete=("brew", "mise", "dotkit"))
        self.assertEqual((counts["closed"], calls), (0, []))

    def test_unchecked_tool_keeps_issues(self):
        counts, calls = self.sync([self.issue(1, finding("hunk", source="mise"))], [], unchecked={"mise/hunk"})
        self.assertEqual((counts["closed"], calls), (0, []))

    def test_body(self):
        mal = Finding(Item("mise", "npm:x", "1.0"), Advisory("MAL-1", ["GHSA-1"], "Malicious code in @x/y",
                                                                              "CRITICAL", ["1.1"], True))
        text = issues.body(mal)
        self.assertTrue(text.startswith("<!-- dotkit-audit: mise/npm:x/MAL-1 -->\n**OSV lists this release as malicious.**"))
        self.assertIn("| Fixed in | 1.1 |", text)
        self.assertIn("Malicious code in &#64;x/y\n", text)
        self.assertIn("remove the tool (`dotkit remove npm:x`)", text)
        self.assertEqual(issues.title(mal), "npm:x 1.0: malicious release MAL-1")


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir()
        self.addCleanup(fake.remove, self.dir)
        self.env = Env(repo=self.dir, dotkit=self.dir, home=self.dir)
        patcher = mock.patch.dict(cli.os.environ, {"XDG_STATE_HOME": str(self.dir / "state")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def notify(self, findings) -> List[list]:
        calls = Calls([])
        with mock.patch.object(cli.proc, "run", calls):
            cli.notify(self.env, findings)
        return [argv for argv, _ in calls.log]

    def test_only_new(self):
        a, b = finding("a"), finding("b")
        first = self.notify([a])
        self.assertEqual(first[0][:1] + first[0][-2:], ["/usr/bin/osascript", "dotkit audit",
                                                        "1 new finding: a. Run dotkit audit."])
        self.assertEqual(self.notify([a]), [])
        self.assertEqual(self.notify([a, b])[0][-1], "1 new finding: b. Run dotkit audit.")
        state = json.loads((self.dir / "state/dotkit/audit.json").read_text())
        self.assertEqual(state, {"seen": ["nix/a/CVE-1", "nix/b/CVE-1"]})
        # Gone, then back: new again.
        self.assertEqual(self.notify([]), [])
        self.assertEqual(len(self.notify([a])), 1)

    def test_alias_and_failed_source(self):
        a = finding("a", "PSF-1")
        self.notify([a, finding("b", source="mise")])
        # The CVE alias sorts first now: not new. mise failed: its key stays for the next run.
        now = Finding(a.item, Advisory("CVE-1", ["PSF-1"], "", "", []))
        self.assertEqual(self.notify_keep([now], {"mise"}), [])
        state = json.loads((self.dir / "state/dotkit/audit.json").read_text())
        self.assertEqual(state, {"seen": ["mise/b/CVE-1", "nix/a/CVE-1", "nix/a/PSF-1"]})
        self.assertEqual(self.notify([finding("b", source="mise")]), [])

    def notify_keep(self, findings, keep) -> List[list]:
        calls = Calls([])
        with mock.patch.object(cli.proc, "run", calls):
            cli.notify(self.env, findings, keep)
        return [argv for argv, _ in calls.log]

    def test_malicious(self):
        f = Finding(Item("mise", "npm:x", "1"), Advisory("MAL-1", [], "", "CRITICAL", [], True))
        self.assertTrue(self.notify([f])[0][-1].startswith("A malicious release! 1 new finding: npm:x."))


if __name__ == "__main__":
    unittest.main()
