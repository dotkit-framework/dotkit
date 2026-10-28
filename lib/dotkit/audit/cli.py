"""dotkit audit: the report of the known vulnerabilities and malicious releases (BEHAVIOR.md, rows Au).

The findings go to stdout, the summary to stderr. --json prints the report as JSON for CI. --issues
syncs one GitHub issue per finding (issues.py). --notify shows a macOS notification for the findings
that the last notified run did not have; the launchd agent of nix/darwin.nix runs it.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
from pathlib import Path
from typing import Collection, Dict, List, Optional

from ..core import proc, ui
from ..core.env import Env
from . import kev, match, osv, sources, triage
from .model import FIX_AVAILABLE, FIX_NONE
from .model import Finding

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 and 3.10: tomli of dotkit/_vendor, the same parser.
    import tomli as tomllib

ROLE = {"CRITICAL": "error", "HIGH": "error", "MEDIUM": "warn", "LOW": "dim", "": "dim"}


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def load_rules(env: Env) -> List[match.Rule]:
    """The ignore rules of audit.toml in dotkit and in the repository. An error in a file stops the audit."""
    out: List[match.Rule] = []
    errors: List[str] = []
    for root, label in ((env.dotkit, "audit.toml of dotkit"), (env.repo, "audit.toml")):
        path = root / "audit.toml"
        try:
            data = tomllib.loads(path.read_bytes().decode("utf-8"))
        except FileNotFoundError:
            continue
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            raise ui.die(f"{path} is not valid TOML: {e}.") from None
        rules, errs = match.rules(data, label)
        out += rules
        errors += errs
    if errors:
        raise ui.die("\n".join(errors) + "\nFix audit.toml, then run dotkit audit again.")
    return out


def _finding_json(f: Finding) -> dict:
    a = f.advisory
    return {"key": f.key, "source": f.item.source, "tool": f.item.name, "version": f.item.version,
            "where": f.item.where, "id": a.id,
            "aliases": a.aliases, "summary": a.summary, "severity": a.severity, "score": a.score, "fixed": a.fixed,
            "malicious": a.malicious, "exploited": f.exploited, "fix": f.fix or "unknown", "upgrade": f.upgrade,
            "url": a.url}


def _state_path(env: Env) -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(env.home / ".local/state")
    return Path(state) / "dotkit" / "audit.json"


def notify(env: Env, findings: List[Finding], keep: Collection[str] = ()) -> None:
    """A macOS notification for the findings that the last run did not have (by any key of an alias). The
    state file gets the keys of the current findings, so a finding that is gone and comes back is new
    again. keep: the sources, the tool keys, and the finding keys that this run did not check or did not
    show; their old keys stay."""
    path = _state_path(env)
    try:
        seen = set(json.loads(path.read_text(encoding="utf-8")).get("seen") or [])
    except (OSError, ValueError, AttributeError):
        seen = set()
    new = [f for f in findings if not any(k in seen for k in f.keys)]
    if new:
        tools = list(dict.fromkeys(f.item.name for f in new))
        text = (f"{_plural(len(new), 'new finding', 'new findings')}: {', '.join(tools[:3])}"
                f"{', ...' if len(tools) > 3 else ''}. Run dotkit audit.")
        if any(f.advisory.malicious for f in new):
            text = "A malicious release! " + text
        proc.run(["/usr/bin/osascript", "-e", "on run argv", "-e",
                  "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run",
                  "dotkit audit", text], stdout=proc.DEVNULL)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    kept = {k for k in seen if k in keep or k.split("/", 1)[0] in keep or k.rsplit("/", 1)[0] in keep}
    current = {k for f in findings for k in f.keys}
    tmp.write_text(json.dumps({"seen": sorted(kept | current)}) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _print_findings(findings: List[Finding]) -> None:
    by_item: Dict[int, List[Finding]] = {}
    for f in findings:
        by_item.setdefault(id(f.item), []).append(f)
    first = True
    for group in by_item.values():
        item = group[0].item
        if not first:
            ui.out()
        first = False
        version = f" {item.version}" if item.version else ""
        ui.out(f"{ui.paint(item.name, 'name')}{version} ({item.where}):")
        for f in group:
            a = f.advisory
            kind = ui.paint("MALICIOUS", "error") if a.malicious else ui.paint(match.severity_text(a), ROLE[a.severity])
            if f.exploited:
                kind += ", " + ui.paint("known exploited", "error")
            ui.out(f"  {a.id} ({kind}): {a.summary or 'no summary'}")
            extra = f"Fixed in {', '.join(a.fixed)}. " if a.fixed else ""
            if a.malicious:
                extra += "Remove the tool, or pin a version without this release. "
            elif f.fix == FIX_AVAILABLE and f.upgrade:
                extra += f"An upgrade installs {f.upgrade}, which has the fix. "
            elif f.fix == FIX_NONE:
                extra += "No upgrade fixes it yet. "
            ui.out(f"    {extra}{ui.paint(a.url, 'dim')}")


def _ask(items: List, parts_findings: List[Finding], rules: List[match.Rule], today: datetime.date) -> match.Result:
    """One OSV query per distinct query of the items (a crate can be in many binaries), then the advisories."""
    if not items:
        return match.findings([], [], {}, rules, today, parts_findings)
    index: Dict[str, int] = {}
    unique: List[dict] = []
    keys = []
    for i in items:
        for q in i.queries:
            k = json.dumps(q, sort_keys=True)
            if k not in index:
                index[k] = len(unique)
                unique.append(q)
            keys.append(k)
    answers = osv.query(unique) if unique else []
    modified: Dict[str, str] = {}
    for answer in answers:
        modified.update(answer)
    records = osv.records(modified, osv.cache_dir())
    ids = [[i for i, _ in answers[index[k]]] for k in keys]
    return match.findings(items, ids, records, rules, today, parts_findings)


def _count_by_severity(findings: List[Finding]) -> str:
    counts: Dict[str, int] = {}
    for f in findings:
        word = f.advisory.severity.lower() if f.advisory.severity in ("CRITICAL", "HIGH", "MEDIUM") else "low or unknown"
        counts[word] = counts.get(word, 0) + 1
    return ", ".join(f"{counts[w]} {w}" for w in ("critical", "high", "medium", "low or unknown") if w in counts)


def _not_listed(view: triage.Listed, min_severity: str) -> None:
    """One line for the findings that the report counts, and a hint to list them."""
    parts = []
    if view.no_fix:
        tools = sorted({f.item.name for f in view.no_fix})
        parts.append(f"{_plural(len(view.no_fix), 'finding', 'findings')} that no upgrade fixes yet "
                     f"({', '.join(tools[:4])}{', ...' if len(tools) > 4 else ''})")
    if view.dependencies:
        n = len({f.item.parent or f.item.name for f in view.dependencies})
        parts.append(f"{len(view.dependencies)} in the packages inside {_plural(n, 'tool', 'tools')} "
                     f"({_count_by_severity(view.dependencies)})")
    if view.below:
        parts.append(f"{len(view.below)} below {min_severity}")
    if parts:
        ui.note(f"Not listed: {'; '.join(parts)}.")
        if view.no_fix or view.dependencies:
            ui.hint("dotkit audit --all lists them.")


def _details(result: match.Result, unchecked: List, view: triage.Listed) -> None:
    """With --all: what was not checked, and what was left out and why."""
    why: Dict[str, List[str]] = {}
    for i in unchecked:
        why.setdefault(i.why, []).append(i.name)
    for reason, names in why.items():
        ui.note(f"Not checked, {reason}: {', '.join(sorted(set(names)))}.")
    ui.note("Apps (Homebrew casks) are checked only for the Go and Rust packages in their executables: OSV has "
            "no data for the apps themselves. `brew outdated --cask --greedy` shows the old ones.")
    _summary(result)


def _summary(result: match.Result) -> None:
    if result.patched:
        ui.note(f"Left out: {_plural(len(result.patched), 'advisory', 'advisories')} that a Nixpkgs patch fixes.")
    reasons: Dict[str, int] = {}
    for _, why in result.left_out:
        reasons[why] = reasons.get(why, 0) + 1
    for why, n in reasons.items():
        ui.note(f"Left out: {_plural(n, 'advisory', 'advisories')}: {why}.")
    if result.notices:
        kinds = sorted({k for _, k in result.notices})
        ui.note(f"Left out: {_plural(len(result.notices), 'notice', 'notices')} that are not vulnerabilities "
                f"({', '.join(kinds)}).")
    if result.ignored:
        ui.note(f"Ignored: {_plural(len(result.ignored), 'advisory', 'advisories')} (audit.toml).")


def audit(env: Env, as_json: bool = False, issues: bool = False, notify_: bool = False,
          config_only: bool = False, all_: bool = False, min_severity: str = "",
          today: Optional[datetime.date] = None) -> int:
    today = today or datetime.date.today()
    rules = load_rules(env)
    ui.progress("Collecting the tools...")
    ctx = sources.Context(env, config_only=config_only)
    parts = sources.collect(ctx)
    for name, part in parts.items():
        for i in part.items + [f.item for f in part.findings]:
            i.where = i.where or sources.LABELS[name]
    items = [i for p in parts.values() for i in p.items]
    checked = [i for i in items if i.queries]
    unchecked = [i for i in items if not i.queries]
    ui.progress("Asking OSV.dev...")
    result = _ask(checked, [f for p in parts.values() for f in p.findings], rules, today)
    exploited = None
    if result.findings:
        exploited = kev.load(osv.cache_dir().parent)
        triage.mark_exploited(result.findings, exploited or frozenset())
        if any(f.item.source not in sources.DEPENDENCIES and not f.fix for f in result.findings):
            ui.progress("Checking what an upgrade fixes...")
            triage.check_fixes(ctx, result.findings, lambda targets: _ask(targets, [], [], today))
    view = triage.select(sorted(triage.one_per_key(result.findings),
                                key=lambda f: (triage.rank(f), sources.NAMES.index(f.item.source), f.item.name,
                                               f.advisory.id)),
                         all_, min_severity)
    findings = view.findings
    counts = {s: sum(1 for i in checked if i.source == s) + parts[s].checked for s in sources.NAMES}
    failed = {s for s, p in parts.items() if p.error}
    skipped = {s: p.skipped for s, p in parts.items() if p.skipped}
    complete = not failed

    if as_json:
        ui.out(json.dumps({
            "version": 1, "complete": complete, "checked": counts, "skipped": skipped,
            "findings": [_finding_json(f) for f in findings],
            "not_listed": {"no_fix": [_finding_json(f) for f in view.no_fix],
                           "dependencies": [_finding_json(f) for f in view.dependencies],
                           "below_severity": [_finding_json(f) for f in view.below]},
            "ignored": [{**_finding_json(f), "reason": r.reason, "file": r.file} for f, r in result.ignored],
            "patched": [_finding_json(f) for f in result.patched],
            "notices": [{**_finding_json(f), "notice": k} for f, k in result.notices],
            "left_out": [{**_finding_json(f), "reason": why} for f, why in result.left_out],
            "unchecked": [{"source": i.source, "tool": i.name, "version": i.version, "why": i.why} for i in unchecked],
            "errors": [parts[s].error for s in sources.NAMES if s in failed],
        }, indent=2))
    else:
        _print_findings(findings)

    for s in sources.NAMES:
        if s in failed:
            ui.warn(parts[s].error)
    if result.findings and exploited is None:
        ui.warn(f"cannot read the catalog of known exploited vulnerabilities ({kev.URL}), so no finding is marked "
                "as known exploited.")
    for r in result.expired:
        ui.warn(f"the ignore rule of {r.id} in {r.file} ended on {r.until.isoformat()}. Remove it, or set a new date.")
    for s, reason in skipped.items():
        ui.note(f"Not checked here: the {sources.NOUNS[s][1]} ({reason}).")
    if all_:
        _details(result, unchecked, view)
    listed = [_plural(counts[s], *sources.NOUNS[s]) for s in sources.NAMES if counts[s]]
    ui.note(f"Checked {', '.join(listed) or 'nothing'}.")
    _not_listed(view, min_severity)
    if findings:
        tools = len({(f.item.source, f.item.name) for f in findings})
        what = "" if all_ else " to act on"
        ui.warn(f"{_plural(len(findings), 'finding', 'findings')}{what}, for {_plural(tools, 'tool', 'tools')}. "
                "Upgrade them (dotkit upgrade), or add an ignore rule to audit.toml.")
    elif complete:
        ui.success("Nothing to act on." if view.counted else "No known vulnerabilities or malicious releases.")
    else:
        ui.warn("nothing to act on, but the audit is not complete: see the warnings above.")

    # A failed or skipped source keeps its old issues and notifications, and so does a tool that was not checked,
    # and a finding that is not listed.
    keep = failed | set(skipped) | {f"{i.source}/{i.name}" for i in unchecked}
    counted = {k for f in view.counted for k in f.keys}
    if notify_:
        notify(env, findings, keep | counted)
    if issues:
        from . import issues as issues_mod
        complete_sources = [s for s in sources.NAMES if s not in failed and s not in skipped]
        n = issues_mod.sync(env.repo, findings, complete_sources, {k for k in keep if "/" in k}, counted)
        ui.note(f"GitHub issues: {n['opened']} opened, {n['reopened']} reopened, {n['updated']} updated, "
                f"{n['closed']} closed.")
    # The issues and the notification report the findings: their run is a success. A part that was not
    # checked fails the run, so that a daily workflow shows it.
    return 1 if (findings and not (issues or notify_)) or not complete else 0
