"""The part of the migration that is not split into migrators yet: the last entry of MIGRATORS.

It runs the old scan (scan.Scanner) and the old planning steps (plan.py).
Each migrator that moves out takes its scan step, its pages, and its
planning step from here. Nothing of it is left at the end of the split.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .. import plan as plan_mod
from .. import scan as scan_mod
from .. import secrets
from .._compat import tomllib
from ..context import Context
from ..docs import Txn
from ..model import Choices, Facts, Page
from . import Migrator


@dataclass
class LayeredMise:
    """Your mise config, when it goes into the repository as config/mise.toml: its tools and settings."""

    tools: Dict[str, object] = field(default_factory=dict)
    settings: Dict[str, object] = field(default_factory=dict)


def layered_mise(f: Facts, c: Choices) -> Optional[LayeredMise]:
    """Your mise config, if it is carried as "layer", or None.

    The tool migrators skip its tools: the order of MiseDoc.set_base and
    add_tool must not matter.
    """
    s = f.get(Rest.key)
    if s is None or not (s.mise_config and c.option("file:.config/mise/config.toml") == "layer"
                         and not secrets.has_secret(s.mise_config)):
        return None
    try:
        data = tomllib.loads(s.mise_config.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        data = {}
    tools, settings = data.get("tools"), data.get("settings")
    return LayeredMise(tools if isinstance(tools, dict) else {}, settings if isinstance(settings, dict) else {})


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
        self._planner(facts).plan(choices, txn)
