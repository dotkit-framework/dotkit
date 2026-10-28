"""The decisions of the audit: which OSV queries a tool gets, and which advisories are findings.

Nothing here runs a command, so the tests are tables. inventory.py collects the tools, osv.py asks OSV.

OSV has no ecosystem for Nix and Homebrew. A tool from a Git repository is a query of the GIT ecosystem:
the repository URL and the tag of the release, or the commit. OSV compares the repository URL as it
stores it, in lowercase, so the queries use lowercase URLs. A package of npm, PyPI, crates.io, Go, or
RubyGems is a query of its own ecosystem; there OSV also has the malicious releases (MAL-*).
"""

from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

# The sources of a tool, in the order of the report.
SOURCES = ("nix", "brew", "mise", "dotkit")
SOURCE_NAMES = {"nix": "Nix", "brew": "Homebrew", "mise": "mise", "dotkit": "dotkit"}


@dataclass
class Item:
    """One tool at one version, and its OSV queries. An item without queries is not checked: why says
    the reason."""

    source: str  # a key of SOURCES
    name: str  # as the report shows it: fzf, npm:cowsay, prompt_toolkit
    version: str
    queries: List[dict] = field(default_factory=list)
    # The CVE IDs that a Nixpkgs patch of the package fixes (the patch file names have them).
    patched: FrozenSet[str] = frozenset()
    why: str = ""


@dataclass
class Advisory:
    id: str
    aliases: List[str]
    summary: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW, or "" (unknown)
    fixed: List[str]  # the versions with the fix, when OSV gives versions (not commits)
    malicious: bool = False

    @property
    def ids(self) -> FrozenSet[str]:
        return frozenset([self.id, *self.aliases])

    @property
    def url(self) -> str:
        return f"https://osv.dev/vulnerability/{self.id}"


@dataclass
class Finding:
    item: Item
    advisory: Advisory

    @property
    def key(self) -> str:
        """The same finding in each run, also after an upgrade to another version that has it."""
        return f"{self.item.source}/{self.item.name}/{self.advisory.id}"

    @property
    def keys(self) -> List[str]:
        """key, then the keys of the aliases. When OSV adds an alias that sorts first, the ID of the
        finding changes; an issue or a notification of an alias key is the same finding."""
        return [self.key] + [f"{self.item.source}/{self.item.name}/{i}" for i in sorted(self.advisory.ids)
                             if i != self.advisory.id]

    @property
    def tool_key(self) -> str:
        return f"{self.item.source}/{self.item.name}"


# --- The queries


def git_query(repo: str, version: str) -> dict:
    return {"package": {"ecosystem": "GIT", "name": repo.lower()}, "version": version}


def package_query(ecosystem: str, name: str, version: str) -> dict:
    return {"package": {"ecosystem": ecosystem, "name": name}, "version": version}


HEX40 = re.compile(r"[0-9a-f]{40}")
# The repository of a source URL: https://github.com/OWNER/REPO/archive/..., .../releases/download/TAG/...,
# https://github.com/OWNER/REPO.git
_HOSTED = re.compile(r"https://(github\.com|gitlab\.com|codeberg\.org)/([^/]+)/([^/?#]+?)(?:\.git)?(?:[/?#]|$)")
# GitLab archives by the API: https://gitlab.com/api/v4/projects/OWNER%2FREPO/repository/archive.tar.gz?sha=REF
_GITLAB_API = re.compile(r"https://gitlab\.com/api/v4/projects/([^/]+)%2[Ff]([^/]+)/repository/archive[^?]*\?sha=([^&]+)")
# The ref in an archive URL: .../archive/refs/tags/REF.tar.gz or .../archive/REF.tar.gz
_ARCHIVE = re.compile(r"/archive/(?:refs/tags/)?(.+?)\.(?:tar\.gz|tar\.bz2|tar\.xz|tgz|zip)$")
_RELEASE = re.compile(r"/releases/download/([^/]+)/")

# Packages whose source is not on a Git host that OSV knows, by their Nixpkgs pname: the mirror that OSV
# has, and the tag of a version. Each one returns advisories for an old version.
KNOWN_REPOS: Dict[str, Tuple[str, str]] = {
    "git": ("https://github.com/git/git", "v{v}"),
    "nix": ("https://github.com/nixos/nix", "{v}"),
    "zsh": ("https://github.com/zsh-users/zsh", "zsh-{v}"),
    "gnupg": ("https://github.com/gpg/gnupg", "gnupg-{v}"),
    "openssl": ("https://github.com/openssl/openssl", "openssl-{v}"),
    "curl": ("https://github.com/curl/curl", "curl-{v_}"),
    "python3": ("https://github.com/python/cpython", "v{v}"),
    "nodejs": ("https://github.com/nodejs/node", "v{v}"),
    "neovim": ("https://github.com/neovim/neovim", "v{v}"),
}


