"""The Homebrew formulae of the configuration: `brew vulns --json --deps`, which asks OSV itself and knows
the patches of Homebrew. With an installed formula it checks the installed version.

The formulae of other taps are not checked: brew vulns checks only the trusted taps, and a runner of CI
has no other taps. OSV has no data for casks (apps).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import List, Tuple

from ...core import proc
from ...core.nix import Nix
from .. import match
from ..match import HEX40
from ..model import FIX_AVAILABLE, FIX_NONE, FIX_UNKNOWN, Advisory, Finding, Item, Part

NAME, LABEL, NOUNS = "brew", "Homebrew", ("Homebrew formula", "Homebrew formulae")


def formulae(brewfile: str) -> Tuple[List[str], List[str]]:
    """The `brew "NAME"` lines of a Brewfile that brew vulns checks, and the names of the formulae of taps."""
    lines, taps = [], []
    for line in brewfile.split("\n"):
        m = re.match(r'\s*brew\s+"([^"]+)"', line)
        if m and "/" in m[1]:
            taps.append(m[1])
        elif m:
            lines.append(line)
    return lines, taps


def _severity(text: object) -> str:
    t = text.upper() if isinstance(text, str) else ""
    return {"MODERATE": "MEDIUM"}.get(t, t if t in ("LOW", "MEDIUM", "HIGH", "CRITICAL") else "")


def report(data: dict) -> Part:
    """The part of the JSON of `brew vulns --json`."""
    part = Part(checked=data.get("checked") if isinstance(data.get("checked"), int) else 0)
    for f in data.get("findings") or []:
        if not isinstance(f, dict) or not isinstance(f.get("formula"), str):
            continue
        item = Item(NAME, f["formula"], str(f.get("version") or ""))
        for v in f.get("vulnerabilities") or []:
            if not isinstance(v, dict) or not isinstance(v.get("id"), str):
                continue
            fixed = [x for x in v.get("fixed_versions") or [] if isinstance(x, str) and not HEX40.fullmatch(x)]
            a = Advisory(v["id"], [a for a in v.get("aliases") or [] if isinstance(a, str)],
                         " ".join(str(v.get("summary") or "").split())[:200], _severity(v.get("severity")), fixed,
                         v["id"].startswith("MAL-"))
            part.findings.append(Finding(item, a))
    skipped = [s for s in data.get("skipped_formulae") or [] if isinstance(s, str)]
    unscanned = data.get("unscanned_formulae")
    skipped += sorted(unscanned) if isinstance(unscanned, dict) else []
    part.items += [Item(NAME, name, "", why="Homebrew knows no source repository for it") for name in skipped]
    return part


def collect(ctx) -> Part:
    r = Nix(ctx.env).brewfile()
    if not r.ok:
        return Part(error="Homebrew: the Brewfile of the configuration did not evaluate, so the formulae are not "
                          "checked.")
    lines, taps = formulae(r.out)
    tap_items = [Item(NAME, name, "", why="brew vulns does not check the formulae of other taps") for name in taps]
    if not lines:
        return Part(tap_items)
    exe = proc.which("brew") or str(ctx.system("/opt/homebrew/bin/brew"))
    if not os.access(exe, os.X_OK):
        return Part(tap_items, error="Homebrew: brew is not installed, so the formulae are not checked.")
    with tempfile.TemporaryDirectory(prefix="dotkit-audit.") as tmp:
        brewfile = Path(tmp) / "Brewfile"
        brewfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = proc.run([exe, "vulns", "--json", "--deps", f"--brewfile={brewfile}"],
                     env={"HOMEBREW_NO_AUTO_UPDATE": "1"}, stdout=proc.CAPTURE, stderr=proc.CAPTURE)
    try:
        data = json.loads(r.out)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        if "Unknown command" in r.err:
            error = ("Homebrew: brew vulns needs Homebrew 6.0.11 or newer, so the formulae are not checked. "
                     "Run `brew update`.")
        else:
            last = r.err.strip().split("\n")[-1] if r.err.strip() else f"exit code {r.code}"
            error = f"Homebrew: brew vulns failed ({last}), so the formulae are not checked."
        return Part(tap_items, error=error)
    part = report(data)
    part.items = tap_items + part.items
    if part.findings:
        _fix_status(exe, part.findings)
    return part


def fix_status(installed: str, stable: str, fixed: List[str]) -> str:
    """FIX_AVAILABLE when the stable version of the formula is newer than the installed one and is a fixed
    version or newer (or brew vulns names no fixed version); FIX_NONE when it is the installed one."""
    if not stable:
        return FIX_UNKNOWN
    if match.version_key(stable) <= match.version_key(installed):
        return FIX_NONE
    if not fixed or any(match.version_key(f) <= match.version_key(stable) for f in fixed):
        return FIX_AVAILABLE
    return FIX_NONE


def _fix_status(exe: str, findings: List[Finding]) -> None:
    names = sorted({f.item.name for f in findings})
    r = proc.run([exe, "info", "--json=v2", "--formula", *names], env={"HOMEBREW_NO_AUTO_UPDATE": "1"},
                 stdout=proc.CAPTURE, stderr=proc.DEVNULL)
    try:
        data = json.loads(r.out) if r.ok else {}
    except ValueError:
        data = {}
    stable = {f.get("name"): (f.get("versions") or {}).get("stable") for f in (data.get("formulae") or [])
              if isinstance(f, dict)} if isinstance(data, dict) else {}
    for f in findings:
        version = stable.get(f.item.name)
        f.fix = fix_status(f.item.version, version if isinstance(version, str) else "", f.advisory.fixed)
        if f.fix == FIX_AVAILABLE:
            f.upgrade = version
