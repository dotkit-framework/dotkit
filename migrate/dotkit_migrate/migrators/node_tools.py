"""Global tools of npm, pnpm, bun, and Volta: to mise, as npm:<package>.

The scan reads the package directories and manifests; it never runs npm,
which writes a log file for each command.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .. import catalog
from ..context import Context, is_managed, read_json, read_text
from ..declared import Declared
from ..docs import Txn
from ..model import AdoptOptions, Choices, Facts, Page
from . import Migrator
from .tools import Package, adopt_tools, env_path, page, plan_tools, tilde_path


@dataclass
class NodeFacts:
    packages: List[Package] = field(default_factory=list)


def facts_of(f: Facts) -> Optional[NodeFacts]:
    return f.get(NodeTools.key)


def node_version_globs(ctx: Context) -> List[Tuple[Path, str, str, int]]:
    """The global npm packages of each Node version: (directory, glob in it, label, index of the version).

    The version managers first, each in its directory (catalog.manager_dir),
    then mise. The order decides the label of a directory that two globs find.
    """
    globs = []
    for spec in catalog.VERSION_MANAGERS:
        root = catalog.manager_dir(spec, ctx.home, ctx.env) if spec.npm_globs else None
        if root is not None:
            globs += [(root, pattern, spec.name, index) for pattern, index in spec.npm_globs]
    globs.append((ctx.home, ".local/share/mise/installs/node/*/lib/node_modules", "mise node", -3))
    return globs


def _node_module_roots(ctx: Context) -> List[Tuple[Path, str]]:
    home = ctx.home
    roots: List[Tuple[Path, str]] = [
        (ctx.brew_prefix / "lib/node_modules", "Homebrew node"),
        (home / ".npm-global/lib/node_modules", "~/.npm-global"),
        (home / ".local/lib/node_modules", "~/.local"),
    ]
    prefix = re.search(r"(?m)^\s*prefix\s*=\s*(.+?)\s*$", read_text(home / ".npmrc"))
    if prefix and not is_managed(home / ".npmrc"):
        p = prefix.group(1).replace("${HOME}", str(home)).replace("$HOME", str(home))
        roots.append((Path(p).expanduser() / "lib/node_modules", f"npm prefix {prefix.group(1)}"))
    for root, pattern, label, index in node_version_globs(ctx):
        for p in sorted(root.glob(pattern)):
            roots.append((p, f"{label} {p.parts[index]}"))
    # Keg-only Homebrew nodes, for example node@20.
    for p in sorted(ctx.brew_prefix.glob("opt/node@*/lib/node_modules")):
        roots.append((p, f"Homebrew {p.parts[-3]}"))
    # The npm on PATH, without running it: npm writes a log file for each
    # command, and the scan writes nothing. Its global packages are in
    # <prefix>/lib/node_modules, and its prefix is the directory above bin.
    for var in ("npm_config_prefix", "NPM_CONFIG_PREFIX"):
        if ctx.env.get(var):
            roots.append((Path(ctx.env[var]).expanduser() / "lib/node_modules", f"npm prefix {ctx.env[var]}"))
    npm = ctx.runner.which("npm")
    if npm:
        prefix = Path(npm).parent.parent
        roots.append((prefix / "lib/node_modules", f"npm in {tilde_path(ctx, prefix)}"))
    seen: Set[str] = set()
    unique = []
    for p, label in roots:
        key = os.path.realpath(p)
        if key not in seen and p.is_dir():
            seen.add(key)
            unique.append((p, label))
    return unique


class NodeTools(Migrator):
    key = "node_tools"
    title = "npm, pnpm, and bun tools"
    sections = ("Tools that moved to mise", "Tools that are not in mise", "mise", "Not carried, by your choice")

    def manual_steps(self, ctx: Context) -> str:
        return ("None of your global npm, pnpm, bun, and Volta tools is in config/mise.toml. They stay where "
                "they are, and mise does not install them. Add the ones you want with "
                "`mise use -g npm:<package>`.")

    def scan(self, ctx: Context) -> NodeFacts:
        found: Dict[str, Package] = {}

        def add(name: str, version: str, where: str, ok: bool = True, note: str = "") -> None:
            if name in ("npm", "corepack") or name in found:
                return
            found[name] = Package(name, version, where, ok, note)

        for root, label in _node_module_roots(ctx):
            entries = []
            for p in sorted(root.iterdir()):
                if p.name.startswith("@") and p.is_dir() and not p.is_symlink():
                    entries += sorted(p.iterdir())
                elif not p.name.startswith("."):
                    entries.append(p)
            for p in entries:
                meta = read_json(p / "package.json")
                if not isinstance(meta, dict) or not meta.get("bin"):
                    continue
                name = meta.get("name") or p.name
                if p.is_symlink():
                    add(name, "", label, False, "a linked local package (npm link)")
                    continue
                add(name, str(meta.get("version", "")), label)
        pnpm_home = env_path(ctx, "PNPM_HOME", "Library/pnpm")
        for manifest in sorted(pnpm_home.glob("global/*/package.json")):
            deps = (read_json(manifest) or {}).get("dependencies") or {}
            for name, version in sorted(deps.items()):
                add(name, str(version).lstrip("^~"), "pnpm")
        bun = env_path(ctx, "BUN_INSTALL", ".bun") / "install/global/package.json"
        for name, version in sorted(((read_json(bun) or {}).get("dependencies") or {}).items()):
            add(name, str(version).lstrip("^~"), "bun")
        volta = catalog.manager_dir(catalog.version_manager("volta"), ctx.home, ctx.env)
        for p in sorted(volta.glob("tools/user/packages/*.json")) if volta else []:
            meta = read_json(p) or {}
            if meta.get("name"):
                add(meta["name"], str(meta.get("version", "")), "volta")
        return NodeFacts(sorted(found.values(), key=lambda p: p.name))

    def pages(self, f: Facts) -> List[Page]:
        n = facts_of(f)
        if n is None:
            return []
        return [page("npm", "npm, pnpm, and bun tools",
                     "Global tools of npm, pnpm, and bun. The selected ones move to mise; the old copies stay "
                     "until you remove them.",
                     [p for p in n.packages if p.name not in catalog.NPM_NOT_IN_MISE],
                     Declared.for_setup(f.base, f))]

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        n = facts_of(f)
        if n is None:
            return
        plan_tools(f, c, txn, self.pages(f), "npm", n.packages,
                   "# From npm, pnpm, and bun (global packages), see MIGRATION.md.", "npm uninstall -g {name}")

    def adopt(self, f: Facts, declared: Declared, txn: Txn, opts: AdoptOptions) -> None:
        n = facts_of(f)
        if n is not None:
            adopt_tools(declared, txn, "npm", n.packages)
