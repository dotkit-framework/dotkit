"""The sources of dotkit audit (BEHAVIOR.md, rows Au2 to Au5 and Au12 to Au14): the items and the queries of
each one, and the collectors that read files. The commands of the sources run in the scenarios."""

from __future__ import annotations

import json
import os
import plistlib
import unittest
from pathlib import Path
from unittest import mock

from dotkit.audit import sources
from dotkit.audit.model import git_query, package_query
from dotkit.audit.sources import actions, binaries, dotkit, mise, nix, packages
from dotkit.core.env import Env
from tests import fake
from tests.audit import machofake as mf

GH = "https://github.com"


def pkg(name="tool", version="1.0", **kw) -> dict:
    return {"name": name, "version": version, "url": "", "git": "", "tag": "", "rev": "", "homepage": "",
            "patches": [], **kw}


class NixItemTests(unittest.TestCase):
    def queries(self, **kw):
        return nix.item(pkg(**kw)).queries

    def test_sources(self):
        rows = [
            # An archive of a tag, with the tag of fetchFromGitHub. The URL is lowercase for OSV.
            (dict(url=f"{GH}/BurntSushi/ripgrep/archive/refs/tags/15.2.0.tar.gz", tag="15.2.0"),
             [git_query(f"{GH}/burntsushi/ripgrep", "15.2.0")]),
            # rev = refs/tags/TAG
            (dict(url=f"{GH}/sharkdp/bat/archive/refs/tags/v0.26.1.tar.gz", rev="refs/tags/v0.26.1"),
             [git_query(f"{GH}/sharkdp/bat", "v0.26.1")]),
            # No tag and no rev: the ref of the archive URL.
            (dict(url=f"{GH}/nixos/nixfmt/archive/v1.5.0.tar.gz"), [git_query(f"{GH}/nixos/nixfmt", "v1.5.0")]),
            # A release asset: the tag of the release.
            (dict(url=f"{GH}/jqlang/jq/releases/download/jq-1.8.2/jq-1.8.2.tar.gz"),
             [git_query(f"{GH}/jqlang/jq", "jq-1.8.2")]),
            # A Git clone.
            (dict(url=f"{GH}/cli/cli.git", tag="v2.102.0"), [git_query(f"{GH}/cli/cli", "v2.102.0")]),
            # A commit.
            (dict(url=f"{GH}/sobolevn/git-secret/archive/{'f' * 40}.tar.gz", rev="f" * 40), [{"commit": "f" * 40}]),
            # GitLab by its API.
            (dict(url="https://gitlab.com/api/v4/projects/OldManProgrammer%2Funix-tree/repository/archive.tar.gz?sha=2.3.2"),
             [git_query("https://gitlab.com/oldmanprogrammer/unix-tree", "2.3.2")]),
            # Only the homepage: both forms of the tag.
            (dict(homepage=f"{GH}/fastfetch-cli/fastfetch"),
             [git_query(f"{GH}/fastfetch-cli/fastfetch", "v1.0"), git_query(f"{GH}/fastfetch-cli/fastfetch", "1.0")]),
        ]
        for kw, want in rows:
            with self.subTest(kw=kw):
                self.assertEqual(self.queries(**kw), want)

    def test_known_repositories(self):
        self.assertEqual(self.queries(name="git", version="2.56.0", url="https://www.kernel.org/x.tar.xz"),
                         [git_query(f"{GH}/git/git", "v2.56.0")])
        self.assertEqual(self.queries(name="curl", version="8.10.1"), [git_query(f"{GH}/curl/curl", "curl-8_10_1")])
        self.assertEqual(self.queries(name="zsh", version="5.9.2"), [git_query(f"{GH}/zsh-users/zsh", "zsh-5.9.2")])

    def test_not_checked(self):
        item = nix.item(pkg(name="gnu-thing", url="mirror://gnu/thing-1.0.tar.gz"))
        self.assertEqual(item.queries, [])
        self.assertEqual(item.why, "its source is not in a Git repository that OSV knows")

    def test_no_version_is_no_item(self):
        self.assertIsNone(nix.item(pkg(name="darwin-rebuild", version="")))
        self.assertIsNone(nix.item(pkg(name="", version="1")))

    def test_patches(self):
        item = nix.item(pkg(patches=["CVE-2024-1234.patch", "fix-cve-2025-99999-and-more.patch", "other.patch"]))
        self.assertEqual(item.patched, frozenset({"CVE-2024-1234", "CVE-2025-99999"}))


