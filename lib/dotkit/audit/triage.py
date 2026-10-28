"""Which findings the report lists: the ones that you can act on (BEHAVIOR.md, row Au16).

- A malicious release, and a vulnerability that attackers use (the KEV catalog of CISA): always.
- A finding of a tool of the configuration whose fix an upgrade installs, or whose fix is not known.
- Counted only: a finding that no upgrade fixes yet (no fixed release, or the source does not have it), a
  finding in the packages inside a tool (an upgrade of the tool fixes it), and one below --min-severity.

--all lists every finding. The notification and the GitHub issues follow the same list.
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence

from . import match, sources
from .model import FIX_AVAILABLE, FIX_NONE, Finding, Item

RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "": 4}
# --min-severity: the highest RANK that is listed. An unknown severity counts as low.
LEVELS = {"critical": 0, "high": 1, "medium": 2, "low": 4}


def rank(f: Finding) -> int:
    """The order of the report: malicious, then known exploited, then by severity."""
    if f.advisory.malicious:
        return -2
    return -1 if f.exploited else RANK[f.advisory.severity]


def mark_exploited(findings: Sequence[Finding], kev: FrozenSet[str]) -> None:
    for f in findings:
        f.exploited = bool(f.advisory.ids & kev)


def check_fixes(ctx, findings: Sequence[Finding], ask: Callable[[List[Item]], match.Result]) -> bool:
    """Sets fix and upgrade of the findings of the tools of the configuration, from the upgrades() of their
    source: the same tool at the version that an upgrade installs. ask: the advisories of items, from OSV.
    A finding whose advisory the upgrade does not have has a fix available. Returns False when there was
    nothing to check."""
    wanted: Dict[str, Dict[int, Item]] = {}
    for f in findings:
        module = sources.MODULES[f.item.source]
        if f.fix or f.item.source in sources.DEPENDENCIES or not hasattr(module, "upgrades"):
            continue
        wanted.setdefault(f.item.source, {})[id(f.item)] = f.item
    if not wanted:
        return False
    target: Dict[int, Optional[Item]] = {}
    for source, items in wanted.items():
        listed = list(items.values())
        for i, t in zip(listed, sources.MODULES[source].upgrades(ctx, listed)):
            target[id(i)] = t
    asked = [t for t in target.values() if t is not None and t.queries]
    still: Dict[int, FrozenSet[str]] = {}
    for g in ask(asked).findings if asked else []:
        still[id(g.item)] = still.get(id(g.item), frozenset()) | g.advisory.ids
    for f in findings:
        t = target.get(id(f.item))
        if t is None or not t.queries:
            continue
        f.upgrade = t.version
        f.fix = FIX_NONE if f.advisory.ids & still.get(id(t), frozenset()) else FIX_AVAILABLE
    return True


def one_per_key(findings: List[Finding]) -> List[Finding]:
    """One finding per key: a tool with an advisory at two versions (node = ["22", "20"], a crate twice in
    a binary) is one finding with both versions, so it has one issue."""
    out: Dict[str, Finding] = {}
    for f in findings:
        same = out.get(f.key)
        if same is None:
            out[f.key] = f
        elif f.item.version not in same.item.version.split(", "):
            versions = sorted([*same.item.version.split(", "), f.item.version])
            out[f.key] = dataclasses.replace(same, item=dataclasses.replace(same.item, version=", ".join(versions)))
    return list(out.values())


@dataclasses.dataclass
class Listed:
    """The findings that the report lists, and the ones that it only counts."""

    findings: List[Finding]
    no_fix: List[Finding]  # no upgrade fixes them yet
    dependencies: List[Finding]  # in the packages inside a tool
    below: List[Finding]  # below --min-severity

    @property
    def counted(self) -> List[Finding]:
        return self.no_fix + self.dependencies + self.below


def select(findings: List[Finding], all_: bool = False, min_severity: str = "") -> Listed:
    out = Listed([], [], [], [])
    limit = LEVELS.get(min_severity, LEVELS["low"])
    for f in findings:
        if f.advisory.malicious or f.exploited:
            out.findings.append(f)
        elif rank(f) > limit:
            out.below.append(f)
        elif all_:
            out.findings.append(f)
        elif f.item.source in sources.DEPENDENCIES:
            out.dependencies.append(f)
        elif f.fix == FIX_NONE:
            out.no_fix.append(f)
        else:
            out.findings.append(f)
    return out
