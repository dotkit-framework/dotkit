"""The data that the parts of the migration pass to each other."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Item:
    """One thing on a page that the person selects, or chooses an option for."""

    id: str  # stable and prefixed, for example brew:wget or file:.npmrc
    label: str
    detail: str = ""  # shown for the highlighted row
    default: bool = True  # checklist pages
    options: tuple[str, ...] = ()  # choices pages, for example ("layer", "replace", "dotkit", "keep")
    default_option: str = ""
    option_text: dict[str, str] = field(default_factory=dict)


@dataclass
class Page:
    """One screen of questions: the items of one source."""

    key: str
    title: str  # names the source: "Homebrew formulae"
    dest: str  # where selected items go: "nix/brews.txt"
    intro: str
    kind: str  # "checklist" or "choices"
    items: list[Item] = field(default_factory=list)
    note: str = ""  # for example why the items are off by default


@dataclass
class Choices:
    """What the person chose. Plain data; tests and tests/wizard_child.py write it as JSON."""

    repo: str
    keymap: str = "emacs"
    picked: dict[str, set[str]] = field(default_factory=dict)  # page key -> selected item ids
    options: dict[str, str] = field(default_factory=dict)  # item id -> option (choices pages)

    def selected(self, page: str, item_id: str) -> bool:
        return item_id in self.picked.get(page, ())

    def option(self, item_id: str) -> str:
        return self.options.get(item_id, "")

    @classmethod
    def defaults(cls, repo: str, pages: list[Page]) -> "Choices":
        c = cls(repo=repo)
        for page in pages:
            if page.kind == "checklist":
                c.picked[page.key] = {i.id for i in page.items if i.default}
            else:
                c.options.update({i.id: i.default_option for i in page.items})
        return c

    def to_json(self) -> dict:
        return {"repo": self.repo, "keymap": self.keymap,
                "picked": {k: sorted(v) for k, v in sorted(self.picked.items())},
                "options": dict(sorted(self.options.items()))}


@dataclass
class Plan:
    repo: Path
    keymap: str
    # Files of the new repository, relative to it, with their content. They
    # replace the template files of the same name.
    files: dict[str, str] = field(default_factory=dict)
    # Files and directories to copy into the repository: repository path -> source.
    copies: dict[str, Path] = field(default_factory=dict)
    # Files with secrets, in local/ of the repository, which Git ignores.
    local: dict[str, str] = field(default_factory=dict)
    links: dict[str, str | None] = field(default_factory=dict)
    # Links of yours (not of Home Manager) where the new configuration links a
    # file, relative to the home directory. Home Manager does not move a link
    # aside, so scripts/setup renames each one to <name>.before-dotkit right
    # before the first activation. writer.write_new() lists them in .build/links-in-way.
    move_links: list[str] = field(default_factory=list)
    # Why the plan cannot be applied, for example ~/.config is a link. The
    # wizard shows them, and nothing is written.
    blockers: list[str] = field(default_factory=list)
    home: str = ""
    summary: list[str] = field(default_factory=list)
    report: str = ""
