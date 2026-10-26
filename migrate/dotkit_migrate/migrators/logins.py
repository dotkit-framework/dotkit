"""Files that hold logins (~/.netrc, ~/.config/gh/hosts.yml, ...): report only.

They stay in place and never go into the repository; the report says how
to log in again on a new Mac.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .. import secrets
from ..context import Context
from ..docs import Txn
from ..model import Choices, Facts
from . import Migrator, and_list

# How to get each login back on a new Mac.
STEPS = {
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


@dataclass
class LoginFacts:
    files: List[str] = field(default_factory=list)  # of secrets.SECRET_FILES, relative to the home


def facts_of(f: Facts) -> Optional[LoginFacts]:
    return f.get(Logins.key)


class Logins(Migrator):
    key = "logins"
    title = "Logins"
    sections = ("Logins",)

    def manual_steps(self, ctx: Context) -> str:
        files = and_list([f"~/{rel}" for rel in secrets.SECRET_FILES])
        return ("MIGRATION.md does not list your files with logins. They stay in place, and none of them is in "
                f"the repository: {files}. On a new Mac, log in again, or copy them by hand; do not put them "
                "into Git.")

    def scan(self, ctx: Context) -> LoginFacts:
        return LoginFacts([rel for rel in secrets.SECRET_FILES if (ctx.home / rel).exists()])

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        lg = facts_of(f)
        items = [f"~/{x}: {STEPS.get(x, 'copy it by hand')}" for x in (lg.files if lg else [])]
        if items:
            txn.report.todo("Logins", "These files hold logins. They stay in place and are not in the repository.",
                            items)