def _tag(template: str, version: str) -> str:
    return template.format(v=version, v_=version.replace(".", "_"))


def nix_item(pkg: dict) -> Optional[Item]:
    """The item of a Nix package (inventory.NIX_EXPR gives the fields). None for a package without a
    version, such as a wrapper script of the configuration."""
    name, version = pkg.get("name") or "", pkg.get("version") or ""
    if not name or not version:
        return None
    patched = frozenset(m.upper() for p in pkg.get("patches") or [] for m in re.findall(r"CVE-\d{4}-\d+", p, re.I))
    item = Item("nix", name, version, patched=patched)
    if name in KNOWN_REPOS:
        repo, template = KNOWN_REPOS[name]
        item.queries = [git_query(repo, _tag(template, version))]
        return item
    url = pkg.get("url") or pkg.get("git") or ""
    ref = pkg.get("tag") or pkg.get("rev") or ""
    if ref.startswith("refs/tags/"):
        ref = ref[len("refs/tags/"):]
    m = _GITLAB_API.match(url)
    if m:
        repo, ref = f"https://gitlab.com/{m[1]}/{m[2]}", ref or m[3]
    else:
        m = _HOSTED.match(url) or _HOSTED.match(pkg.get("homepage") or "")
        if not m:
            item.why = "its source is not in a Git repository that OSV knows"
            return item
        repo = f"https://{m[1]}/{m[2]}/{m[3]}"
        if not ref:
            r = _RELEASE.search(url) or _ARCHIVE.search(url)
            ref = r[1] if r else ""
    if HEX40.fullmatch(ref):
        item.queries = [{"commit": ref}]
    elif ref:
        item.queries = [git_query(repo, ref)]
    else:
        # Only the homepage: the usual tags of a version.
        item.queries = [git_query(repo, f"v{version}"), git_query(repo, version)]
    return item


# The mise backends with an OSV ecosystem, and the ones that are a GitHub repository.
ECOSYSTEMS = {"npm": "npm", "pipx": "PyPI", "pypi": "PyPI", "cargo": "crates.io", "go": "Go", "gem": "RubyGems"}
GITHUB_BACKENDS = ("github", "aqua", "ubi")
# The core tools of mise: the repository and the tag of a version. Go is the stdlib package of the Go
# ecosystem. OSV has no advisories for the tags of Ruby.
MISE_CORE: Dict[str, Tuple[str, str]] = {
    "node": ("https://github.com/nodejs/node", "v{v}"),
    "python": ("https://github.com/python/cpython", "v{v}"),
    "bun": ("https://github.com/oven-sh/bun", "bun-v{v}"),
    "deno": ("https://github.com/denoland/deno", "v{v}"),
}
# Registry tools whose advisories are in a package ecosystem that the mise registry does not name.
MISE_ALIASES = {
    "claude-code": "npm:@anthropic-ai/claude-code",
    "codex": "npm:@openai/codex",
    "gemini-cli": "npm:@google/gemini-cli",
}


def backend_queries(spec: str, version: str) -> List[dict]:
    """The queries of a mise backend spec (npm:cowsay, github:owner/repo); [] for the other backends."""
    backend, _, name = spec.partition(":")
    # A spec can have options in brackets (github:o/r[exe=x]) or a version (npm:x@1).
    name = name.split("[", 1)[0]
    if backend in ECOSYSTEMS and name:
        if name[1:].find("@") > 0:
            name = name[:name.rindex("@")]
        return [package_query(ECOSYSTEMS[backend], name, version)]
    if backend in GITHUB_BACKENDS and re.fullmatch(r"[\w.-]+/[\w.-]+", name):
        repo = f"https://github.com/{name}"
        # mise drops the v of a tag: query both forms; only one is a tag of the repository.
        return [git_query(repo, f"v{version}"), git_query(repo, version)]
    return []


