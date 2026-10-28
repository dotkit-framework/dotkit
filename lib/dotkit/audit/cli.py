"""dotkit audit: the report of the known vulnerabilities and malicious releases (BEHAVIOR.md, rows Au).

The findings go to stdout, the summary to stderr. --json prints the report as JSON for CI. --issues
syncs one GitHub issue per finding (issues.py). --notify shows a macOS notification for the findings
that the last notified run did not have; the launchd agent of nix/darwin.nix runs it.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Collection, Dict, List, Optional

from ..core import proc, ui
from ..core.env import Env
from . import inventory, match, osv

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 and 3.10: tomli of dotkit/_vendor, the same parser.
    import tomli as tomllib

RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "": 4}
ROLE = {"CRITICAL": "error", "HIGH": "error", "MEDIUM": "warn", "LOW": "dim", "": "dim"}
NOUNS = {"nix": ("Nix package", "Nix packages"), "brew": ("Homebrew formula", "Homebrew formulae"),
         "mise": ("mise tool", "mise tools"), "dotkit": ("package of dotkit", "packages of dotkit")}


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


def _finding_json(f: match.Finding) -> dict:
    a = f.advisory
    return {"key": f.key, "source": f.item.source, "tool": f.item.name, "version": f.item.version, "id": a.id,
            "aliases": a.aliases, "summary": a.summary, "severity": a.severity, "fixed": a.fixed,
            "malicious": a.malicious, "url": a.url}


def _state_path(env: Env) -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(env.home / ".local/state")
    return Path(state) / "dotkit" / "audit.json"


def notify(env: Env, findings: List[match.Finding], keep: Collection[str] = ()) -> None:
    """A macOS notification for the findings that the last run did not have (by any key of an alias). The
    state file gets the keys of the current findings, so a finding that is gone and comes back is new
    again. keep: the sources and the tool keys that this run did not check; their old keys stay."""
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
    kept = {k for k in seen if k.split("/", 1)[0] in keep or k.rsplit("/", 1)[0] in keep}
    current = {k for f in findings for k in f.keys}
    tmp.write_text(json.dumps({"seen": sorted(kept | current)}) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _print_findings(findings: List[match.Finding]) -> None:
    by_item: Dict[int, List[match.Finding]] = {}
    for f in findings:
        by_item.setdefault(id(f.item), []).append(f)
    first = True
    for group in by_item.values():
        item = group[0].item
        if not first:
            ui.out()
        first = False
        version = f" {item.version}" if item.version else ""
        ui.out(f"{ui.paint(item.name, 'name')}{version} ({match.SOURCE_NAMES[item.source]}):")
        for f in group:
            a = f.advisory
            kind = ui.paint("MALICIOUS", "error") if a.malicious else ui.paint(a.severity.lower() or "unknown", ROLE[a.severity])
            ui.out(f"  {a.id} ({kind}): {a.summary or 'no summary'}")
            extra = f"Fixed in {', '.join(a.fixed)}. " if a.fixed else ""
            ui.out(f"    {extra}{ui.paint(a.url, 'dim')}")


def audit(env: Env, as_json: bool = False, issues: bool = False, notify_: bool = False,
          today: Optional[datetime.date] = None) -> int:
    today = today or datetime.date.today()
    rules = load_rules(env)
    ui.progress("Collecting the tools...")
    inv = inventory.collect(env)
    checked = [i for i in inv.items if i.queries]
    queries = [q for i in checked for q in i.queries]
    ui.progress("Asking OSV.dev...")
    answers = osv.query(queries) if queries else []
    modified: Dict[str, str] = {}
    for answer in answers:
        modified.update(answer)
    records = osv.records(modified, osv.cache_dir())
    result = match.findings(checked, [[i for i, _ in a] for a in answers], records, rules, today, inv.brew)
    findings = sorted(result.findings, key=lambda f: (RANK[f.advisory.severity] if not f.advisory.malicious else -1,
                                                     match.SOURCES.index(f.item.source), f.item.name, f.advisory.id))
    unchecked = [i for i in inv.items if not i.queries]
    counts = {s: sum(1 for i in checked if i.source == s) for s in match.SOURCES}
    counts["brew"] += inv.brew_checked
    failed = {source for source, _ in inv.errors}
    complete = not inv.errors

    if as_json:
        ui.out(json.dumps({
            "version": 1, "complete": complete, "checked": counts,
            "findings": [_finding_json(f) for f in findings],
            "ignored": [{**_finding_json(f), "reason": r.reason, "file": r.file} for f, r in result.ignored],
            "patched": [_finding_json(f) for f in result.patched],
            "unchecked": [{"source": i.source, "tool": i.name, "version": i.version, "why": i.why} for i in unchecked],
            "errors": [text for _, text in inv.errors],
        }, indent=2))
    else:
        _print_findings(findings)

    for _, text in inv.errors:
        ui.warn(text)
    for r in result.expired:
        ui.warn(f"the ignore rule of {r.id} in {r.file} ended on {r.until.isoformat()}. Remove it, or set a new date.")
    if unchecked:
        why: Dict[str, List[str]] = {}
        for i in unchecked:
            why.setdefault(i.why, []).append(i.name)
        for reason, names in why.items():
            ui.note(f"Not checked, {reason}: {', '.join(sorted(set(names)))}.")
    ui.note("Homebrew casks are not checked: OSV has no data for apps. `brew outdated --cask --greedy` shows "
            "the old ones.")
    if result.patched:
        ui.note(f"Left out: {_plural(len(result.patched), 'advisory', 'advisories')} that a Nixpkgs patch fixes.")
    if result.ignored:
        ui.note(f"Ignored: {_plural(len(result.ignored), 'advisory', 'advisories')} (audit.toml).")
    parts = [_plural(counts[s], *NOUNS[s]) for s in match.SOURCES if counts[s]]
    ui.note(f"Checked {', '.join(parts) or 'nothing'}.")
    if findings:
        tools = len({(f.item.source, f.item.name) for f in findings})
        ui.warn(f"{_plural(len(findings), 'finding', 'findings')} for {_plural(tools, 'tool', 'tools')}. "
                "Upgrade them (dotkit upgrade), or add an ignore rule to audit.toml.")
    elif complete:
        ui.success("No known vulnerabilities or malicious releases.")
    else:
        ui.warn("no findings, but the audit is not complete: see the warnings above.")

    unchecked_tools = {f"{i.source}/{i.name}" for i in unchecked}
    if notify_:
        notify(env, findings, failed | unchecked_tools)
    if issues:
        from . import issues as issues_mod
        complete_sources = [s for s in match.SOURCES if s not in failed]
        n = issues_mod.sync(env.repo, findings, complete_sources, unchecked_tools)
        ui.note(f"GitHub issues: {n['opened']} opened, {n['reopened']} reopened, {n['updated']} updated, "
                f"{n['closed']} closed.")
    # The issues and the notification report the findings: their run is a success. A part that was not
    # checked fails the run, so that a daily workflow shows it.
    return 1 if (findings and not (issues or notify_)) or not complete else 0
