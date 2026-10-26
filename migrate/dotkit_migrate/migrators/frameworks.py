"""Shell frameworks (Oh My Zsh, Powerlevel10k, ...): report only.

dotkit's zsh does not load them; the shell migrator reports their lines,
and this migrator says how to remove each one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from .. import catalog
from ..context import Context, is_managed
from ..docs import Txn
from ..model import Choices, Facts
from . import Migrator, and_list, before_removal


@dataclass
class FrameworkFacts:
    found: Dict[str, str] = field(default_factory=dict)  # name -> where, with ~


def facts_of(f: Facts) -> Optional[FrameworkFacts]:
    return f.get(Frameworks.key)


class Frameworks(Migrator):
    key = "frameworks"
    title = "Shell frameworks"
    sections = ("Shell frameworks",)

    def manual_steps(self, ctx: Context) -> str:
        names = and_list([spec.name for spec in catalog.FRAMEWORKS])
        return (f"MIGRATION.md does not list your shell frameworks. dotkit's zsh does not load {names}. Their "
                "directories stay on disk; remove them when you no longer need them.")

    def scan(self, ctx: Context) -> FrameworkFacts:
        fw = FrameworkFacts()
        for spec in catalog.FRAMEWORKS:
            for rel in spec.paths:
                p = ctx.home / rel
                if p.exists() and not is_managed(p):
                    fw.found[spec.name] = "~/" + rel
                    break
        return fw

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        fw = facts_of(f)
        if fw is not None and fw.found:
            txn.report.todo("Shell frameworks",
                            "dotkit's zsh does not load them. Remove each one when you no longer need it.",
                            [f"{n}: before {before_removal(p)}." for n, p in fw.found.items()])
