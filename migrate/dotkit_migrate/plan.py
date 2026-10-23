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
from .migrators import config_files
from .model import Choices, Facts, Item, Page
from .report import plural
from .context import tilde
from .scan import Scan


class Planner:
    def __init__(self, scan: Scan, base: base_mod.Base | None = None):
        self.scan = scan
        self.base = base or base_mod.Base()

    # --- What the wizard shows

    def pages(self) -> list[Page]:
        """The questions, one page per source, in the order of the plan. Pages without items are left out."""
        s = self.scan
        out: list[Page] = []

        def checklist(key: str, title: str, dest: str, intro: str, items: list[Item], note: str = "") -> None:
            unique: dict[str, Item] = {}
            for i in items:  # a package in two places is one item
                unique.setdefault(i.id, i)
            out.append(Page(key, title, dest, intro, "checklist", list(unique.values()), note))

        other = []
        for o in s.other_config:
            if o.adoptable:
                what = (f"a directory with {plural(o.files, 'file')}") if o.is_dir else "a file"
                other.append(Item(f"other:{o.rel}", f"~/{o.rel}",
                                  f"~/{o.rel}: {what}. It goes to config/ and is linked back.", default=False))
        checklist("other", "Other config to keep in the repository", "config/",
                  "These are not part of dotkit. By default they stay in place and keep working. Select the ones "
                  "to copy into the repository.", other)
        return [p for p in out if p.items]

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
        self.other_config()
        self.secret_files()

    def other_config(self) -> None:
        r = self.r
        adopted, left, skipped, taken = [], [], [], []
        for o in self.s.other_config:
            if self.picked("other", f"other:{o.rel}") and o.adoptable:
                name = o.rel.removeprefix(".config/").lstrip(".")
                dest = f"config/{name}" if o.rel.startswith(".config/") else f"config/home/{name}"
                if self.txn.copies.add(dest, Path(self.home, o.rel)):
                    self.txn.links().link(o.rel, dest)
                    adopted.append(f"~/{o.rel} -> {dest}")
                else:
                    how, where = self.txn.copies.taken(dest)
                    src = self.txn.copies.source(where)
                    has = self.t(src) if src else config_files.merged_sources(self.facts, self.c).get(where, "another file")
                    keep = ("To keep it in the repository, copy it under another name and link it in "
                            "nix/home.nix.")
                    if how == "contains":
                        taken.append(f"~/{o.rel}: its place in the repository, {dest}, would contain {where}; "
                                     f"it stays in place. {keep}")
                    elif how == "inside":
                        taken.append(f"~/{o.rel}: its place in the repository, {dest}, is inside {where}, "
                                     f"which has {has} already. It stays in place. {keep}")
                    else:
                        taken.append(f"~/{o.rel}: its place in the repository, {dest}, has {has} already. It "
                                     f"stays in place. {keep}")
            elif o.adoptable:
                left.append(f"~/{o.rel}")
            elif o.reason not in ("empty", "a Home Manager link"):
                skipped.append(f"~/{o.rel}: {o.reason}")
        if adopted:
            r.carried(plural(len(adopted), "other config file or directory", "other config files and directories"),
                      "config/, linked with dotkit.links")
        if taken:
            r.todo("Other config", "", taken)
        if left:
            r.todo("Other config", "These stay in place and keep working. To keep one in the repository, copy "
                                   "it to config/ and link it in nix/home.nix, for example "
                                   "`dotkit.links.\".config/nvim\" = \"config/nvim\";`.", left)
        if skipped:
            r.todo("Other config that stays out of the repository",
                   "These stay in place. They hold state, caches, or logins, so they are not for Git.", skipped)

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
