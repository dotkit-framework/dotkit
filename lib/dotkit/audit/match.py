"""The decisions of the audit: which advisories of OSV are findings, their severity, and the ignore rules.

Nothing here runs a command, so the tests are tables. The sources (sources/) collect the tools and make
their queries, osv.py asks OSV.
"""

from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import cvss4
from .model import Advisory, Finding, Item

HEX40 = re.compile(r"[0-9a-f]{40}")

# --- The advisories


def notice(record: dict) -> str:
    """The kind of a notice that is not a vulnerability: "unmaintained", "unsound", or "notice" (RustSec
    has them as `informational`); "" for a vulnerability."""
    for a in record.get("affected") or []:
        info = (a.get("database_specific") or {}).get("informational") if isinstance(a, dict) else None
        if isinstance(info, str) and info:
            return info
    info = (record.get("database_specific") or {}).get("informational")
    return info if isinstance(info, str) else ""


def version_key(v: str) -> tuple:
    """A version for comparison: the numbers in order, without zeros at the end (47.1 is 47.1.0); a
    pre-release (1.0.0-rc.1) before its release."""
    v = v[1:] if v[:1] in ("v", "V") else v
    main, _, pre = v.partition("-")
    nums = [int(n) for n in re.findall(r"\d+", main)]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums), 0 if pre else 1, pre


def _affected_of(record: dict, query: dict) -> List[dict]:
    pkg = query.get("package") or {}
    eco, name = pkg.get("ecosystem"), (pkg.get("name") or "").lower()
    return [a for a in record.get("affected") or [] if isinstance(a, dict)
            and (a.get("package") or {}).get("ecosystem") == eco and ((a.get("package") or {}).get("name") or "").lower() == name]


def in_ranges(record: dict, query: dict, version: str) -> bool:
    """True when VERSION is in a range of the record for the package of the query: introduced, then fixed
    (not included) or last_affected (included), or in its list of versions. For an ecosystem where OSV
    does not compare versions itself (GitHub Actions)."""
    key = version_key(version)
    for a in _affected_of(record, query):
        if version in (a.get("versions") or []):
            return True
        for r in a.get("ranges") or []:
            if r.get("type") not in ("ECOSYSTEM", "SEMVER"):
                continue
            start = None
            for e in r.get("events") or []:
                if "introduced" in e:
                    start = version_key(e["introduced"])
                elif start is not None and "fixed" in e:
                    if start <= key < version_key(e["fixed"]):
                        return True
                    start = None
                elif start is not None and "last_affected" in e:
                    if start <= key <= version_key(e["last_affected"]):
                        return True
                    start = None
            if start is not None and start <= key:
                return True
    return False


def has_fix(record: dict, query: dict) -> bool:
    """True when the record names a version with the fix, or the last affected one, for the package of
    the query. A record of Bitnami without one has every version affected, also the fixed ones."""
    return any("fixed" in e or "last_affected" in e for a in _affected_of(record, query)
               for r in a.get("ranges") or [] for e in r.get("events") or [] if isinstance(e, dict))


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


def cvss_score(record: dict) -> Optional[float]:
    """The CVSS base score of an OSV record: of its CVSS 4.0 vector, else of its CVSS 3 vector."""
    vectors = [s.get("score") for s in record.get("severity") or [] if isinstance(s, dict)]
    vectors = [v for v in vectors if isinstance(v, str)]
    for v in vectors:
        score = cvss4.score(v)
        if score is not None:
            return score
    for v in vectors:
        score = cvss3_score(v)
        if score is not None:
            return score
    return None


def severity(record: dict) -> str:
    """The severity of an OSV record: the one of its database (GitHub: LOW, MODERATE, HIGH, CRITICAL),
    else the rating of its CVSS score. "" when it has neither."""
    text = (record.get("database_specific") or {}).get("severity")
    if isinstance(text, str) and text.upper() in ("LOW", "MODERATE", "MEDIUM", "HIGH", "CRITICAL"):
        return "MEDIUM" if text.upper() == "MODERATE" else text.upper()
    score = cvss_score(record)
    return rating(score) if score is not None else ""


def severity_text(a: Advisory) -> str:
    """For the report and the issues: "high 7.5", "high" (no CVSS vector), "low 2.0", "none 0.0", or
    "unknown"."""
    word = a.severity.lower() or ("none" if a.score == 0 else "unknown")
    return word if a.score is None else f"{word} {a.score:.1f}"


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
    return Advisory(rid, aliases, " ".join(summary.split())[:200], sev, _fixed(record, query), malicious,
                    cvss_score(record))


# --- The ignore rules of audit.toml


@dataclass
class Rule:
    id: str
    reason: str
    tool: str = ""
    until: Optional[datetime.date] = None
    file: str = ""

    def matches(self, f: Finding) -> bool:
        """The same ID or alias, and the tool of the rule, when it has one: the name of the item, or the
        tool that has the package (mise for mise > zstd-safe)."""
        return self.id in f.advisory.ids and (not self.tool or self.tool in (f.item.name, f.item.parent))


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
        same.score = same.score if same.score is not None else a.score
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
    # (finding, kind) for each notice: unmaintained, unsound.
    notices: List[Tuple[Finding, str]] = field(default_factory=list)
    # (finding, reason) for each advisory that the check of its source left out.
    left_out: List[Tuple[Finding, str]] = field(default_factory=list)
    # The rules whose until date has passed.
    expired: List[Rule] = field(default_factory=list)


def findings(items: Sequence[Item], ids: Sequence[List[str]], records: Dict[str, dict], rules_: Sequence[Rule],
             today: datetime.date, more: Sequence[Finding] = ()) -> Result:
    """The findings of the items. ids: the advisory IDs of each query, in the order of the queries of the
    items. records: the OSV record of each ID. more: the findings that came without OSV queries (brew
    vulns), for the ignore rules. A withdrawn record is left out."""
    result = Result()
    active = [r for r in rules_ if r.until is None or r.until >= today]
    result.expired = [r for r in rules_ if r.until is not None and r.until < today]
    found: List[Tuple[Finding, str]] = []
    n = 0
    for item in items:
        advisories, why = [], {}
        for q in item.queries:
            for i in ids[n]:
                record = records.get(i)
                if record is None or record.get("withdrawn"):
                    continue
                verdict = item.accept(record, q) if item.accept else None
                if verdict == "":
                    continue
                a = advisory(record, q)
                advisories.append(a)
                why[a.id] = verdict or ("notice:" + notice(record) if notice(record) else "")
            n += 1
        for a in _merge(advisories):
            # The verdict of a vulnerability: of any of its records (a GO record decides for its GHSA alias).
            verdict = next((why[i] for i in sorted(a.ids) if why.get(i)), "")
            found.append((Finding(item, a), verdict))
    for f, why_ in [*found, *((f, "") for f in more)]:
        rule = next((r for r in active if r.matches(f)), None)
        if rule is not None:
            result.ignored.append((f, rule))
        elif f.advisory.ids & f.item.patched:
            result.patched.append(f)
        elif why_.startswith("notice:"):
            result.notices.append((f, why_[len("notice:"):]))
        elif why_:
            result.left_out.append((f, why_))
        else:
            result.findings.append(f)
    return result
