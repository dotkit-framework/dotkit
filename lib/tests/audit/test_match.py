"""The decisions of dotkit audit (BEHAVIOR.md, rows Au): the queries of each tool, the severity, the
ignore rules, and the findings. Tables: match.py runs no command."""

from __future__ import annotations

import datetime
import unittest

from dotkit.audit import match

GH = "https://github.com"


def nix(name="tool", version="1.0", **kw) -> dict:
    return {"name": name, "version": version, "url": "", "git": "", "tag": "", "rev": "", "homepage": "",
            "patches": [], **kw}


class NixItemTests(unittest.TestCase):
    def queries(self, **kw):
        return match.nix_item(nix(**kw)).queries

    def test_sources(self):
        rows = [
            # An archive of a tag, with the tag of fetchFromGitHub. The URL is lowercase for OSV.
            (dict(url=f"{GH}/BurntSushi/ripgrep/archive/refs/tags/15.2.0.tar.gz", tag="15.2.0"),
             [match.git_query(f"{GH}/burntsushi/ripgrep", "15.2.0")]),
            # rev = refs/tags/TAG
            (dict(url=f"{GH}/sharkdp/bat/archive/refs/tags/v0.26.1.tar.gz", rev="refs/tags/v0.26.1"),
             [match.git_query(f"{GH}/sharkdp/bat", "v0.26.1")]),
            # No tag and no rev: the ref of the archive URL.
            (dict(url=f"{GH}/nixos/nixfmt/archive/v1.5.0.tar.gz"), [match.git_query(f"{GH}/nixos/nixfmt", "v1.5.0")]),
            # A release asset: the tag of the release.
            (dict(url=f"{GH}/jqlang/jq/releases/download/jq-1.8.2/jq-1.8.2.tar.gz"),
             [match.git_query(f"{GH}/jqlang/jq", "jq-1.8.2")]),
            # A Git clone.
            (dict(url=f"{GH}/cli/cli.git", tag="v2.102.0"), [match.git_query(f"{GH}/cli/cli", "v2.102.0")]),
            # A commit.
            (dict(url=f"{GH}/sobolevn/git-secret/archive/{'f' * 40}.tar.gz", rev="f" * 40), [{"commit": "f" * 40}]),
            # GitLab by its API.
            (dict(url="https://gitlab.com/api/v4/projects/OldManProgrammer%2Funix-tree/repository/archive.tar.gz?sha=2.3.2"),
             [match.git_query("https://gitlab.com/oldmanprogrammer/unix-tree", "2.3.2")]),
            # Only the homepage: both forms of the tag.
            (dict(homepage=f"{GH}/fastfetch-cli/fastfetch"),
             [match.git_query(f"{GH}/fastfetch-cli/fastfetch", "v1.0"), match.git_query(f"{GH}/fastfetch-cli/fastfetch", "1.0")]),
        ]
        for kw, want in rows:
            with self.subTest(kw=kw):
                self.assertEqual(self.queries(**kw), want)

    def test_known_repositories(self):
        self.assertEqual(self.queries(name="git", version="2.56.0", url="https://www.kernel.org/x.tar.xz"),
                         [match.git_query(f"{GH}/git/git", "v2.56.0")])
        self.assertEqual(self.queries(name="curl", version="8.10.1"), [match.git_query(f"{GH}/curl/curl", "curl-8_10_1")])
        self.assertEqual(self.queries(name="zsh", version="5.9.2"), [match.git_query(f"{GH}/zsh-users/zsh", "zsh-5.9.2")])

    def test_not_checked(self):
        item = match.nix_item(nix(name="gnu-thing", url="mirror://gnu/thing-1.0.tar.gz"))
        self.assertEqual(item.queries, [])
        self.assertEqual(item.why, "its source is not in a Git repository that OSV knows")

    def test_no_version_is_no_item(self):
        self.assertIsNone(match.nix_item(nix(name="darwin-rebuild", version="")))
        self.assertIsNone(match.nix_item(nix(name="", version="1")))

    def test_patches(self):
        item = match.nix_item(nix(patches=["CVE-2024-1234.patch", "fix-cve-2025-99999-and-more.patch", "other.patch"]))
        self.assertEqual(item.patched, frozenset({"CVE-2024-1234", "CVE-2025-99999"}))