class MiseItemTests(unittest.TestCase):
    def test_backends(self):
        rows = [
            ("npm:cowsay", (), [package_query("npm", "cowsay", "1.0")]),
            ("npm:@scope/pkg", (), [package_query("npm", "@scope/pkg", "1.0")]),
            ("pipx:black", (), [package_query("PyPI", "black", "1.0")]),
            ("cargo:ripgrep", (), [package_query("crates.io", "ripgrep", "1.0")]),
            ("go:golang.org/x/tools/gopls", (), [package_query("Go", "golang.org/x/tools/gopls", "1.0")]),
            ("github:Modem-Dev/hunk", (), [git_query(f"{GH}/modem-dev/hunk", "v1.0"),
                                           git_query(f"{GH}/modem-dev/hunk", "1.0")]),
            ("github:o/r[exe=x]", (), [git_query(f"{GH}/o/r", "v1.0"), git_query(f"{GH}/o/r", "1.0")]),
            ("node", (), [git_query(f"{GH}/nodejs/node", "v1.0")]),
            ("python", (), [git_query(f"{GH}/python/cpython", "v1.0")]),
            ("go", (), [package_query("Go", "stdlib", "1.0")]),
            # The registry: the backends, without asdf and vfox, and without repeats.
            ("uv", ("aqua:astral-sh/uv", "asdf:asdf-community/asdf-uv", "pypi:uv"),
             [git_query(f"{GH}/astral-sh/uv", "v1.0"), git_query(f"{GH}/astral-sh/uv", "1.0"),
              package_query("PyPI", "uv", "1.0")]),
            # An alias: the npm package that has the advisories.
            ("claude-code", ("aqua:anthropics/claude-code", "http:claude"),
             [package_query("npm", "@anthropic-ai/claude-code", "1.0"),
              git_query(f"{GH}/anthropics/claude-code", "v1.0"), git_query(f"{GH}/anthropics/claude-code", "1.0")]),
        ]
        for key, backends, want in rows:
            with self.subTest(key=key):
                self.assertEqual(mise.item(key, "1.0", backends).queries, want)

    def test_not_checked(self):
        for key, backends in (("erlang", ()), ("asdf:x/y", ()), ("tool", ("vfox:x",))):
            with self.subTest(key=key):
                item = mise.item(key, "1.0", backends)
                self.assertEqual(item.queries, [])
                self.assertEqual(item.why, "OSV has no data for its source")


class DotkitItemTests(unittest.TestCase):
    def test_vendored(self):
        items = dotkit.vendored("# c\nprompt_toolkit==3.0.52 --hash=sha256:x\ntomli==2.3.2\n\n")
        self.assertEqual([(i.name, i.version, i.queries) for i in items],
                         [("prompt_toolkit", "3.0.52", [package_query("PyPI", "prompt_toolkit", "3.0.52")]),
                          ("tomli", "2.3.2", [package_query("PyPI", "tomli", "2.3.2")])])

    def test_flake_inputs(self):
        lock = {"nodes": {"root": {"inputs": {}},
                          "zsh-abbr": {"locked": {"type": "github", "rev": "a" * 40}},
                          "local": {"locked": {"type": "path", "path": "/x"}},
                          "short": {"locked": {"type": "github", "rev": "abc"}}}}
        items = dotkit.inputs(lock)
        self.assertEqual([(i.name, i.version, i.queries) for i in items], [("input zsh-abbr", "a" * 12, [{"commit": "a" * 40}])])


