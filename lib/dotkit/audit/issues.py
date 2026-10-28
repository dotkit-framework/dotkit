"""One GitHub issue per finding, with gh, in the GitHub repository of the dotfiles repository (or GH_REPO).

Each issue has the label dotkit-audit and a marker with the key of its finding in the body. A run opens
an issue for a new finding, reopens the issue of a finding that is back, and updates the body when the
version changed. An issue whose key is the key of an alias is the same finding. A run closes the open
issues whose finding is gone, but not for a source that failed or a tool that was not checked.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Collection, Dict, List, Sequence

from ..core import proc, ui
from . import match
from .model import Finding

LABEL = "dotkit-audit"
# A key can have spaces: dotkit/input zsh-abbr/ID.
_MARKER = re.compile(r"<!-- dotkit-audit: (.+?) -->")


def _gh(repo: Path, args: List[str], body: str = "") -> proc.Result:
    """gh in the dotfiles repository, so that it finds its GitHub repository, from any directory."""
    r = proc.run(["gh", *args], cwd=repo, stdin=body if body else None, stdout=proc.CAPTURE, stderr=proc.CAPTURE)
    if not r.ok:
        raise ui.die(f"gh {args[0]} {args[1]} failed: {r.err.strip() or f'exit code {r.code}'}")
    return r


def title(f: Finding) -> str:
    kind = "malicious release" if f.advisory.malicious else "advisory"
    return f"{f.item.name} {f.item.version}: {kind} {f.advisory.id}".replace("  ", " ")


def body(f: Finding) -> str:
    a, item = f.advisory, f.item
    rows = [("Tool", f"`{item.name}` {item.version} ({item.where})"),
            ("Advisory", f"[{a.id}]({a.url})"), ("Severity", match.severity_text(a))]
    if a.aliases:
        rows.append(("Aliases", ", ".join(a.aliases)))
    if a.fixed:
        rows.append(("Fixed in", ", ".join(a.fixed)))
    lines = [f"<!-- dotkit-audit: {f.key} -->"]
    if a.malicious:
        lines += ["**OSV lists this release as malicious.** Remove it, and treat the Mac as compromised until "
                  "you know what the release did.", ""]
    # &#64;: an npm scope in a summary (@scope/pkg) is not a mention that notifies the scope's owner.
    summary = (a.summary or "(no summary)").replace("@", "&#64;")
    lines += [summary, "", "| | |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows]
    if a.malicious:
        lines += ["", f"To fix it, remove the tool (`dotkit remove {item.name}`), or pin a version without this "
                  "release."]
    else:
        lines += ["", "To fix it, upgrade the tool: `dotkit upgrade`. When a fix is not released yet, or the "
                  "advisory does not apply to you, add an ignore rule to `audit.toml` of your repository:", "",
                  "```toml", "[[ignore]]", f'id = "{a.id}"', f'tool = "{item.name}"', 'reason = "..."',
                  "# until = YYYY-MM-DD: the rule ends on this date, and the finding comes back.", "```"]
    lines += ["", "dotkit audit closes this issue when the finding is gone."]
    return "\n".join(lines) + "\n"


def sync(repo: Path, findings: Sequence[Finding], complete_sources: Collection[str],
         unchecked: Collection[str] = ()) -> Dict[str, int]:
    """Opens, reopens, updates, and closes the issues. unchecked: the tool keys (SOURCE/NAME) that the run
    did not check. Returns the count of each change."""
    _gh(repo, ["label", "create", LABEL, "--color", "B60205", "--force",
               "--description", "A finding of dotkit audit: a known vulnerability or a malicious release"])
    r = _gh(repo, ["issue", "list", "--label", LABEL, "--state", "all", "--limit", "1000",
                   "--json", "number,state,title,body"])
    try:
        issues = json.loads(r.out)
    except ValueError:
        raise ui.die("gh issue list did not print JSON.") from None
    by_key: Dict[str, dict] = {}
    for issue in issues if isinstance(issues, list) else []:
        m = _MARKER.search(issue.get("body") or "") if isinstance(issue, dict) else None
        # The newest issue of a key counts: gh lists the newest first.
        if m and m[1] not in by_key:
            by_key[m[1]] = issue
    counts = {"opened": 0, "reopened": 0, "updated": 0, "closed": 0}
    current = set()
    for f in findings:
        keys = f.keys
        current.update(keys)
        issue = next((by_key[k] for k in keys if k in by_key), None)
        text, head = body(f), title(f)
        if issue is None:
            _gh(repo, ["issue", "create", "--title", head, "--label", LABEL, "--body-file", "-"], text)
            counts["opened"] += 1
            continue
        number = str(issue["number"])
        if issue.get("state") != "OPEN":
            _gh(repo, ["issue", "reopen", number, "--comment", "dotkit audit finds this again."])
            counts["reopened"] += 1
        if (issue.get("body") or "").strip() != text.strip() or issue.get("title") != head:
            _gh(repo, ["issue", "edit", number, "--title", head, "--body-file", "-"], text)
            counts["updated"] += 1
        # An issue of another alias of the same finding.
        for k in keys:
            other = by_key.get(k)
            if other is not None and other is not issue and other.get("state") == "OPEN":
                _gh(repo, ["issue", "close", str(other["number"]), "--comment", f"The same finding as #{number}."])
                other["state"] = "CLOSED"
                counts["closed"] += 1
    for key, issue in by_key.items():
        source, tool = key.split("/", 1)[0], key.rsplit("/", 1)[0]
        if key in current or issue.get("state") != "OPEN" or source not in complete_sources or tool in unchecked:
            continue
        _gh(repo, ["issue", "close", str(issue["number"]), "--comment",
                   "dotkit audit does not find this any more: the tool was upgraded or removed, the advisory changed, "
                   "or audit.toml ignores it."])
        issue["state"] = "CLOSED"
        counts["closed"] += 1
    return counts
