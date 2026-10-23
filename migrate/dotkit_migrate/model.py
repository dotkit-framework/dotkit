"""The data that the parts of the migration pass to each other."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
class Identity:
    """Who and where: the core scan of the Session, before the migrators."""

    home: str
    user: str
    hostname: str = ""
    login_shell: str = ""
    git_name: str = ""
    git_email: str = ""


class Facts:
    """What every scan found, by migrator key, for pages(), plan(), and adopt.

    base and check_syntax are set by the Session. They are the only things
    from outside the facts that the migrators use: base is the dotkit base
    (read-only data), and check_syntax(shell, text) runs zsh -n or bash -n
    (None: no check).
    """

    def __init__(self, identity: Identity, base: Any, check_syntax: Callable[[str, str], bool] | None = None,
                 by_key: dict[str, object] | None = None, warnings: list[str] | None = None):
        self.identity = identity
        self.base = base
        self.check_syntax = check_syntax
        self._by_key = dict(by_key or {})
        self.warnings = list(warnings or [])

    def get(self, key: str) -> object | None:
        """The facts of one migrator, or None when its scan failed or did not run."""
        return self._by_key.get(key)

    def without(self, keys: Collection[str]) -> "Facts":
        """The same facts, but the migrators of keys have none, and none of their warnings: they failed."""
        kept = {k: v for k, v in self._by_key.items() if k not in keys}
        return Facts(self.identity, self.base, self.check_syntax, kept, warnings_of(kept))


# The phases of a Failure in a sentence: "failed while planning". The UI and the report share them.
PHASE_TEXT = {"scan": "scanning", "pages": "preparing its questions", "plan": "planning",
              "adopt": "adding to the lists", "skipped": "skipped", "conflict": "a conflict"}


@dataclass
class Failure:
    """A part of the migration that crashed, was skipped, or had a conflict (ARCHITECTURE.md, section 7)."""

    migrator: str  # its key, or "identity"
    title: str
    phase: str  # "scan", "pages", "plan", "adopt", "skipped", or "conflict"
    # "KeyError: 'versions'": short, with the home as ~, masked. A conflict: what both owners wanted.
    error: str
    # The last frame inside dotkit_migrate: "migrators/runtimes.py:88 in plan". A conflict: the document.
    where: str
    manual: str  # what to do by hand: Migrator.manual_steps(ctx)
    reason: str = ""  # "skipped": "Shell lines failed". A conflict: the target both wanted, "npm:eslint"


def warnings_of(by_key: dict[str, object]) -> list[str]:
    """The scan warnings of these facts, in their order (the order of MIGRATORS)."""
    return [w for found in by_key.values() for w in getattr(found, "warnings", None) or []]


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
    # The parts of the migration that failed: they contributed nothing.
    failures: list[Failure] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    report: str = ""


@dataclass
class AdoptOptions:
    """The options of `dotkit migrate`."""

    all_tools: bool = False  # --all: also cargo and Go tools, which mise builds from source


@dataclass
class AdoptResult:
    """What `dotkit migrate` adds to an existing repository. writer.apply_edits() writes it."""

    repo: Path
    # The files that change, relative to the repository, with their new text.
    files: dict[str, str] = field(default_factory=dict)
    # The text of each of these files that the plan was built from; None: the file did not exist.
    before: dict[str, str | None] = field(default_factory=dict)
    # What was added and what was not, as shown; masked.
    output: list[str] = field(default_factory=list)
    # The parts that failed, were skipped, or had a conflict: nothing of them was added.
    failures: list[Failure] = field(default_factory=list)
