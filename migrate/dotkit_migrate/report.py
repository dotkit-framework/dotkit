"""MIGRATION.md: what setup carried over, and the next step for everything else."""

from __future__ import annotations

import datetime

# The order of the sections under "To do". Shell line sections go where
# "Shell lines" is, in the order of rcparse.CATEGORIES.
ORDER = (
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
    "Homebrew packages that are not in the lists",
    "Homebrew taps",
    "App Store apps",
    "Other config",
    "Other config that stays out of the repository",
    "Scan errors",
)


def _sorted(sections: dict) -> list:
    """The sections in ORDER, and in the order they first appear within one place of it."""
    first = list(sections)

    def key(title: str) -> tuple[int, int]:
        head = "Shell lines" if title.startswith("Shell lines") else title
        rank = ORDER.index(head) if head in ORDER else len(ORDER)
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


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")
