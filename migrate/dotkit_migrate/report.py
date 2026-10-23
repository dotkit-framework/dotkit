"""MIGRATION.md: what setup carried over, and the next step for everything else."""

from __future__ import annotations

import datetime

from .model import PHASE_TEXT, Failure

# The one ordered list of the sections under "To do". Shell line sections go
# where "Shell lines" is, in the order of rcparse.CATEGORIES. Each migrator's
# `sections` is a part of it (tests/test_registry.py).
SECTIONS = (
    "Problems during the migration",
    "Secrets",
    "Logins",
    "Config files",
    "Shell lines",
    "Other shell files",
    "Shell frameworks",
    "Login shell",
    "Version managers",
    "Tools that moved to mise",
    "Tools that are not in mise",
    "mise",
    "Homebrew copies of dotkit tools",
    "Not carried, by your choice",
    "Homebrew taps",
    "App Store apps",
    "Other config",
    "Other config that stays out of the repository",
    "Scan warnings",
)

# Where a person sees the traceback of a failure.
DETAILS = "`dotkit-migrate setup --dry-run --debug`"


def _sorted(sections: dict) -> list:
    """The sections in SECTIONS, and in the order they first appear within one place of it."""
    first = list(sections)

    def key(title: str) -> tuple[int, int]:
        head = "Shell lines" if title.startswith("Shell lines") else title
        rank = SECTIONS.index(head) if head in SECTIONS else len(SECTIONS)
        return rank, first.index(title)

    return [sections[t] for t in sorted(sections, key=key)]


def render(doc, host: str, repo: str) -> str:
    """The text of MIGRATION.md from a docs.ReportDoc."""
    host = host or "this Mac"
    date = datetime.date.today().isoformat()
    out = [
        f"# Migration from {host}",
        "",
        f"dotkit setup created this repository ({repo}) from the Mac {host} on {date}. This file lists "
        "what it carried over and what needs your attention. Work through the list, then delete this "
        "file and commit.",
        "",
        "Setup uninstalled nothing that was on the Mac; the Homebrew packages stay installed. A file "
        "in the way of a dotkit link is renamed to <name>.before-dotkit.",
        "",
        "## Carried over",
        "",
    ]
    if doc.carried_rows:
        out += ["| What | Where |", "|---|---|"]
        out += [f"| {_cell(w)} | {_cell(d)} |" for w, d in doc.carried_rows]
    else:
        out.append("Nothing.")
    out += ["", "## To do", ""]
    if not doc.sections:
        out += ["Nothing.", ""]
    for sec in _sorted(doc.sections):
        out.append(f"### {sec.title}")
        out.append("")
        for block in sec.blocks:
            if block.intro:
                out += [block.intro, ""]
            for item in block.items:
                out.append(f"- {item}")
            if block.items:
                out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def problem_item(f: Failure) -> str:
    """The item of a model.Failure under "Problems during the migration" (ARCHITECTURE.md, section 7.4)."""
    by_hand = f" By hand: {f.manual}" if f.manual else ""
    if f.phase == "conflict":
        return f"**{f.title}**: {conflict_text(f)} {f.error}. Please report it."
    if f.phase == "skipped":
        return (f"**{f.title}**: skipped, because {f.reason}. Nothing of it is in this repository.{by_hand}")
    where = f" ({f.where})" if f.where else ""
    return (f"**{f.title}**: failed while {PHASE_TEXT[f.phase]}: {_code(f.error)}{where}. Nothing of it "
            f"is in this repository.{by_hand} Details: {DETAILS}. Please report it.")


def conflict_text(f: Failure) -> str:
    """What a conflict did: "<document>: two parts wanted different values for `<target>`; the value of <the
    second owner> is left out." """
    return (f"{f.where}: two parts wanted different values for `{f.reason.replace('`', chr(39))}`; the value of "
            f"{f.title} is left out.")


def problems_lines(failures: list[Failure]) -> list[str]:
    """The first lines of the summary, and the lines on stderr: the parts that failed, then each conflict."""
    failed = [f for f in failures if f.phase != "conflict"]
    out = []
    if failed:
        n = len(failed)
        what = ", ".join(f"{f.migrator} ({PHASE_TEXT[f.phase]})" for f in failed)
        out.append(f"Problems: {plural(n, 'part')} of the migration failed: {what}. "
                   f"{'It is' if n == 1 else 'They are'} left out; MIGRATION.md says what to do.")
    out += [f"Problems: {conflict_text(f)}" for f in failures if f.phase == "conflict"]
    return out


def scan_warning(failures: list[Failure]) -> str:
    """The warning line of the first screen and of the plain prompts: the parts that have no screen."""
    n = len(failures)
    return (f"{plural(n, 'part')} of the scan failed: {', '.join(f.title for f in failures)}. "
            f"{'It is' if n == 1 else 'They are'} left out; the plan screen and MIGRATION.md say more.")


def _code(text: str) -> str:
    return "`" + text.replace("`", "'") + "`"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")
