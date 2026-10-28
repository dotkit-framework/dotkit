"""The GitHub Actions of the workflows in .github/workflows of the repository, and of dotkit when the
repository is in the dotkit directory (the audit of the base, with the template as the repository).

The version of an action is its tag: the ref of `uses: owner/repo@v4`, or the comment after a commit,
`uses: owner/repo@HASH # v4.1.2`, as Dependabot writes it. OSV does not compare the versions of this
ecosystem, so the query asks for all advisories of the action, and match.in_ranges compares them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ...core import proc
from .. import match
from ..match import HEX40
from ..model import Item, Part, package_query

NAME, LABEL, NOUNS = "actions", "GitHub Actions", ("GitHub Action", "GitHub Actions")
ECOSYSTEM = "GitHub Actions"

_USES = re.compile(r"""^\s*(?:-\s*)?uses:\s*["']?([^\s"'#@]+)@([^\s"'#]+)["']?\s*(?:#\s*(\S+))?""")


def uses(text: str) -> List[Tuple[str, str, str]]:
    """(action, ref, the tag of the comment) of each `uses:` line; action is owner/repo, also for an action
    or a reusable workflow in a subdirectory. Local actions (./) and Docker images are left out."""
    out = []
    for line in text.split("\n"):
        m = _USES.match(line)
        if not m or m[1].startswith((".", "docker:")):
            continue
        parts = m[1].split("/")
        if len(parts) >= 2:
            out.append(("/".join(parts[:2]), m[2], m[3] or ""))
    return out


def item(action: str, ref: str, comment: str) -> Item:
    version = comment if HEX40.fullmatch(ref) else ref
    out = Item(NAME, action, version)
    if not re.match(r"v?\d", version):
        out.why = "its ref is not a release tag" if version else "it is pinned to a commit without a tag comment"
        return out
    if len(re.findall(r"\d+", version.split("-", 1)[0])) < 3:
        # v4 moves to each new 4.x release: its version is not known.
        out.why = "its tag moves to each new release; pin a release, or a commit with its tag in a comment"
        return out
    out.queries = [package_query(ECOSYSTEM, action)]

    def accept(record: dict, query: dict) -> Optional[str]:
        return None if match.in_ranges(record, query, version) else ""

    out.accept = accept
    return out


def newest_tag(refs: str) -> str:
    """The newest release tag of the output of `git ls-remote --tags`: three numbers or more, no pre-release.
    An annotated tag is there twice, also as TAG^{}."""
    tags = set()
    for line in refs.split("\n"):
        _, _, ref = line.partition("\trefs/tags/")
        if ref:
            tags.add(ref[:-3] if ref.endswith("^{}") else ref)
    releases = [t for t in tags if re.fullmatch(r"v?\d+\.\d+\.\d+(\.\d+)*", t)]
    return max(releases, key=match.version_key, default="")


def upgrades(ctx, items: List[Item]) -> List[Optional[Item]]:
    """The item of the newest release tag of each action (`git ls-remote --tags`): a Dependabot update or an
    edit of the workflow installs it."""
    out: List[Optional[Item]] = []
    for i in items:
        r = proc.run(["git", "ls-remote", "--tags", f"https://github.com/{i.name}"], stdout=proc.CAPTURE,
                     stderr=proc.DEVNULL, env={"GIT_TERMINAL_PROMPT": "0"})
        tag = newest_tag(r.out) if r.ok else ""
        out.append(item(i.name, tag, "") if tag else None)
    return out


def workflow_files(ctx) -> List[Path]:
    env = ctx.env
    roots = [env.repo]
    if env.repo != env.dotkit and env.dotkit in env.repo.parents:
        roots.append(env.dotkit)
    out: List[Path] = []
    for root in roots:
        d = root / ".github/workflows"
        if d.is_dir():
            out += sorted(p for p in d.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file())
    return out


def collect(ctx) -> Part:
    found: Dict[Tuple[str, str], Item] = {}
    for path in workflow_files(ctx):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for action, ref, comment in uses(text):
            i = item(action, ref, comment)
            found.setdefault((i.name, i.version), i)
    return Part(list(found.values()))
