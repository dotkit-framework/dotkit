"""The source picker of `dotkit add` (BEHAVIOR.md, rows Pk).

    lazygit
    nix   0.66.2  Simple terminal UI for git commands          <- the selected row: reverse video
    mise  0.66.2  simple terminal UI for git commands
    brew  0.66.2  Simple terminal UI for git commands
    pipx  1.1     Automating the daily git push operations · anyone can publish
    ↑/↓ move · enter select · esc cancel

ask() draws the list with prompt_toolkit on stderr, so that stdout keeps
only the plan, and removes it when it returns. The first row is the source
that the rules select, and the cursor starts there. Esc cancels; Ctrl-C
stops dotkit (KeyboardInterrupt). A terminal that cannot show the list
(TERM=dumb, no TERM) gets a numbered question instead.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..core import ui

REGISTRY_NOTE = "anyone can publish"
HELP = "↑/↓ move · enter select · esc cancel"
# The time to wait after Esc for the rest of an arrow key: short, so that Esc feels immediate.
ESC_WAIT = 0.05


@dataclass
class Row:
    manager: str
    version: str
    description: str
    note: str = ""


def lines(title: str, rows: List[Row], cursor: int, columns: int) -> List[List[Tuple[str, str]]]:
    """The list as lines of (style, text): the title, the rows in three columns, and the help. Each line fits
    in `columns`; the description takes the space that is left. Pure, for the tests."""
    mgr = max(ui.width(r.manager) for r in rows)
    ver = max(ui.width(r.version or "?") for r in rows)
    out = [[("class:title", ui.cut(title, columns))]]
    for i, r in enumerate(rows):
        # " MGR  VER  " before the description.
        room = max(columns - (1 + mgr + 2 + ver + 2), 1)
        desc = r.description
        note = f" · {r.note}" if r.note else ""
        if ui.width(desc + note) > room:
            note = ""
        desc = ui.cut(desc, room - ui.width(note))
        line = [("class:manager", f" {ui.pad(r.manager, mgr)}  "), ("class:version", f"{ui.pad(r.version or '?', ver)}  "),
                ("", desc), ("class:note", note)]
        if i == cursor:
            used = sum(ui.width(t) for _, t in line)
            line = [(f"{style} class:selected".strip(), text) for style, text in line]
            line.append(("class:selected", " " * max(columns - used, 0)))
        out.append(line)
    out.append([("class:help", ui.cut(HELP, columns))])
    return out


def ask(title: str, rows: List[Row]) -> Optional[int]:
    """The index of the row that the user selects; None when the user cancels."""
    if os.environ.get("TERM", "") in ("", "dumb"):
        return _ask_numbered(title, rows)
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import merge_formatted_text
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output import ColorDepth, create_output
    from prompt_toolkit.styles import Style

    state = {"cursor": 0}
    output = create_output(stdout=sys.stderr)

    def text():
        width = output.get_size().columns - 1
        parts = []
        for n, line in enumerate(lines(title, rows, state["cursor"], width)):
            parts.append(line + ([("", "\n")] if n < len(rows) + 1 else []))
        return merge_formatted_text(parts)

    keys = KeyBindings()

    @keys.add("up")
    def _up(event):
        state["cursor"] = max(state["cursor"] - 1, 0)

    @keys.add("down")
    def _down(event):
        state["cursor"] = min(state["cursor"] + 1, len(rows) - 1)

    @keys.add("enter")
    def _select(event):
        event.app.exit(result=state["cursor"])

    @keys.add("escape")
    def _cancel(event):
        event.app.exit(result=None)

    @keys.add("c-c")
    def _interrupt(event):
        event.app.exit(exception=KeyboardInterrupt)

    no_color = bool(os.environ.get("NO_COLOR"))
    style = Style.from_dict({
        "title": "bold",
        "manager": "" if no_color else "bold",
        "version": "" if no_color else "ansicyan",
        "note": "" if no_color else "ansiyellow",
        "help": "" if no_color else "ansibrightblack",
        # Reverse video: a background that shows on light and dark themes, also without color.
        "selected": "reverse",
    })
    app = Application(layout=Layout(Window(FormattedTextControl(text, show_cursor=False), dont_extend_height=True)),
                      key_bindings=keys, style=style, output=output, erase_when_done=True,
                      color_depth=ColorDepth.MONOCHROME if no_color else None)
    app.ttimeoutlen = ESC_WAIT
    ui.flush()
    return app.run()


def _ask_numbered(title: str, rows: List[Row]) -> Optional[int]:
    ui.note(title)
    mgr = max(ui.width(r.manager) for r in rows)
    ver = max(ui.width(r.version or "?") for r in rows)
    for i, r in enumerate(rows, 1):
        note = f" · {r.note}" if r.note else ""
        ui.note(f"  {i}) {ui.pad(r.manager, mgr)}  {ui.pad(r.version or '?', ver)}  {r.description}{note}")
    while True:
        ui.raw_err(f"Select 1-{len(rows)} (Enter for 1), or q to cancel: ")
        line = ui.read_line()
        if line is None:
            return None
        answer = line.strip(" \t")
        if answer == "":
            return 0
        if answer.isdigit() and 1 <= int(answer) <= len(rows):
            return int(answer) - 1
        if answer in ("q", "Q"):
            return None
