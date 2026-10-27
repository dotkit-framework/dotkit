"""The source picker of `dotkit add` (CLI-PLAN.md, section 7; PARITY.md, DV2 and rows Pk).

ask() shows the candidates with questionary (prompt_toolkit), on stderr, so
that stdout keeps only the plan. The arrow keys or j and k move, Enter
selects, and Ctrl-C stops dotkit. The last two rows skip the tool or stop
without a change. A terminal that cannot show the list (TERM=dumb, no TERM)
gets a numbered question instead.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..core import ui

PICK, SKIP, STOP = "pick", "skip", "stop"
Result = Tuple[str, Optional[int]]
REGISTRY_NOTE = "anyone can publish here; check the package"
SKIP_TITLE = "Skip this tool"
STOP_TITLE = "Stop, and change nothing"


@dataclass
class Row:
    spec: str
    version: str
    description: str
    note: str = ""

    def text(self, columns: int) -> str:
        """The row in one line of at most `columns` columns (wide characters count as two)."""
        line = f"{ui.pad(self.spec, 30)} {ui.pad(self.version or '?', 14)} {self.description}"
        if self.note:
            line += f" ({self.note})"
        return ui.cut(line, columns)


def ask(title: str, rows: List[Row], default: Optional[int]) -> Result:
    """The row that the user selects, or SKIP or STOP. default is the row of the rules; None: SKIP."""
    if os.environ.get("TERM", "") in ("", "dumb"):
        return _ask_numbered(title, rows, default)
    import questionary
    from prompt_toolkit.output import create_output

    columns = (ui.terminal_columns(2) or 80) - 4
    choices = [questionary.Choice(r.text(columns), value=(PICK, i)) for i, r in enumerate(rows)]
    skip = questionary.Choice(SKIP_TITLE, value=(SKIP, None))
    stop = questionary.Choice(STOP_TITLE, value=(STOP, None))
    question = questionary.select(
        title, choices=[*choices, questionary.Separator(), skip, stop],
        default=choices[default] if default is not None else skip,
        instruction="(↑/↓, Enter)", output=create_output(stdout=sys.stderr))
    ui.flush()
    # unsafe_ask: Ctrl-C raises KeyboardInterrupt, which stops dotkit with 130, instead of None.
    return question.unsafe_ask()


def _ask_numbered(title: str, rows: List[Row], default: Optional[int]) -> Result:
    ui.note(title)
    for i, r in enumerate(rows, 1):
        ui.note(f"  {i}) {r.text(200)}")
    hint = f", Enter for {default + 1}" if default is not None else ""
    while True:
        ui.raw_err(f"Select 1-{len(rows)}{hint}, s to skip, q to stop: ")
        line = ui.read_line()
        if line is None:
            return STOP, None
        answer = line.strip(" \t")
        if answer == "" and default is not None:
            return PICK, default
        if answer.isdigit() and 1 <= int(answer) <= len(rows):
            return PICK, int(answer) - 1
        if answer in ("s", "S"):
            return SKIP, None
        if answer in ("q", "Q"):
            return STOP, None
