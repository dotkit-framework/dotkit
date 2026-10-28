"""The data of the audit, which the sources make and the core reads: Item, Part, Advisory, Finding.

A source (sources/) collects the tools of one kind and returns a Part: items with OSV queries, the
findings that it made itself (brew vulns asks OSV on its own), and an error or the reason that it does not
apply here. The core (cli.py) asks OSV about the queries of all parts at once, and match.py decides.

A source can also say what an upgrade installs: upgrades(ctx, items) -> one Item or None per item, the
same tool at the version of the upgrade, with its queries. The core asks OSV about those too: a finding
whose advisory the upgrade does not have has a fix available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Optional

# A check of an advisory that only the source can make (a version range, the functions of a binary):
# None keeps the advisory; "" drops it, because it does not apply; other text drops it and says why, for
# the summary of the report.
Accept = Callable[[dict, dict], Optional[str]]


def git_query(repo: str, version: str) -> dict:
    """A query of the GIT ecosystem. OSV stores the repository URLs in lowercase and compares them as
    they are, so the URL is in lowercase."""
    return {"package": {"ecosystem": "GIT", "name": repo.lower()}, "version": version}


def package_query(ecosystem: str, name: str, version: str = "") -> dict:
    """A query of a package ecosystem; without a version, all advisories of the package."""
    q: dict = {"package": {"ecosystem": ecosystem, "name": name}}
    if version:
        q["version"] = version
    return q


@dataclass
class Item:
    """One tool or package at one version, and its OSV queries. An item without queries is not checked:
    why says the reason."""

    source: str  # the NAME of its source module
    name: str  # as the report shows it: fzf, npm:cowsay, mise > zstd-safe
    version: str
    queries: List[dict] = field(default_factory=list)
    # The CVE IDs that a Nixpkgs patch of the package fixes (the patch file names have them).
    patched: FrozenSet[str] = frozenset()
    why: str = ""
    # What the report shows after the version, instead of the label of the source: "Rust crate in mise 2026.10.17".
    where: str = ""
    # The tool that has the package: the name that an ignore rule with `tool` can also give.
    parent: str = ""
    accept: Optional[Accept] = None
    # What only the source reads, for example the arguments of `mise latest` for its upgrades().
    meta: Dict[str, object] = field(default_factory=dict)


@dataclass
class Part:
    """What a source found."""

    items: List[Item] = field(default_factory=list)
    findings: List["Finding"] = field(default_factory=list)
    # The tools that the source checked itself, for the count of the report (brew vulns).
    checked: int = 0
    # A failure: the part is not checked, and the audit is not complete.
    error: str = ""
    # The reason that the source does not apply here, for example in CI: "the mise tools are not installed".
    skipped: str = ""


@dataclass
class Advisory:
    id: str
    aliases: List[str]
    summary: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW, or "" (unknown)
    fixed: List[str]  # the versions with the fix, when OSV gives versions (not commits)
    malicious: bool = False
    score: Optional[float] = None  # the CVSS base score, when the advisory has a vector

    @property
    def ids(self) -> FrozenSet[str]:
        return frozenset([self.id, *self.aliases])

    @property
    def url(self) -> str:
        return f"https://osv.dev/vulnerability/{self.id}"


# Finding.fix: an upgrade installs a version without the advisory; no version that an upgrade installs
# is without it (a fix is not released, or the source does not have it yet); or not known.
FIX_AVAILABLE, FIX_NONE, FIX_UNKNOWN = "available", "none", ""


@dataclass
class Finding:
    item: Item
    advisory: Advisory
    fix: str = FIX_UNKNOWN
    # The version that an upgrade installs, when the source knows it.
    upgrade: str = ""
    # In the catalog of known exploited vulnerabilities of CISA (KEV).
    exploited: bool = False

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
