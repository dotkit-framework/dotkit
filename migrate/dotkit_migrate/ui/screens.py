"""The screens of the setup wizard, as data. The curses and the plain frontends show the same screens.

The sequence: where the repository goes, the zsh keymap, one screen per page
of the Session (pages without items are not there), and the plan. A screen
only changes the Choices; the migrators have the rules. Nothing on the Mac
changes until the caller writes the plan after the last screen.

No curses here: tests drive the screens with a scripted frontend.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..context import expand, repo_error, tilde
from ..model import PHASE_TEXT, Choices, Failure, Page, Plan
from ..registry import Session

KEYMAPS = (("emacs", "Ctrl+A, Ctrl+E, Ctrl+R, as in most terminals"),
           ("vi", "Esc for command mode, as in vim"))


class Quit(Exception):
    """The person quits. Nothing is written."""


@dataclass
class Row:
    id: str  # an item id, or a keymap name
    label: str
    options: tuple[str, ...] = ()  # choices screens
    option_text: dict[str, str] = field(default_factory=dict)


@dataclass
class Screen:
    kind: str  # "text", "radio", "checklist", "choices", or "review"
    id: str  # "repo", "keymap", a page key, or "review"
    title: str
    intro: str
    rows: list[Row]
    # text: get(id) is the value. radio and checklist: get(row id) says whether the row is selected.
    # choices: get(row id) is the option of the row.
    get: Callable[[str], Any]
    # Changes the choices. Returns an error to show, or "".
    set: Callable[[str, Any], str]
    detail: Callable[[str], str]  # the text for the highlighted row
    note: str = ""  # a page note, for example why its items are off
    dest: str = ""  # where the selected items go
    lines: Callable[[], list[str]] | None = None  # review: the plan, built when the screen opens
    # review: the Problems block of the plan that lines() built last, in the warning color, above the lines.
    problems: Callable[[], list[str]] | None = None
    warning: str = ""  # the first screen: the parts of the scan that failed and have no screen


class Frontend:
    """Shows one screen. show() returns 1 (next) or -1 (back), or raises Quit."""

    def show(self, screen: Screen, step: int, total: int) -> int:
        raise NotImplementedError


def not_carried(pages: list[Page], c: Choices) -> list[tuple[Page, int]]:
    """Per checklist page: the items that were on by default and that are deselected now.

    MIGRATION.md lists the same items under "Not carried, by your choice".
    Items that are off by default have their own to-dos there.
    """
    out = []
    for page in pages:
        if page.kind != "checklist":
            continue
        n = sum(1 for i in page.items if i.default and not c.selected(page.key, i.id))
        if n:
            out.append((page, n))
    return out


def review_lines(plan: Plan, pages: list[Page], c: Choices) -> list[str]:
    """The text of the plan screen: the summary of the plan, then what you left out, per page."""
    lines = list(plan.summary)
    left = not_carried(pages, c)
    if left:
        lines += ["", "Not carried, by your choice:"] + [f"  {page.title}: {n}" for page, n in left]
    return lines


def problem_lines(failures: list[Failure]) -> list[str]:
    """The Problems block of the plan screen: each part that failed, its phase, and that its choices do nothing.

    A plan that failed can depend on a choice: b goes back, and the plan is
    built again when this screen opens.
    """
    if not failures:
        return []
    out = ["Problems:"]
    for f in failures:
        if f.phase == "skipped":
            what = f"skipped, because {f.reason}"
        elif f.phase == "conflict":
            what = (f"{f.where}: two parts wanted different values for {f.reason}; the value of {f.title} is "
                    f"left out ({f.error})")
        else:
            what = f"failed while {PHASE_TEXT[f.phase]}: {f.error}"
        out.append(f"  {f.title}: {what}.")
    out += ["  Their part is left out, so its choices have no effect. MIGRATION.md says what to do by hand.",
            "  b goes back: a changed choice builds the plan again.", ""]
    return out


def toggle_all(screen: Screen) -> None:
    """Selects every row of a checklist, or none when every row is selected."""
    on = not all(screen.get(r.id) for r in screen.rows)
    for r in screen.rows:
        screen.set(r.id, on)


def build(session: Session, c: Choices, home: str) -> list[Screen]:
    """The screens for the pages of the Session. They change c."""
    pages = session.pages()

    def set_repo(_id: str, value: str) -> str:
        path = expand(value or "~/.dotfiles", home)
        error = repo_error(path, home)
        if not error:
            c.repo = path
        return error or ""

    def set_keymap(row: str, on: bool) -> str:
        if on:
            c.keymap = row
        return ""

    out = [
        Screen("text", "repo", "Where the dotfiles repository goes",
               "Setup creates a new Git repository with your settings, lists, and a report of what needs your "
               "attention. Nothing on this Mac changes before the last screen.",
               [], lambda _id: tilde(c.repo, home), set_repo, lambda _id: "", warning=session.scan_warning()),
        Screen("radio", "keymap", "The zsh keymap",
               "How the command line edits text. nix/shell.nix has it, and you can change it later.",
               [Row(name, desc) for name, desc in KEYMAPS], lambda row: c.keymap == row, set_keymap,
               lambda _id: ""),
    ]
    out += [_page_screen(page, c) for page in pages if page.items]
    built: list[Plan] = []

    def lines() -> list[str]:
        built[:] = [session.build(c)]
        return review_lines(built[0], pages, c)

    out.append(Screen("review", "review", "The plan", "", [], lambda _id: None, lambda _id, _v: "",
                      lambda _id: "", lines=lines,
                      problems=lambda: problem_lines(built[0].failures) if built else []))
    return out


def _page_screen(page: Page, c: Choices) -> Screen:
    items = {i.id: i for i in page.items}

    def detail(item_id: str) -> str:
        item = items.get(item_id)
        return item.detail if item else ""

    if page.kind == "choices":
        def get_option(item_id: str) -> str:
            return c.option(item_id) or items[item_id].default_option

        def set_option(item_id: str, option: str) -> str:
            if option not in items[item_id].options:
                return f"{option} is not one of: {', '.join(items[item_id].options)}"
            c.options[item_id] = option
            return ""

        rows = [Row(i.id, i.label, i.options, i.option_text) for i in page.items]
        return Screen("choices", page.key, page.title, page.intro, rows, get_option, set_option, detail,
                      page.note, page.dest)

    def set_picked(item_id: str, on: bool) -> str:
        picked = c.picked.setdefault(page.key, set())
        if on:
            picked.add(item_id)
        else:
            picked.discard(item_id)
        return ""

    rows = [Row(i.id, i.label) for i in page.items]
    return Screen("checklist", page.key, page.title, page.intro, rows,
                  lambda item_id: c.selected(page.key, item_id), set_picked, detail, page.note, page.dest)


def run(frontend: Frontend, screens: list[Screen]) -> bool:
    """Shows the screens, forward and back. False when the person quits."""
    i = 0
    try:
        while True:
            move = frontend.show(screens[i], i + 1, len(screens))
            if move > 0 and i == len(screens) - 1:
                return True
            i = max(0, min(len(screens) - 1, i + move))
    except Quit:
        return False
