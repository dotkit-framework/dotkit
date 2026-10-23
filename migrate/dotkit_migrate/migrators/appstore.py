"""App Store apps: the apps that mas lists, to nix/mas.txt.

Without mas, the scan finds the App Store receipts in /Applications, which
have no IDs; the report says how to add them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..context import Context
from ..declared import Declared
from ..docs import ListView, Txn, mas_problem
from ..model import AdoptOptions, Choices, Facts, Item, Page
from ..report import plural
from . import NOT_CARRIED, Migrator, left_out


@dataclass
class AppStoreFacts:
    mas: bool = False  # mas listed the apps
    apps: List[Tuple[int, str]] = field(default_factory=list)  # (ID, name)
    store_apps: List[str] = field(default_factory=list)  # App Store apps without mas: names only


def facts_of(f: Facts) -> Optional[AppStoreFacts]:
    return f.get(AppStore.key)


def add_app(i: int, n: str, declared: Declared, mas: ListView) -> Tuple[str, str]:
    """Add an App Store app to nix/mas.txt: (a note on its label, a to-do when it is not added); "" for none.

    homebrew.masApps lists apps by name, and the name is only a label. An app
    whose name is taken (Declared.mas_name, or the nix/mas.txt document) gets
    the label `<name> (<ID>)`. An app whose line the secret check would flag
    is not added.
    """
    n = n.strip()
    problem = mas_problem(i, n)
    if problem:
        return "", f"`{n}` (ID {i}): it cannot go into nix/mas.txt: {problem}."
    names = set(mas.names())
    label = f"{n} ({i})" if declared.mas_name(n) or n in names else n
    if label != n and (declared.mas_name(label) or label in names):
        return "", (f"`{n}` (ID {i}): other apps have this name and `{label}`, so it is not in nix/mas.txt. "
                    f"Add it there under another name: `{i} <name>`.")
    if mas.flagged(f"{i} {label}"):
        # Without its name: the name is what the check flags.
        return "", (f"The App Store app with ID {i}: dotkit's secret check flags its line in nix/mas.txt, so it is "
                    f"not added. Add it by hand: `dotkit add mas:{i}`.")
    mas.add(f"{i} {label}")
    if label != n:
        return f"`{n}` (ID {i}): another app has this name, so nix/mas.txt lists it as `{label}`.", ""
    return "", ""


class AppStore(Migrator):
    key = "appstore"
    title = "App Store apps"
    sections = ("App Store apps", "Not carried, by your choice")

    def manual_steps(self, ctx: Context) -> str:
        return ("None of your App Store apps is in nix/mas.txt. They stay installed, and a new Mac does not get "
                "them. `mas list` lists them (without mas, run `dotkit add brew:mas` first). Add the ones you "
                "want with `dotkit add mas:<ID>`.")

    def scan(self, ctx: Context) -> AppStoreFacts:
        a = AppStoreFacts()
        mas = ctx.runner.which("mas", ("/opt/homebrew/bin",))
        if mas:
            out = ctx.runner.run([mas, "list"], timeout=60)
            if out is not None:
                a.mas = True
                for line in out.splitlines():
                    m = re.match(r"^\s*(\d+)\s+(.+?)\s+\(([^)]*)\)\s*$", line)
                    # An app without a name cannot go into nix/mas.txt.
                    if m and m.group(2).strip():
                        a.apps.append((int(m.group(1)), m.group(2).strip()))
                return a
        try:
            apps = sorted(ctx.applications.glob("*.app/Contents/_MASReceipt/receipt"))
        except OSError:
            apps = []
        a.store_apps = [p.parents[2].name.removesuffix(".app") for p in apps]
        return a

    def pages(self, f: Facts) -> List[Page]:
        a = facts_of(f)
        if a is None:
            return []
        return [Page("mas", "App Store apps", "nix/mas.txt",
                     "The apps that mas lists. The selected ones go to the list, and a new Mac installs them from "
                     "the App Store.", "checklist",
                     [Item(f"mas:{i}", n.strip(), f"ID {i}") for i, n in a.apps if mas_problem(i, n.strip()) is None])]

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        a, r = facts_of(f), txn.report
        if a is None:
            return
        pages = self.pages(f)
        items = {i.id for page in pages for i in page.items}
        # The apps that cannot go into the list are not items; add_app() writes their to-dos.
        chosen = [(i, n) for i, n in a.apps if c.selected("mas", f"mas:{i}") or f"mas:{i}" not in items]
        declared = Declared.for_setup(f.base, f)
        mas = txn.list("nix/mas.txt")
        notes, todos = [], []
        for i, n in chosen:
            note, todo = add_app(i, n, declared, mas)
            notes += [note] if note else []
            todos += [todo] if todo else []
        added = len(chosen) - len(todos)
        if added:
            r.carried(plural(added, "App Store app"), "nix/mas.txt")
        if notes or todos:
            r.todo("App Store apps", "", notes + todos)
        # An app whose line the secret check flags is named by its ID only.
        left = [f"The App Store app with ID {i}: `dotkit add mas:{i}`" if mas.flagged(f"{i} {n.strip()}") else
                f"`{n.strip()}` (App Store app): `dotkit add mas:{i}`"
                for i, n in a.apps if (i, n) not in chosen and left_out(pages, c, f"mas:{i}")]
        if left:
            r.todo("Not carried, by your choice", NOT_CARRIED, left)
        if a.store_apps:
            r.todo("App Store apps",
                   "These apps came from the App Store. dotkit installs App Store apps with mas, which was not "
                   "on this Mac, so the scan has no IDs. Run `dotkit add brew:mas`, then `mas list`, and "
                   "`dotkit add mas:ID` for each app.",
                   [f"{n}" for n in a.store_apps])

    def adopt(self, f: Facts, declared: Declared, txn: Txn, opts: AdoptOptions) -> None:
        a = facts_of(f)
        if a is None:
            return
        mas = txn.list("nix/mas.txt")
        notes, todos = [], []
        for i, n in a.apps:
            if declared.mas_id(i):
                continue
            note, todo = add_app(i, n, declared, mas)
            notes += [note] if note else []
            todos += [todo] if todo else []
        if notes:
            txn.report.todo("App Store apps", "App Store apps, added under another name:", notes)
        if todos:
            txn.report.todo("App Store apps", "App Store apps, not added:", todos)
