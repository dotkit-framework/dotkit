"""The part of the migration that is not split into migrators yet: the last entry of MIGRATORS.

It runs the old scan (scan.Scanner) and the old planning steps (plan.py).
Each migrator that moves out takes its scan step, its pages, and its
planning step from here. Nothing of it is left at the end of the split.
"""

from __future__ import annotations

from typing import List

from .. import plan as plan_mod
from .. import scan as scan_mod
from ..context import Context
from ..docs import Txn
from ..model import Choices, Facts, Page
from . import Migrator


class Rest(Migrator):
    key = "_rest"
    title = "The rest of the migration"

    def scan(self, ctx: Context) -> scan_mod.Scan:
        return scan_mod.Scanner(ctx).run()

    def _planner(self, facts: Facts) -> plan_mod.Planner:
        return plan_mod.Planner(facts.get(self.key), facts.base)

    def pages(self, facts: Facts) -> List[Page]:
        return self._planner(facts).pages()

    def plan(self, facts: Facts, choices: Choices, txn: Txn) -> None:
        self._planner(facts).plan(facts, choices, txn)
