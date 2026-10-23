"""Turn a scan and the choices into a plan: the files of the new repository and the report.

Safe defaults: Homebrew packages stay in Homebrew and go to the lists, tools
of other package managers go to mise, safe shell lines are carried, and
config files stay yours. Nothing is installed or uninstalled. Secrets never
go into a file that Git tracks.
"""

from __future__ import annotations

from pathlib import Path

from . import base as base_mod
from . import docs as docs_mod
from .model import Choices, Facts
from .context import tilde
from .scan import Scan


class Planner:
    def __init__(self, scan: Scan, base: base_mod.Base | None = None):
        self.scan = scan
        self.base = base or base_mod.Base()

    # --- The plan

    def plan(self, facts: Facts, c: Choices, txn: docs_mod.Txn) -> None:
        _Build(self, facts, c, txn).run()


class _Build:
    def __init__(self, planner: Planner, facts: Facts, c: Choices, txn: docs_mod.Txn):
        self.p = planner
        self.facts = facts
        self.s = planner.scan
        self.base = planner.base
        self.c = c
        self.home = self.s.home
        # The planning steps write only through this transaction.
        self.txn = txn
        self.r = self.txn.report

    def t(self, path: str | Path) -> str:
        return tilde(path, self.home)

    def picked(self, page: str, item_id: str) -> bool:
        return self.c.selected(page, item_id)

    def run(self) -> None:
        self.secret_files()

    def secret_files(self) -> None:
        steps = {
            ".config/gh/hosts.yml": "on a new Mac, `gh auth login`",
            ".netrc": "copy it by hand",
            ".config/github-copilot": "sign in to Copilot again on a new Mac",
            ".config/gcloud": "on a new Mac, `gcloud auth login`",
            ".config/configstore": "the tools that use it ask again on a new Mac",
            ".config/op": "sign in to 1Password again on a new Mac",
            ".docker/config.json": "on a new Mac, `docker login`",
            ".aws/credentials": "on a new Mac, `aws configure`",
            ".gem/credentials": "on a new Mac, `gem signin`",
            ".pypirc": "copy it by hand",
        }
        items = [f"~/{f}: {steps.get(f, 'copy it by hand')}" for f in self.s.secret_files]
        if items:
            self.r.todo("Logins", "These files hold logins. They stay in place and are not in the repository.",
                        items)
