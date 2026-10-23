"""What the tool migrators share: npm, pipx, cargo, and Go tools that move to mise.

node_tools, python_tools, and built_tools each scan their package managers
into Packages; the page and the planning step are the same for all of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

from .. import catalog
from ..context import Context, shell_path
from ..docs import Txn
from ..model import Choices, Facts, Item, Page
from ..report import plural
from . import NOT_CARRIED, left_out, unique_items
from . import config_files


@dataclass
class Package:
    name: str
    version: str = ""
    where: str = ""  # where it is installed, for the report
    ok: bool = True  # False: it cannot go to a mise backend; note says why
    note: str = ""


def env_path(ctx: Context, var: str, default: str) -> Path:
    """The directory in the environment variable, or the default in the home directory."""
    v = ctx.env.get(var)
    return Path(v).expanduser() if v else ctx.home / default


def tilde_path(ctx: Context, p: Path) -> str:
    """A path below the home directory with ~ for it; other paths as they are."""
    s, home = str(p), str(ctx.home)
    return "~" + s[len(home):] if s.startswith(home + "/") else s


def page(key: str, title: str, intro: str, pkgs: List[Package]) -> Page:
    """The page of one package manager: the packages that can go to mise. cargo and Go are off by default."""
    off = key in catalog.BUILT_FROM_SOURCE
    return Page(key, title, "config/mise.toml", intro, "checklist",
                unique_items([Item(f"{key}:{p.name}", p.name, f"{p.version or 'installed'} in {p.where}",
                                   default=not off) for p in pkgs if p.ok]),
                f"off: mise builds them from source with {key}" if off else "")


def plan_tools(f: Facts, c: Choices, txn: Txn, pages: List[Page], key: str, pkgs: Optional[List[Package]],
               comment: str, remove: str) -> List[Package]:
    """The selected tools of one package manager to config/mise.toml, and the report.

    Returns the selected tools that go to mise: carried, or a to-do when your
    mise config is kept in place.

    A tool that your mise config has, when it is carried as "layer", is not
    added again: config_files sets that file as the base of config/mise.toml.
    When your mise config is kept in place, mise does not read
    config/mise.toml: nothing is added, and each selected tool that your
    config does not have is a to-do of the "mise" section.
    """
    r = txn.report
    mise = txn.mise()
    user = config_files.layered_mise(f, c)
    kept = config_files.mise_target(f, c) == "kept"
    yours = config_files.user_mise_tools(f) if kept else {}
    dest = config_files.mise_dest(f, c)
    carried, manual, old, left, todo, to_mise = [], [], [], [], [], []
    seen: Set[str] = set()
    for p in pkgs or []:
        if key == "npm" and p.name in catalog.NPM_NOT_IN_MISE:
            manual.append(f"`{p.name}` ({p.where}): {catalog.NPM_NOT_IN_MISE[p.name]}")
            continue
        if not p.ok:
            manual.append(f"`{p.name}` ({p.where}): {p.note}. Add it to {dest} by hand.")
            continue
        spec = f"{key}:{p.name}"
        if c.selected(key, f"{key}:{p.name}"):
            if kept:
                # One item per spec: two Go binaries can have one module.
                if spec not in yours and spec not in seen:
                    seen.add(spec)
                    todo.append(f"`{p.name}` ({p.where}): `mise use -g {spec}`")
                    to_mise.append(p)
            elif not (user is not None and spec in user.tools) and mise.add_tool(spec, "latest", comment):
                carried.append(p)
                to_mise.append(p)
                rm = remove.format(name=p.name, path=shell_path(p.where),
                                   remove=("pipx uninstall " if p.where == "pipx" else "uv tool uninstall ")
                                   + p.name)
                old.append(f"`{spec}`, was {p.version or 'installed'} in {p.where}. "
                           f"Old copy: `{rm}`" + (f". Note: {p.note}" if p.note else ""))
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
    return to_mise