def mise_item(key: str, version: str, backends: Sequence[str] = ()) -> Item:
    """The item of a mise tool: KEY of the [tools] table, at VERSION. backends: what `mise registry KEY`
    prints, for a key without a backend."""
    item = Item("mise", key, version)
    if key == "go":
        item.queries = [package_query("Go", "stdlib", version)]
    elif key in MISE_CORE:
        repo, template = MISE_CORE[key]
        item.queries = [git_query(repo, _tag(template, version))]
    elif ":" in key:
        item.queries = backend_queries(key, version)
    else:
        specs = [MISE_ALIASES[key]] if key in MISE_ALIASES else []
        specs += [b for b in backends if not b.startswith(("asdf:", "vfox:"))]
        seen: List[dict] = []
        for spec in specs:
            for q in backend_queries(spec, version):
                if q not in seen:
                    seen.append(q)
        item.queries = seen
    if not item.queries:
        item.why = "OSV has no data for its source"
    return item


def vendored_items(text: str) -> List[Item]:
    """The packages of lib/dotkit/_vendor/vendor.txt (NAME==VERSION --hash=...), from PyPI."""
    out = []
    for line in text.split("\n"):
        m = re.match(r"\s*([A-Za-z0-9_.-]+)==([^\s;]+)", line)
        if m:
            out.append(Item("dotkit", m[1], m[2], [package_query("PyPI", m[1], m[2])]))
    return out


def flake_items(lock: dict) -> List[Item]:
    """The inputs of a flake.lock from GitHub, by their locked commit: the zsh plugins, nix-darwin, Home
    Manager, and Nixpkgs."""
    out = []
    nodes = lock.get("nodes") if isinstance(lock, dict) else None
    for name, node in sorted((nodes or {}).items()):
        locked = node.get("locked") if isinstance(node, dict) else None
        if not isinstance(locked, dict) or locked.get("type") != "github":
            continue
        rev = locked.get("rev")
        if isinstance(rev, str) and HEX40.fullmatch(rev):
            out.append(Item("dotkit", f"input {name}", rev[:12], [{"commit": rev}]))
    return out


# --- The advisories


_CVSS3 = {
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2},
    "AC": {"L": 0.77, "H": 0.44},
    "UI": {"N": 0.85, "R": 0.62},
    "C": {"H": 0.56, "L": 0.22, "N": 0.0},
}
_PR = {"U": {"N": 0.85, "L": 0.62, "H": 0.27}, "C": {"N": 0.85, "L": 0.68, "H": 0.5}}


def _roundup(x: float) -> float:
    """The Roundup of CVSS 3.1: the smallest number with one decimal that is x or more."""
    n = round(x * 100000)
    return n / 100000.0 if n % 10000 == 0 else (math.floor(n / 10000) + 1) / 10.0


def cvss3_score(vector: str) -> Optional[float]:
    """The base score of a CVSS 3.0 or 3.1 vector; None when the vector is not one."""
    if not vector.startswith(("CVSS:3.0/", "CVSS:3.1/")):
        return None
    m = dict(part.split(":", 1) for part in vector.split("/")[1:] if ":" in part)
    try:
        scope = m["S"]
        av, ac, ui = _CVSS3["AV"][m["AV"]], _CVSS3["AC"][m["AC"]], _CVSS3["UI"][m["UI"]]
        pr = _PR[scope][m["PR"]]
        c, i, a = (_CVSS3["C"][m[k]] for k in ("C", "I", "A"))
    except KeyError:
        return None
    iss = 1 - (1 - c) * (1 - i) * (1 - a)
    impact = 6.42 * iss if scope == "U" else 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    if impact <= 0:
        return 0.0
    exploit = 8.22 * av * ac * pr * ui
    total = impact + exploit if scope == "U" else 1.08 * (impact + exploit)
    return _roundup(min(total, 10))


def rating(score: float) -> str:
    if score >= 9:
        return "CRITICAL"
    if score >= 7:
        return "HIGH"
    if score >= 4:
        return "MEDIUM"
    return "LOW" if score > 0 else ""


def severity(record: dict) -> str:
    """The severity of an OSV record: the one of its database (GitHub: LOW, MODERATE, HIGH, CRITICAL),
    else the rating of its CVSS 3 vector. "" when it has neither, for example only a CVSS 4 vector."""
    text = (record.get("database_specific") or {}).get("severity")
    if isinstance(text, str) and text.upper() in ("LOW", "MODERATE", "MEDIUM", "HIGH", "CRITICAL"):
        return "MEDIUM" if text.upper() == "MODERATE" else text.upper()
    for s in record.get("severity") or []:
        score = cvss3_score(s.get("score") or "") if isinstance(s, dict) else None
        if score is not None:
            return rating(score)
    return ""