class RubyTests(unittest.TestCase):
    def test_bitnami_with_a_fix(self):
        item = mise.item("ruby", "3.3.0")
        q = package_query("Bitnami", "ruby", "3.3.0")
        self.assertEqual(item.queries, [q])
        fixed = {"affected": [{"package": {"ecosystem": "Bitnami", "name": "ruby"},
                               "ranges": [{"type": "SEMVER", "events": [{"introduced": "3.3.0"}, {"fixed": "3.3.1"}]}]}]}
        open_ = {"affected": [{"package": {"ecosystem": "Bitnami", "name": "ruby"},
                               "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}]}]}]}
        self.assertIsNone(item.accept(fixed, q))
        self.assertEqual(item.accept(open_, q), "")


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.dir = fake.tempdir().resolve()
        self.addCleanup(fake.remove, self.dir)

    def write(self, rel: str, data, mode: int = 0o644) -> Path:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            p.write_bytes(data)
        else:
            p.write_text(data)
        p.chmod(mode)
        return p

    def ctx(self, mise_ls=None) -> sources.Context:
        env = Env(repo=self.dir / "repo", dotkit=self.dir / "dotkit", home=self.dir / "home")
        (self.dir / "home").mkdir(exist_ok=True)
        c = sources.Context(env, root=self.dir / "root")
        c._mise_done, c._mise = True, mise_ls
        return c


class PackagesTests(Sandbox):
    def test_scan(self):
        r = "ruby/lib/ruby/gems/3.4.0/specifications/"
        self.write(r + "default/uri-1.0.3.gemspec", "")
        self.write(r + "net-http-0.6.0.gemspec", "")
        self.write(r + "nokogiri-1.18.0-arm64-darwin.gemspec", "")
        self.write(r + "README", "")
        n = "node/lib/node_modules/"
        self.write(n + "npm/package.json", json.dumps({"name": "npm", "version": "11.19.1"}))
        self.write(n + "npm/node_modules/undici/package.json", json.dumps({"name": "undici", "version": "6.28.0"}))
        self.write(n + "npm/node_modules/@scope/x/package.json", json.dumps({"name": "@scope/x", "version": "1.0.0"}))
        # Not a package: a package.json inside the files of a package, and one that is not JSON.
        self.write(n + "npm/lib/package.json", json.dumps({"name": "inner", "version": "1"}))
        self.write(n + "broken/package.json", "{")
        self.write("python/lib/python3.14/site-packages/Jinja2-3.1.4.dist-info/METADATA", "")
        found = packages.scan(self.dir)
        self.assertEqual(sorted(found), [
            ("gem", "net-http", "0.6.0"), ("gem", "nokogiri", "1.18.0"), ("gem", "uri", "1.0.3"),
            ("npm", "@scope/x", "1.0.0"), ("npm", "npm", "11.19.1"), ("npm", "undici", "6.28.0"),
            ("python", "jinja2", "3.1.4")])

    def test_items_leave_out_the_tool(self):
        found = [("npm", "cowsay", "1.6.0"), ("npm", "get-stdin", "8.0.0")]
        items = packages.items("npm:cowsay", "1.6.0", found)
        self.assertEqual([(i.name, i.version, i.queries, i.where, i.parent) for i in items], [
            ("npm:cowsay > get-stdin", "8.0.0", [package_query("npm", "get-stdin", "8.0.0")],
             "npm package in npm:cowsay 1.6.0", "npm:cowsay")])

    def test_collect(self):
        self.write("installs/ruby/3.4.12/lib/ruby/gems/3.4.0/specifications/default/uri-1.0.3.gemspec", "")
        entry = {"version": "3.4.12", "installed": True, "active": True,
                 "install_path": str(self.dir / "installs/ruby/3.4.12")}
        part = packages.collect(self.ctx({"ruby": [entry]}))
        self.assertEqual([i.name for i in part.items], ["ruby > uri"])
        self.assertEqual(packages.collect(self.ctx({})).skipped, "no mise tool is installed here")
        self.assertEqual(packages.collect(self.ctx(None)).skipped, "mise does not run here")


class BinariesTests(Sandbox):
    def test_owner(self):
        installs = {"/m/installs/github-o-hunk/0.24.1": ("github:o/hunk", "0.24.1")}
        rows = [
            ("/nix/store/" + "a" * 32 + "-ripgrep-15.2.0/bin/rg", ("ripgrep", "15.2.0", "Nix")),
            ("/nix/store/" + "a" * 32 + "-darwin-rebuild/bin/darwin-rebuild", ("darwin-rebuild", "", "Nix")),
            ("/opt/homebrew/Cellar/mole/1.2.0/bin/mole", ("mole", "1.2.0", "Homebrew")),
            ("/m/installs/github-o-hunk/0.24.1/bin/hunk", ("github:o/hunk", "0.24.1", "mise")),
            ("/usr/local/bin/x", ("x", "", "")),
        ]
        for path, want in rows:
            with self.subTest(path=path):
                self.assertEqual(binaries.owner(path, installs), want)

    def test_app_owner(self):
        self.write("Apps/OrbStack.app/Contents/Info.plist", plistlib.dumps({"CFBundleShortVersionString": "2.2.5"}))
        path = str(self.dir / "Apps/OrbStack.app/Contents/MacOS/scli.app/Contents/MacOS/scli")
        self.assertEqual(binaries.owner(path, {}), ("OrbStack", "2.2.5", "app"))

    def go_tool(self, rel: str, functions):
        data = mf.macho({"__go_buildinfo": mf.go_buildinfo("go1.25.3", [("dep", "golang.org/x/net", "v0.57.0")]),
                         "__gopclntab": mf.go_functions(functions)})
        return self.write(rel, data, 0o755)

    def test_collect(self):
        store = "root/nix/store/" + "b" * 32
        self.go_tool(f"{store}-fzf-0.74.5/bin/fzf", ["main.main"])
        rust = mf.macho({".dep-v0": mf.rust_deps([{"name": "regex", "version": "1.11.0", "source": "crates.io"}])})
        self.write(f"{store}-ripgrep-15.2.0/bin/rg", rust, 0o755)
        self.write(f"{store}-hello-1.0/bin/script", "#!/bin/sh\n", 0o755)
        bin_ = self.dir / "root/run/current-system/sw/bin"
        bin_.mkdir(parents=True)
        for rel in ("fzf-0.74.5/bin/fzf", "ripgrep-15.2.0/bin/rg", "hello-1.0/bin/script"):
            os.symlink(self.dir / f"{store}-{rel}", bin_ / rel.rsplit("/", 1)[1])
        part = binaries.collect(self.ctx({}))
        self.assertEqual([(i.name, i.version, i.queries[0], i.where) for i in part.items], [
            ("fzf > golang.org/x/net", "v0.57.0", package_query("Go", "golang.org/x/net", "v0.57.0"),
             "Go module in fzf 0.74.5 (Nix)"),
            ("fzf > Go", "1.25.3", package_query("Go", "stdlib", "1.25.3"), "Go standard library in fzf 0.74.5 (Nix)"),
            ("ripgrep > regex", "1.11.0", package_query("crates.io", "regex", "1.11.0"),
             "Rust crate in ripgrep 15.2.0 (Nix)")])
        self.assertEqual(binaries.collect(self.ctx({})).skipped, "")
        empty = Sandbox.ctx(self, {})
        empty.root = self.dir / "nothing"
        self.assertEqual(binaries.collect(empty).skipped, "no tool is installed here")

    def test_wrapped_and_mise_executables(self):
        """A Nix wrapper script has the program next to it as .NAME-wrapped; a mise tool from GitHub has its
        executable in the install directory or a directory in it."""
        store = "root/nix/store/" + "c" * 32 + "-gh-2.103.2/bin/"
        self.write(store + "gh", "#!/bin/sh\nexec .gh-wrapped\n", 0o755)
        self.go_tool(store + ".gh-wrapped", ["main.main"])
        bin_ = self.dir / "root/run/current-system/sw/bin"
        bin_.mkdir(parents=True)
        os.symlink(self.dir / store / "gh", bin_ / "gh")
        uv = self.dir / "installs/uv/0.13.6"
        rust = mf.macho({".dep-v0": mf.rust_deps([{"name": "tokio", "version": "1.0.0", "source": "crates.io"}])})
        self.write("installs/uv/0.13.6/uv-aarch64-apple-darwin/uv", rust, 0o755)
        self.write("installs/hunk/0.24.1/hunk", rust, 0o755)
        entries = {"uv": [{"version": "0.13.6", "installed": True, "active": True, "install_path": str(uv)}],
                   "github:o/hunk": [{"version": "0.24.1", "installed": True, "active": True,
                                      "install_path": str(self.dir / "installs/hunk/0.24.1")}]}
        names = [i.name for i in binaries.collect(self.ctx(entries)).items]
        self.assertEqual(names, ["gh > golang.org/x/net", "gh > Go", "github:o/hunk > tokio", "uv > tokio"])

    def test_go_tables_are_read_once(self):
        p = self.go_tool("tool", ["golang.org/x/net/http2.(*serverConn).processHeaders"])
        tables: dict = {}
        q = package_query("Go", "golang.org/x/net", "v0.57.0")
        record = {"id": "GO-1", "affected": [{"package": {"name": "golang.org/x/net"}, "ecosystem_specific": {
            "imports": [{"path": "golang.org/x/net/http2", "symbols": ["Server.ServeConn"]}]}}]}
        with mock.patch.object(binaries.macho, "read_section", wraps=binaries.macho.read_section) as read:
            for _ in range(3):
                self.assertEqual(binaries.GoFunctions([p], tables)(record, q), binaries.NOT_CALLED)
        self.assertEqual(read.call_count, 1)

    def test_go_functions(self):
        p = self.go_tool("tool", ["golang.org/x/net/http2.(*serverConn).processHeaders"])
        check = binaries.GoFunctions([p])
        q = package_query("Go", "golang.org/x/net", "v0.57.0")

        def record(rid, symbols):
            return {"id": rid, "affected": [{"package": {"ecosystem": "Go", "name": "golang.org/x/net"},
                                             "ecosystem_specific": {"imports": [{"path": "golang.org/x/net/http2",
                                                                                 "symbols": symbols}]}}]}
        self.assertIsNone(check(record("GO-1", ["serverConn.processHeaders"]), q))
        self.assertEqual(check(record("GO-2", ["Server.ServeConn"]), q), binaries.NOT_CALLED)
        # The whole package, no functions named.
        self.assertIsNone(check(record("GO-3", []), q))
        # A GHSA record gives no verdict; its GO alias decides (match.findings).
        self.assertIsNone(check(record("GHSA-1", ["Server.ServeConn"]), q))
        self.assertIsNone(check({"id": "GO-4", "affected": []}, q))


class ActionsTests(Sandbox):
    WORKFLOW = """jobs:
  a:
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: tj-actions/changed-files@v45.0.7
      - uses: "github/codeql-action/init@v3"
      - uses: ./local
      - uses: docker://alpine:3
      - uses: owner/pinned@3d3c42e5aac5ba805825da76410c181273ba90b1
      - uses: owner/branch@main
  b:
    uses: owner/repo/.github/workflows/x.yml@v1.2
"""

    def test_uses(self):
        self.assertEqual(actions.uses(self.WORKFLOW), [
            ("actions/checkout", "3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"),
            ("tj-actions/changed-files", "v45.0.7", ""), ("github/codeql-action", "v3", ""),
            ("owner/pinned", "3d3c42e5aac5ba805825da76410c181273ba90b1", ""), ("owner/branch", "main", ""),
            ("owner/repo", "v1.2", "")])

    def test_items(self):
        rows = [(("actions/checkout", "a" * 40, "v7.0.1"), "v7.0.1", ""),
                (("actions/download-artifact", "v4", ""), "v4",
                 "its tag moves to each new release; pin a release, or a commit with its tag in a comment"),
                (("o/a", "v4.1", ""), "v4.1",
                 "its tag moves to each new release; pin a release, or a commit with its tag in a comment"),
                (("owner/pinned", "a" * 40, ""), "", "it is pinned to a commit without a tag comment"),
                (("owner/branch", "main", ""), "main", "its ref is not a release tag")]
        for args, version, why in rows:
            with self.subTest(args=args):
                i = actions.item(*args)
                self.assertEqual((i.version, i.why, bool(i.queries)), (version, why, not why))
        i = actions.item("tj-actions/changed-files", "v45.0.7", "")
        q = i.queries[0]
        self.assertEqual(q, package_query("GitHub Actions", "tj-actions/changed-files"))
        record = {"affected": [{"package": {"ecosystem": "GitHub Actions", "name": "tj-actions/changed-files"},
                                "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "46.0.1"}]}]}]}
        self.assertIsNone(i.accept(record, q))
        self.assertEqual(actions.item("tj-actions/changed-files", "v46.0.1", "").accept(record, q), "")

    def test_workflows_of_the_repository_and_of_the_base(self):
        self.write("dotkit/.github/workflows/ci.yml", "      - uses: a/b@v1\n")
        self.write("dotkit/templates/default/.github/workflows/audit.yml", "      - uses: c/d@v2\n")
        self.write("repo/.github/workflows/audit.yml", "      - uses: e/f@v3\n")
        c = self.ctx({})
        self.assertEqual([i.name for i in actions.collect(c).items], ["e/f"])
        env = Env(repo=self.dir / "dotkit/templates/default", dotkit=self.dir / "dotkit", home=self.dir / "home")
        self.assertEqual([i.name for i in actions.collect(sources.Context(env, self.dir)).items], ["c/d", "a/b"])


if __name__ == "__main__":
    unittest.main()
