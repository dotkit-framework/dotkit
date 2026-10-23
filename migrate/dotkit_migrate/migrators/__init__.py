"""The migrators: one source of the Mac each, from its scan to its part of the plan.

registry.MIGRATORS has them in order. A migrator imports only context,
catalog, model, docs (through the Txn it gets), declared, rcparse, secrets,
base, _compat (for tomllib), report (only plural), and its own helpers. It
never imports ui, writer, proc, subprocess, or curses, never writes a file,
and never runs a command but through ctx.runner in its scan
(ARCHITECTURE.md, sections 3, 4.1, and 6). The AST test of
tests/test_registry.py enforces this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Tuple

from ..context import Context, shell_path
from ..docs import Txn
from ..model import AdoptOptions, Choices, Facts, Item, Page

if TYPE_CHECKING:  # declared reads the facts of config_files, a migrator
    from ..declared import Declared


# The intros of "Not carried, by your choice": items that were on by default and that you deselected.
# Homebrew formulae and casks get the first one.
NOT_CARRIED_BREW = ("You left these out of the lists. They stay on this Mac, and dotkit does not manage them. "
                    "`dotkit upgrade brew` upgrades only the packages in the lists, so these go stale. With "
                    "`homebrew.onActivation.cleanup = \"uninstall\"` in nix/darwin.nix, activation would uninstall "
                    "them.")
NOT_CARRIED = "You left these out. They stay where they are, and dotkit does not manage them."


def left_out(pages: List[Page], choices: Choices, item_id: str) -> bool:
    """An item of these pages that was on by default and that you deselected: "Not carried, by your choice".

    An item that is off by default keeps the to-do of its kind, which says why it is off.
    """
    for page in pages:
        for item in page.items:
            if item.id == item_id:
                return item.default and not choices.selected(page.key, item_id)
    return False


def and_list(names: List[str]) -> str:
    """Names in a sentence: "a", "a and b", "a, b, and c"."""
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def before_removal(path: str, directory: bool = True) -> str:
    """A removal command and its reference check (ARCHITECTURE.md, section 11), to follow "Before " or "before ".

    The path has the home as ~: "`rm -rf ~/.pyenv`, check that nothing points into it: ...".
    """
    q = shell_path(path)
    command, into = (f"rm -rf {q}", "into") if directory else (f"rm {q}", "to")
    return (f"`{command}`, check that nothing points {into} it: LaunchAgents, IDE interpreter settings, git hooks, "
            f"crontab (`grep -rl {q} ~/Library/LaunchAgents`)")


def unique_items(items: List[Item]) -> List[Item]:
    """The items without repeats: a package in two places is one item."""
    out: Dict[str, Item] = {}
    for i in items:
        out.setdefault(i.id, i)
    return list(out.values())


class Migrator:
    key: str = ""  # stable id: the facts key, the Txn owner, the test name, Failure.migrator
    title: str = ""  # human name for problems and warnings
    needs: Tuple[str, ...] = ()  # keys of the migrators it cannot work without: it is skipped when one failed
    sections: Tuple[str, ...] = ()  # the report sections it writes, each in report.SECTIONS

    def scan(self, ctx: Context) -> object:
        """Read-only: this migrator's facts. Never writes."""
        return None

    def pages(self, facts: Facts) -> List[Page]:
        """The questions. Depends only on facts. Item defaults are the --yes behavior."""
        return []

    def plan(self, facts: Facts, choices: Choices, txn: Txn) -> None:
        """Add to the documents and the report through txn. Runs no command but facts.check_syntax."""

    def adopt(self, facts: Facts, declared: "Declared", txn: Txn, opts: AdoptOptions) -> None:
        """`dotkit migrate`: add to the repository's documents what it lacks, and what was not added to
        the report through txn. Default: nothing."""

    def manual_steps(self, ctx: Context) -> str:
        """What a person does by hand when this migrator failed: what happens without it, and what to do.

        It describes the plain template, as the run has nothing of this
        migrator. tests/test_registry.py checks that every migrator has one.
        """
        return ""