def _fixed(record: dict, query: dict) -> List[str]:
    """The versions with the fix, for the package of the query. GIT ranges have commits: none."""
    pkg = query.get("package") or {}
    out: List[str] = []
    for a in record.get("affected") or []:
        p = a.get("package") or {}
        if p.get("ecosystem") != pkg.get("ecosystem") or p.get("name") != pkg.get("name"):
            continue
        for r in a.get("ranges") or []:
            if r.get("type") == "GIT":
                continue
            out += [e["fixed"] for e in r.get("events") or [] if isinstance(e, dict) and "fixed" in e]
    return list(dict.fromkeys(out))


def advisory(record: dict, query: dict) -> Advisory:
    aliases = [a for a in record.get("aliases") or [] if isinstance(a, str)]
    summary = record.get("summary") or (record.get("details") or "").strip().split("\n", 1)[0]
    rid = record.get("id") or ""
    malicious = rid.startswith("MAL-")
    sev = "CRITICAL" if malicious else severity(record)
    return Advisory(rid, aliases, " ".join(summary.split())[:200], sev, _fixed(record, query), malicious)


# --- The ignore rules of audit.toml


@dataclass
class Rule:
    id: str
    reason: str
    tool: str = ""
    until: Optional[datetime.date] = None
    file: str = ""

    def matches(self, f: Finding) -> bool:
        return self.id in f.advisory.ids and (not self.tool or self.tool == f.item.name)


def rules(data: dict, file: str) -> Tuple[List[Rule], List[str]]:
    """The [[ignore]] entries of an audit.toml, and the errors of the ones that are not valid."""
    out, errors = [], []
    entries = data.get("ignore", [])
    if not isinstance(entries, list):
        return [], [f"{file}: ignore must be a list of tables: [[ignore]]."]
    for n, e in enumerate(entries, 1):
        if not isinstance(e, dict):
            errors.append(f"{file}: ignore {n} is not a table.")
            continue
        rid, reason, tool, until = e.get("id"), e.get("reason"), e.get("tool", ""), e.get("until")
        if not isinstance(rid, str) or not rid:
            errors.append(f"{file}: ignore {n} has no id.")
        elif not isinstance(reason, str) or not reason.strip():
            errors.append(f"{file}: the ignore of {rid} has no reason.")
        elif not isinstance(tool, str):
            errors.append(f"{file}: the tool of the ignore of {rid} is not a string.")
        elif until is not None and type(until) is not datetime.date:
            errors.append(f"{file}: the until of the ignore of {rid} is not a date, such as 2026-12-31.")
        else:
            out.append(Rule(rid, reason.strip(), tool, until, file))
    return out, errors


# --- The findings


def _merge(advisories: Iterable[Advisory]) -> List[Advisory]:
    """One advisory per vulnerability: an advisory whose ID or alias another one has goes into it."""
    out: List[Advisory] = []
    for a in advisories:
        same = next((b for b in out if b.ids & a.ids), None)
        if same is None:
            out.append(a)
            continue
        same.aliases = sorted((same.ids | a.ids) - {same.id})
        same.severity = same.severity or a.severity
        same.fixed = same.fixed or a.fixed
        same.malicious = same.malicious or a.malicious
    return out


@dataclass
class Result:
    findings: List[Finding] = field(default_factory=list)
    # (finding, rule) for each advisory that a rule of audit.toml ignores.
    ignored: List[Tuple[Finding, Rule]] = field(default_factory=list)
    # The advisories that a Nixpkgs patch fixes.
    patched: List[Finding] = field(default_factory=list)
    # The rules whose until date has passed.
    expired: List[Rule] = field(default_factory=list)


def findings(items: Sequence[Item], ids: Sequence[List[str]], records: Dict[str, dict], rules_: Sequence[Rule],
             today: datetime.date, more: Sequence[Finding] = ()) -> Result:
    """The findings of the items. ids: the advisory IDs of each query, in the order of the queries of the
    items. records: the OSV record of each ID. more: the findings that came without OSV queries (brew
    vulns), for the ignore rules."""
    result = Result()
    active = [r for r in rules_ if r.until is None or r.until >= today]
    result.expired = [r for r in rules_ if r.until is not None and r.until < today]
    found: List[Finding] = []
    n = 0
    for item in items:
        advisories = []
        for q in item.queries:
            advisories += [advisory(records[i], q) for i in ids[n] if i in records]
            n += 1
        found += [Finding(item, a) for a in _merge(advisories)]
    for f in [*found, *more]:
        rule = next((r for r in active if r.matches(f)), None)
        if rule is not None:
            result.ignored.append((f, rule))
        elif f.advisory.ids & f.item.patched:
            result.patched.append(f)
        else:
            result.findings.append(f)
    return result
