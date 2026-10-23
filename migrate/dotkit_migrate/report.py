"""MIGRATION.md: what setup carried over, and the next step for everything else."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

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


@dataclass
class _Block:
    intro: str
    items: list[str] = field(default_factory=list)


@dataclass
class _Section:
    """A section of "To do": blocks of an intro and its items, in the order they first appear."""

    title: str
    blocks: list[_Block] = field(default_factory=list)

    @property
    def items(self) -> list[str]:
        return [i for b in self.blocks for i in b.items]


class Report:
    def __init__(self) -> None:
        self.carried_rows: list[tuple[str, str]] = []
        self.sections: dict[str, _Section] = {}
        self._order: list[str] = []

    def carried(self, what: str, where: str) -> None:
        self.carried_rows.append((what, where))

    def todo(self, title: str, intro: str, items: list[str]) -> None:
        sec = self.sections.get(title)
        if sec is None:
            sec = self.sections[title] = _Section(title)
            self._order.append(title)
        block = next((b for b in sec.blocks if b.intro == intro), None)
        if block is None:
            block = _Block(intro)
            sec.blocks.append(block)
        for i in items:
            if i not in block.items:
                block.items.append(i)

    @property
    def count(self) -> int:
        """The number of items to check, without the lines that dotkit does already."""
        return sum(max(1, len(s.items)) for t, s in self.sections.items() if t != "Shell lines: Done by dotkit")

    def _sorted(self) -> list[_Section]:
        def key(title: str) -> tuple[int, int]:
            head = "Shell lines" if title.startswith("Shell lines") else title
            rank = ORDER.index(head) if head in ORDER else len(ORDER)
            return rank, self._order.index(title)

        return [self.sections[t] for t in sorted(self.sections, key=key)]

    def render(self, scan, repo: str) -> str:
        host = scan.hostname or "this Mac"
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
        if self.carried_rows:
            out += ["| What | Where |", "|---|---|"]
            out += [f"| {_cell(w)} | {_cell(d)} |" for w, d in self.carried_rows]
        else:
            out.append("Nothing.")
        out += ["", "## To do", ""]
        if not self.sections:
            out += ["Nothing.", ""]
        for sec in self._sorted():
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
