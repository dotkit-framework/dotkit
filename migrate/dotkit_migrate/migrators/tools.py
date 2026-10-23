"""What the tool migrators share: npm, pipx, cargo, and Go tools that move to mise.

node_tools, python_tools, and built_tools each scan their package managers
into Packages; the page and the planning step are the same for all of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

from .. import catalog
from ..context import Context
from ..declared import Declared
from ..docs import Txn
from ..model import Choices, Facts, Item, Page
from ..report import plural
from . import NOT_CARRIED, before_removal, left_out, unique_items
from . import config_files


@dataclass
class Package:
    name: str
    version: str = ""
    where: str = ""  # where it is installed, for the report
    ok: bool = True  # False: it cannot go to a mise backend; note says why
    note: str = ""
    commands: List[str] = field(default_factory=list)  # the names of its commands, for the leftovers


def env_path(ctx: Context, var: str, default: str) -> Path:
    """The directory in the environment variable, or the default in the home directory."""
    v = ctx.env.get(var)
    return Path(v).expanduser() if v else ctx.home / default


def tilde_path(ctx: Context, p: Path) -> str:
    """A path below the home directory with ~ for it; other paths as they are."""
    s, home = str(p), str(ctx.home)
    return "~" + s[len(home):] if s.startswith(home + "/") else s


def page(key: str, title: str, intro: str, pkgs: List[Package], declared: Declared) -> Page:
    """The page of one package manager: the packages that can go to mise. cargo and Go are off by default.

    A tool that dotkit has already (Declared.tool) is not an item.
    """
    off = key in catalog.BUILT_FROM_SOURCE
    return Page(key, title, "config/mise.toml", intro, "checklist",
                unique_items([Item(f"{key}:{p.name}", p.name, f"{p.version or 'installed'} in {p.where}",
                                   default=not off) for p in pkgs if p.ok and not declared.tool(p.name)]),
                f"off: mise builds them from source with {key}" if off else "")


def old_copy(key: str, p: Package) -> str:
    """How to remove the copy of a tool that the scan found, for "Old copy: ...": a command, with the
    reference check when it removes a path."""
    if key == "npm":
        return f"`npm uninstall -g {p.name}`"
    if key == "pipx":
        return f"`{'pipx' if p.where == 'pipx' else 'uv tool'} uninstall {p.name}`"
    if key == "cargo":
        return f"`cargo uninstall {p.name}`"
    return "before " + before_removal(p.where, directory=False)


# The intro of the tools that dotkit has already, in "Tools that are not in mise".
DOTKIT_HAS = "dotkit has these tools already, so they are not added."


def flagged_text(p: Package, key: str) -> str:
    """The to-do of a tool whose line in config/mise.toml the secret check flags: it is not added."""
    return (f"`{p.name}` ({p.where}): dotkit's secret check flags its line in config/mise.toml, so it is not "
            f"added. Add it by hand: `mise use -g {key}:{p.name}`.")


def plan_tools(f: Facts, c: Choices, txn: Txn, pages: List[Page], key: str, pkgs: Optional[List[Package]],
               comment: str) -> List[Package]:
    """The selected tools of one package manager to config/mise.toml, and the report.

    Returns the selected tools that go to mise: carried, or a to-do when your
    mise config is kept in place.

    A tool that your mise config has (Declared.yours_mise_key) is not added
    again: carried as "layer", config_files sets that file as the base of
    config/mise.toml. When your mise config is kept in place, mise does not
    read config/mise.toml: nothing is added, and each selected tool that your
    config does not have is a to-do of the "mise" section. A tool that
    dotkit has already (Declared.tool) is not added, and the report says so.
    A tool whose line the secret check would flag is not added either.
    """
    r = txn.report
    mise = txn.mise()
    declared = Declared.for_setup(f.base, f)
    kept = config_files.mise_target(f, c) == "kept"
    dest = config_files.mise_dest(f, c)
    carried, manual, old, left, todo, to_mise, has, flagged = [], [], [], [], [], [], [], []
    seen: Set[str] = set()

    for p in pkgs or []:
        if key == "npm" and p.name in catalog.NPM_NOT_IN_MISE:
            manual.append(f"`{p.name}` ({p.where}): {catalog.NPM_NOT_IN_MISE[p.name]}")
            continue
        if not p.ok:
            manual.append(f"`{p.name}` ({p.where}): {p.note}. Add it to {dest} by hand.")
            continue
        source = declared.tool_source(p.name)
        if source:
            has.append(f"`{p.name}` ({p.where}): dotkit has {source}. Old copy: {old_copy(key, p)}")
            continue
        spec = f"{key}:{p.name}"
        if c.selected(key, f"{key}:{p.name}"):
            if kept:
                # One item per spec: two Go binaries can have one module.
                if not declared.yours_mise_key(spec) and spec not in seen:
                    seen.add(spec)
                    todo.append(f"`{p.name}` ({p.where}): `mise use -g {spec}`")
                    to_mise.append(p)
            elif declared.yours_mise_key(spec):
                continue
            elif mise.flagged(spec, "latest"):
                flagged.append(flagged_text(p, key))
            elif mise.add_tool(spec, "latest", comment):
                carried.append(p)
                to_mise.append(p)
                old.append(f"`{spec}`, was {p.version or 'installed'} in {p.where}. "
                           f"Old copy: {old_copy(key, p)}" + (f". Note: {p.note}" if p.note else ""))
        elif left_out(pages, c, f"{key}:{p.name}"):
            left.append(f"`{p.name}` ({p.where}): `mise use -g {spec}`")
        else:
            manual.append(f"`{p.name}` ({p.where}): `mise use -g {spec}`")
    if todo:
        r.todo("mise", config_files.KEPT_INTRO, todo)
    if left:
        r.todo("Not carried, by your choice", NOT_CARRIED, left)
    if carried:
        r.carried(plural(len(carried), f"{key} tool"), "config/mise.toml")
        r.todo("Tools that moved to mise",
               "mise installs these at the end of setup (`mise install`). The old copies stay, and "
               "they can come first in PATH. Remove each one when the mise copy works.",
               old)
    if manual and key in catalog.BUILT_FROM_SOURCE:
        r.todo("Tools that are not in mise", f"mise builds {catalog.BUILT_FROM_SOURCE[key]} tools from source, so "
               f"they are off by default. They stay where they are. To add one to {dest}:", manual)
    elif manual:
        r.todo("Tools that are not in mise", f"They stay where they are. To add one to {dest}:", manual)
    if has:
        r.todo("Tools that are not in mise", DOTKIT_HAS, has)
    if flagged:
        r.todo("Tools that are not in mise", "", flagged)
    return to_mise


def adopt_tools(declared: Declared, txn: Txn, key: str, pkgs: Optional[List[Package]], add: bool = True) -> List[str]:
    """`dotkit migrate`: the tools of one package manager that nothing declares, to config/mise.toml.

    Each added tool gets a "carried" row with where it was installed, for
    the output. A tool whose line the secret check would flag gets a to-do
    instead. With add=False, nothing is added, and the tools are returned as
    "<spec> (from <where>)".
    """
    left = []
    for p in pkgs or []:
        spec = f"{key}:{p.name}"
        if (not p.ok or (key == "npm" and p.name in catalog.NPM_NOT_IN_MISE) or declared.mise_key(spec)
                or declared.tool(p.name)):
            continue
        if not add:
            left.append(f"{spec} (from {p.where})")
        elif txn.mise().flagged(spec, "latest"):
            txn.report.todo("Tools that are not in mise", "", [flagged_text(p, key)])
        elif txn.mise().add_tool(spec, "latest"):
            txn.report.carried(spec, p.where)
    return left
