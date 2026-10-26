"""Homebrew: the formulae and casks that you installed, and the taps, to nix/brews.txt, nix/casks.txt, nix/taps.txt.

Homebrew packages stay in Homebrew. A package that the dotkit base has
already is not an item; the report says how to remove the Homebrew copy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..catalog import BREW_ENV
from ..context import Context
from ..declared import YOUR_MISE, Declared
from ..docs import Txn, nix_string, tap_name_problem, tap_problem
from ..model import AdoptOptions, Choices, Facts, Item, Page
from ..report import plural
from . import NOT_CARRIED, NOT_CARRIED_BREW, Migrator, left_out, unique_items

# The default taps of Homebrew. Other homebrew/* taps are old and merged.
DEFAULT_TAPS = {"homebrew/core", "homebrew/cask"}


@dataclass
class Tap:
    name: str
    remote: str = ""
    custom: bool = False  # the remote is not github.com/<user>/homebrew-<repo>


@dataclass
class BrewFacts:
    brew: str = ""  # the brew command, "" without Homebrew
    formulae: List[str] = field(default_factory=list)
    casks: List[str] = field(default_factory=list)
    taps: List[Tap] = field(default_factory=list)
    old_taps: List[str] = field(default_factory=list)  # homebrew/* taps other than core and cask


def facts_of(f: Facts) -> Optional[BrewFacts]:
    return f.get(Homebrew.key)


def tap_line(t: Tap) -> str:
    """The line of a tap in nix/taps.txt: the name, and the clone URL of a tap that is not on GitHub."""
    return f"{t.name} {t.remote}" if t.custom else t.name


def tap_line_problem(t: Tap) -> Optional[str]:
    """Why a tap cannot go into nix/taps.txt, or None: its name first, as the name has no spaces."""
    return tap_name_problem(t.name) or tap_problem(tap_line(t))


def tap_todo(t: Tap, problem: str) -> str:
    """The to-do of a tap that cannot go into nix/taps.txt, for its tap_line_problem()."""
    if problem.startswith("its name"):
        return f"`{t.name}`: it cannot go into nix/taps.txt: {problem}."
    if problem.startswith("it has a secret"):
        return (f"`{t.name}`: its URL has a secret ({problem[len('it has a secret ('):-1]}), so it is not in "
                "nix/taps.txt. Take the credential out of the URL (for example with an SSH URL or a git "
                "credential helper), then add the tap to nix/taps.txt. Do not put the URL with the credential "
                "into a file that Git tracks.")
    return (f"`{t.name}`: its URL cannot go into nix/taps.txt ({problem}). Add it by hand to `homebrew.taps` in "
            f"nix/darwin.nix as `{{ name = {nix_string(t.name)}; clone_target = {nix_string(t.remote)}; }}`.")


def good_taps(taps: List[Tap]) -> List[Tap]:
    """The taps that can go into nix/taps.txt, each name once."""
    out: Dict[str, Tap] = {}
    for t in taps:
        if t.name not in out and tap_line_problem(t) is None:
            out[t.name] = t
    return list(out.values())


def split(f: Facts, names: List[str], kind: str) -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
    """The names that nothing declares; those that the base has, with what provides them (Declared.brew);
    those that your mise config has, with its key."""
    declared = Declared.for_setup(f.base, f)
    keep, base, yours = [], {}, {}
    suffix = f" ({YOUR_MISE})"
    for n in names:
        what = declared.brew(n, kind)
        if not what:
            keep.append(n)
        elif what.endswith(suffix):
            yours[n] = what[:-len(suffix)]
        else:
            base[n] = what
    return keep, base, yours


def flagged_todo(entry: str, rel: str, how: str) -> str:
    """The to-do of a formula or cask whose line the secret check flags: it is not added."""
    return f"`{entry}`: dotkit's secret check flags its line in {rel}, so it is not added. Add it by hand: {how}."


def flagged_tap(name: str) -> str:
    """The to-do of a tap whose line the secret check flags: by its name only, as the line is what is flagged."""
    return (f"`{name}`: dotkit's secret check flags its line for nix/taps.txt, so it is not added. Add it by hand; "
            f"`brew tap-info {name}` shows its URL.")


class Homebrew(Migrator):
    key = "homebrew"
    title = "Homebrew packages and taps"
    sections = ("Secrets", "Homebrew copies of dotkit tools", "Not carried, by your choice", "Homebrew taps")

    def manual_steps(self, ctx: Context) -> str:
        return ("None of your Homebrew formulae, casks, and taps is in nix/brews.txt, nix/casks.txt, or "
                "nix/taps.txt. They stay installed, and dotkit does not manage them. With "
                "`homebrew.onActivation.cleanup = \"uninstall\"` in nix/darwin.nix, activation would uninstall "
                "them. `brew leaves --installed-on-request`, `brew list --cask`, and `brew tap` list them. Add the "
                "ones you want with `dotkit add brew:<name>` and `dotkit add cask:<name>`, and the taps to "
                "nix/taps.txt.")

    def scan(self, ctx: Context) -> BrewFacts:
        b = BrewFacts()
        b.brew = ctx.runner.which("brew", ("/opt/homebrew/bin",)) or ""
        if not b.brew:
            return b

        def brew(*args: str) -> List[str]:
            out = ctx.runner.run([b.brew, *args], timeout=300, env=BREW_ENV)
            return [line.strip() for line in (out or "").splitlines() if line.strip()]

        b.formulae = brew("leaves", "--installed-on-request")
        b.casks = brew("list", "--cask", "--full-name", "-1")
        out = ctx.runner.run([b.brew, "tap-info", "--json", "--installed"], timeout=300, env=BREW_ENV)
        try:
            taps = json.loads(out or "[]")
        except ValueError:
            taps = [{"name": n} for n in brew("tap")]
        for t in taps:
            name = t.get("name", "")
            if not name or name in DEFAULT_TAPS:
                continue
            if name.startswith("homebrew/"):
                b.old_taps.append(name)
                continue
            remote = t.get("remote") or ""
            user, _, repo = name.partition("/")
            usual = re.fullmatch(
                rf"https://github\.com/{re.escape(user)}/homebrew-{re.escape(repo)}(\.git)?/?", remote, re.I
            )
            b.taps.append(Tap(name, remote, custom=bool(remote) and not usual))
        return b

    def pages(self, f: Facts) -> List[Page]:
        b = facts_of(f)
        if b is None:
            return []
        formulae, _, _ = split(f, b.formulae, "formula")
        casks, _, _ = split(f, b.casks, "cask")
        lines = [tap_line(t) for t in good_taps(b.taps)]
        return [
            Page("formulae", "Homebrew formulae", "nix/brews.txt",
                 "The formulae that you installed with Homebrew. The selected ones go to the list; all of them "
                 "stay installed. MIGRATION.md lists the ones that the dotkit base has already.", "checklist",
                 unique_items([Item(f"brew:{n}", n, f"`dotkit add brew:{n}` adds it later.") for n in formulae])),
            Page("casks", "Homebrew casks", "nix/casks.txt",
                 "The apps that you installed with Homebrew. The selected ones go to the list; all of them stay "
                 "installed. MIGRATION.md lists the ones that the dotkit base has already.", "checklist",
                 unique_items([Item(f"cask:{n}", n, f"`dotkit add cask:{n}` adds it later.") for n in casks])),
            Page("taps", "Homebrew taps", "nix/taps.txt",
                 "The taps of Homebrew. The selected ones go to the list, so that a new Mac has them too.",
                 "checklist",
                 unique_items([Item(f"tap:{line.split()[0]}", line.split()[0],
                               f"From {line.split()[1]}" if " " in line else "") for line in lines])),
        ]

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        b, r = facts_of(f), txn.report
        if b is None or not b.brew:
            return
        pages = self.pages(f)
        left: List[str] = []
        for kind, names, key, dest in (("formula", b.formulae, "formulae", "nix/brews.txt"),
                                       ("cask", b.casks, "casks", "nix/casks.txt")):
            keep, redundant, yours = split(f, names, kind)
            flag = "--cask " if kind == "cask" else ""
            prefix = "cask" if kind == "cask" else "brew"
            chosen = [n for n in keep if c.selected(key, f"{prefix}:{n}")]
            flagged = [n for n in chosen if txn.list(dest).flagged(n)]
            if flagged:
                r.todo("Secrets", "", [flagged_todo(n, dest, f"`dotkit add {prefix}:{n}`") for n in flagged])
            chosen = [n for n in chosen if n not in flagged]
            for n in chosen:
                txn.list(dest).add(n)
            if chosen:
                r.carried(plural(len(chosen), "Homebrew formula" if kind == "formula" else "Homebrew cask",
                                 "Homebrew formulae" if kind == "formula" else "Homebrew casks"), dest)
            left += [f"`{n}` ({kind}): `dotkit add {prefix}:{n}`" for n in keep
                     if n not in chosen and left_out(pages, c, f"{prefix}:{n}")]
            if redundant:
                r.todo("Homebrew copies of dotkit tools",
                       "The dotkit base has these tools, so they are not in your lists. The Homebrew copy "
                       "stays installed. Remove it when nothing else needs it; brew says when another "
                       "formula depends on it.",
                       [f"`{n}`: dotkit has {what}. `brew uninstall {flag}{n}`" for n, what in redundant.items()])
            if yours:
                r.todo("Homebrew copies of dotkit tools",
                       "Your mise config has these tools, so they are not in your lists. The Homebrew copy stays "
                       "installed. Remove it when nothing else needs it; brew says when another formula depends "
                       "on it.",
                       [f"`{n}`: your mise config has {key}. `brew uninstall {flag}{n}`" for n, key in yours.items()])
        lines = [tap_line(t) for t in good_taps(b.taps)]
        todos = [tap_todo(t, tap_line_problem(t)) for t in b.taps if tap_line_problem(t)]
        chosen = [line for line in lines if c.selected("taps", f"tap:{line.split()[0]}")]
        todos += [flagged_tap(line.split()[0]) for line in chosen if txn.list("nix/taps.txt").flagged(line)]
        chosen = [line for line in chosen if not txn.list("nix/taps.txt").flagged(line)]
        for line in chosen:
            txn.list("nix/taps.txt").add(line)
        if chosen:
            r.carried(plural(len(chosen), "Homebrew tap"), "nix/taps.txt")
            names = {line.split()[0] for line in chosen}
            custom = [t for t in b.taps if t.custom and t.name in names]
            if custom:
                r.todo("Homebrew taps", "These taps are not on GitHub, so the list has their URL. Check it.",
                       [f"`{t.name}`: {t.remote}" for t in custom])
        if left:
            r.todo("Not carried, by your choice", NOT_CARRIED_BREW, left)
        # A tap whose line the secret check flags is named without its line.
        taps_left = [f"`{line.split()[0]}` (tap): add it to nix/taps.txt by hand; `brew tap-info {line.split()[0]}` "
                     "shows its URL." if txn.list("nix/taps.txt").flagged(line) else
                     f"`{line.split()[0]}` (tap): add `{line}` to nix/taps.txt" for line in lines
                     if line not in chosen and left_out(pages, c, f"tap:{line.split()[0]}")]
        if taps_left:
            r.todo("Not carried, by your choice", NOT_CARRIED, taps_left)
        if todos:
            r.todo("Homebrew taps", "", todos)
        if b.old_taps:
            r.todo("Homebrew taps", "Homebrew merged these taps into its own. Remove them.",
                   [f"`{t}`: `brew untap {t}`" for t in b.old_taps])

    def adopt(self, f: Facts, declared: Declared, txn: Txn, opts: AdoptOptions) -> None:
        b = facts_of(f)
        if b is None or not b.brew:
            return
        for kind, names, dest in (("formula", b.formulae, "nix/brews.txt"), ("cask", b.casks, "nix/casks.txt")):
            prefix = "cask" if kind == "cask" else "brew"
            for n in names:
                if declared.brew(n, kind):
                    continue
                if txn.list(dest).flagged(n):
                    txn.report.todo("Secrets", "", [flagged_todo(n, dest, f"`dotkit add {prefix}:{n}`")])
                else:
                    txn.list(dest).add(n)
        todos = []
        for t in b.taps:
            if declared.tap(t.name):
                continue
            problem = tap_line_problem(t)
            if problem:
                todos.append(tap_todo(t, problem))
            elif txn.list("nix/taps.txt").flagged(tap_line(t)):
                todos.append(flagged_tap(t.name))
            else:
                txn.list("nix/taps.txt").add(tap_line(t))
        if todos:
            txn.report.todo("Homebrew taps", "Homebrew taps, not added:", todos)