class MiseItemTests(unittest.TestCase):
    def test_backends(self):
        rows = [
            ("npm:cowsay", (), [match.package_query("npm", "cowsay", "1.0")]),
            ("npm:@scope/pkg", (), [match.package_query("npm", "@scope/pkg", "1.0")]),
            ("pipx:black", (), [match.package_query("PyPI", "black", "1.0")]),
            ("cargo:ripgrep", (), [match.package_query("crates.io", "ripgrep", "1.0")]),
            ("go:golang.org/x/tools/gopls", (), [match.package_query("Go", "golang.org/x/tools/gopls", "1.0")]),
            ("github:Modem-Dev/hunk", (), [match.git_query(f"{GH}/modem-dev/hunk", "v1.0"),
                                           match.git_query(f"{GH}/modem-dev/hunk", "1.0")]),
            ("github:o/r[exe=x]", (), [match.git_query(f"{GH}/o/r", "v1.0"), match.git_query(f"{GH}/o/r", "1.0")]),
            ("node", (), [match.git_query(f"{GH}/nodejs/node", "v1.0")]),
            ("python", (), [match.git_query(f"{GH}/python/cpython", "v1.0")]),
            ("go", (), [match.package_query("Go", "stdlib", "1.0")]),
            # The registry: the backends, without asdf and vfox, and without repeats.
            ("uv", ("aqua:astral-sh/uv", "asdf:asdf-community/asdf-uv", "pypi:uv"),
             [match.git_query(f"{GH}/astral-sh/uv", "v1.0"), match.git_query(f"{GH}/astral-sh/uv", "1.0"),
              match.package_query("PyPI", "uv", "1.0")]),
            # An alias: the npm package that has the advisories.
            ("claude-code", ("aqua:anthropics/claude-code", "http:claude"),
             [match.package_query("npm", "@anthropic-ai/claude-code", "1.0"),
              match.git_query(f"{GH}/anthropics/claude-code", "v1.0"), match.git_query(f"{GH}/anthropics/claude-code", "1.0")]),
        ]
        for key, backends, want in rows:
            with self.subTest(key=key):
                self.assertEqual(match.mise_item(key, "1.0", backends).queries, want)

    def test_not_checked(self):
        for key, backends in (("ruby", ()), ("asdf:x/y", ()), ("tool", ("vfox:x",))):
            with self.subTest(key=key):
                item = match.mise_item(key, "1.0", backends)
                self.assertEqual(item.queries, [])
                self.assertEqual(item.why, "OSV has no data for its source")


class DotkitItemTests(unittest.TestCase):
    def test_vendored(self):
        items = match.vendored_items("# c\nprompt_toolkit==3.0.52 --hash=sha256:x\ntomli==2.3.2\n\n")
        self.assertEqual([(i.name, i.version, i.queries) for i in items],
                         [("prompt_toolkit", "3.0.52", [match.package_query("PyPI", "prompt_toolkit", "3.0.52")]),
                          ("tomli", "2.3.2", [match.package_query("PyPI", "tomli", "2.3.2")])])

    def test_flake_inputs(self):
        lock = {"nodes": {"root": {"inputs": {}},
                          "zsh-abbr": {"locked": {"type": "github", "rev": "a" * 40}},
                          "local": {"locked": {"type": "path", "path": "/x"}},
                          "short": {"locked": {"type": "github", "rev": "abc"}}}}
        items = match.flake_items(lock)
        self.assertEqual([(i.name, i.version, i.queries) for i in items], [("input zsh-abbr", "a" * 12, [{"commit": "a" * 40}])])


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
            ({}, ""),
        ]
        for record, want in rows:
            with self.subTest(record=record):
                self.assertEqual(match.severity(record), want)

    def test_advisory(self):
        q = match.package_query("npm", "x", "1.0")
        record = {"id": "GHSA-1", "aliases": ["CVE-1"], "details": "First line\nmore",
                  "affected": [{"package": {"ecosystem": "npm", "name": "x"},
                                "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "1.2.0"}]}]},
                               {"package": {"ecosystem": "npm", "name": "other"},
                                "ranges": [{"type": "SEMVER", "events": [{"fixed": "9"}]}]}]}
        a = match.advisory(record, q)
        self.assertEqual((a.id, a.aliases, a.summary, a.fixed, a.malicious), ("GHSA-1", ["CVE-1"], "First line", ["1.2.0"], False))
        mal = match.advisory({"id": "MAL-2025-1", "summary": "Malicious code in x"}, q)
        self.assertEqual((mal.malicious, mal.severity), (True, "CRITICAL"))


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
        item = match.Item("mise", "uv", "1.0", [match.git_query("x", "1"), match.package_query("PyPI", "uv", "1.0")])
        records = {"CVE-1": {"id": "CVE-1", "summary": "s"},
                   "GHSA-1": {"id": "GHSA-1", "aliases": ["CVE-1"], "database_specific": {"severity": "HIGH"}},
                   "GHSA-2": {"id": "GHSA-2"}}
        r = self.run_([item], [["CVE-1"], ["GHSA-1", "GHSA-2"]], records)
        self.assertEqual([(f.advisory.id, f.advisory.aliases, f.advisory.severity) for f in r.findings],
                         [("CVE-1", ["GHSA-1"], "HIGH"), ("GHSA-2", [], "")])

    def test_ignore_patch_expired(self):
        a = match.Item("nix", "fzf", "1", [match.git_query("x", "1")], patched=frozenset({"CVE-3"}))
        b = match.Item("nix", "jq", "1", [match.git_query("y", "1")])
        records = {i: {"id": i} for i in ("CVE-1", "CVE-2", "CVE-3")}
        rules = [match.Rule("CVE-1", "r", "fzf"), match.Rule("CVE-2", "r", "", datetime.date(2026, 5, 31)),
                 match.Rule("CVE-2", "r", "other")]
        brew = match.Finding(match.Item("brew", "wget", "1"), match.Advisory("CVE-1", [], "", "", []))
        r = self.run_([a, b], [["CVE-1", "CVE-2", "CVE-3"], ["CVE-2"]], records, rules, [brew])
        self.assertEqual([f.key for f in r.findings], ["nix/fzf/CVE-2", "nix/jq/CVE-2", "brew/wget/CVE-1"])
        self.assertEqual([(f.key, rule.id) for f, rule in r.ignored], [("nix/fzf/CVE-1", "CVE-1")])
        self.assertEqual([f.key for f in r.patched], ["nix/fzf/CVE-3"])
        self.assertEqual([x.id for x in r.expired], ["CVE-2"])


if __name__ == "__main__":
    unittest.main()
