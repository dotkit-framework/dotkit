"""The migrators: one source of the Mac each, from its scan to its part of the plan.

registry.MIGRATORS has them in order. A migrator imports only context,
catalog, model, docs (through the Txn it gets), rcparse, secrets, base, and
its own helpers; it never writes a file and never runs a command outside its
scan (ARCHITECTURE.md, section 4.1).
"""

from __future__ import annotations

from typing import List, Tuple

from ..context import Context
from ..docs import Txn
from ..model import Choices, Facts, Page


class Migrator:
    key: str = ""  # stable id: the facts key, the Txn owner, the test name
    title: str = ""  # human name for problems and warnings
    needs: Tuple[str, ...] = ()  # facts it cannot work without
    sections: Tuple[str, ...] = ()  # the report sections it writes

    def scan(self, ctx: Context) -> object:
        """Read-only: this migrator's facts. Never writes."""
        return None

    def pages(self, facts: Facts) -> List[Page]:
        """The questions. Depends only on facts. Item defaults are the --yes behavior."""
        return []

    def plan(self, facts: Facts, choices: Choices, txn: Txn) -> None:
        """Add to the documents and the report through txn. Runs no command but facts.check_syntax."""
