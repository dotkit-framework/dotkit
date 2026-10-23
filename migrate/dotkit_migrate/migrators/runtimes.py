"""Version managers (nvm, pyenv, asdf, ...): their runtime versions to config/mise.toml, off by default.

mise replaces them, and dotkit's shell does not load them; the report says
how to remove each one. Also the other global files that mise still reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .. import catalog
from ..context import Context, is_managed, read_text, version_key
from ..docs import Txn
from ..model import Choices, Facts, Item, Page
from . import Migrator, unique_items
from . import _rest


@dataclass
class VersionManager:
    name: str
    path: str
    tool: str  # the mise tool, "" for asdf (per plugin)
    versions: Dict[str, List[str]] = field(default_factory=dict)  # tool -> versions
    default: Dict[str, str] = field(default_factory=dict)  # tool -> the global version


@dataclass
class RuntimeFacts:
    managers: List[VersionManager] = field(default_factory=list)
    mise_extra: List[str] = field(default_factory=list)  # other global mise files, relative to the home


def facts_of(f: Facts) -> Optional[RuntimeFacts]:
    return f.get(Runtimes.key)


def runtime_lines(rt: Optional[RuntimeFacts]) -> Dict[str, List[str]]:
    """mise tool -> versions, default first, for the version managers mise can replace."""
    out: Dict[str, List[str]] = {}
    for vm in rt.managers if rt else []:
        if not catalog.version_manager(vm.name).mise_replaces:
            continue
        for tool, versions in vm.versions.items():
            vs = [v.lstrip("v") for v in versions if re.fullmatch(r"v?\d+(\.\d+)*", v)]
            d = vm.default.get(tool, "").lstrip("v")
            if d and re.fullmatch(r"\d+(\.\d+)*", d):
                # The default can be a prefix, as nvm's 20: it covers 20.11.0.
                vs = [d] + [v for v in vs if v != d and not v.startswith(d + ".")]
            elif vs:
                vs = vs[::-1]
            for v in vs:
                out.setdefault(tool, [])
                if v not in out[tool]:
                    out[tool].append(v)
    return out


def _managers(ctx: Context) -> List[VersionManager]:
    home = ctx.home
    out = []
    for spec in catalog.VERSION_MANAGERS:
        if not spec.detect:
            continue
        root = next((home / d for d in spec.dirs if (home / d).is_dir()), None)
        if root is None:
            continue
        vm = VersionManager(spec.name, "~/" + str(root.relative_to(home)), spec.tool)
        if not spec.tool:
            for plugin in sorted((root / spec.versions).glob("*")):
                t = catalog.ASDF_TOOLS.get(plugin.name, plugin.name)
                vm.versions[t] = sorted((v.name for v in plugin.iterdir() if v.is_dir()), key=version_key)
            for line in read_text(home / ".tool-versions").splitlines():
                parts = line.split("#")[0].split()
                if len(parts) >= 2:
                    vm.default[catalog.ASDF_TOOLS.get(parts[0], parts[0])] = parts[1]
        else:
            versions = [v.name for v in (root / spec.versions).glob("*") if v.is_dir() and v.name != "current"]
            if versions:
                vm.versions[spec.tool] = sorted(versions, key=version_key)
            default = spec.default(root) if spec.default else ""
            if default and default != "system":
                vm.default[spec.tool] = default
        out.append(vm)
    return out


def _mise_extra(ctx: Context) -> List[str]:
    """The global mise files other than your config, which config_files carries."""
    out = []
    for rel in (".tool-versions", ".config/mise/config.local.toml", ".mise.toml"):
        q = ctx.home / rel
        if q.is_file() and not is_managed(q):
            out.append(rel)
    for q in sorted((ctx.home / ".config/mise/conf.d").glob("*.toml")):
        if q.name != "dotkit.toml" and not is_managed(q):
            out.append(str(q.relative_to(ctx.home)))
    return out


class Runtimes(Migrator):
    key = "runtimes"
    title = "Runtime versions of version managers"
    sections = ("Version managers", "mise")

    def scan(self, ctx: Context) -> RuntimeFacts:
        return RuntimeFacts(_managers(ctx), _mise_extra(ctx))

    def pages(self, f: Facts) -> List[Page]:
        rt = facts_of(f)
        if rt is None:
            return []
        managers: Dict[str, List[str]] = {}
        for vm in rt.managers:
            for tool in vm.versions:
                managers.setdefault(tool, []).append(vm.name)
        return [Page("runtimes", self.title, "config/mise.toml",
                     "The versions that your version managers have. The selected ones go to config/mise.toml, and "
                     "mise installs them.", "checklist",
                     unique_items([Item(f"runtime:{t}", f"{t} {', '.join(v)}",
                                        "From " + ", ".join(managers.get(t, [])), default=False)
                                   for t, v in runtime_lines(rt).items()]),
                     "off: mise installs them; the base has Node, Python, and Ruby")]

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        rt, r = facts_of(f), txn.report
        if rt is None:
            return
        # Your mise config, carried as "layer", has its own versions: they stay.
        user = _rest.layered_mise(f, c)
        lines = runtime_lines(rt)
        picked = {t for t in lines if c.selected("runtimes", f"runtime:{t}")}
        for tool, versions in lines.items():
            if tool in picked and not (user is not None and tool in user.tools):
                txn.mise().add_tool(tool, versions[0] if len(versions) == 1 else versions)
        if picked:
            r.carried("runtime versions of " + ", ".join(
                vm.name for vm in rt.managers
                if vm.versions and (not catalog.version_manager(vm.name).mise_replaces or picked & set(vm.versions))),
                "config/mise.toml")
        items = []
        for vm in rt.managers:
            parts = []
            for tool, versions in vm.versions.items():
                d = vm.default.get(tool)
                parts.append(f"{tool} {', '.join(versions)}" + (f" (default {d})" if d else ""))
            what = "; ".join(parts) or "no versions"
            mise = ""
            if not catalog.version_manager(vm.name).mise_replaces:
                mise = ("dotkit has the Zulu 17 JDK (JAVA_HOME). For another JDK: `mise ls-remote java`, then "
                        "`mise use -g java@<version>`")
            else:
                tools = [t for t in vm.versions if t]
                done = [t for t in tools if t in picked]
                rest = [t for t in tools if t not in picked]
                mise = ", ".join(f"`mise use -g {t}@{(lines.get(t) or ['<version>'])[0]}`" for t in rest)
                if done and not rest:
                    mise = "config/mise.toml has these versions now"
                elif done:
                    names = ", ".join(done[:-1]) + " and " + done[-1] if len(done) > 1 else done[0]
                    mise = f"config/mise.toml has the {names} versions now. With mise: " + mise
                elif mise:
                    mise = "With mise: " + mise
            steps = ". ".join(x for x in (what, mise) if x)
            items.append(f"{vm.name} ({vm.path}): {steps}. Then remove its lines from the shell files "
                         f"(the report lists them) and `rm -rf {vm.path}`")
        if items:
            r.todo("Version managers",
                   "mise replaces them; dotkit's shell does not load them. The base has Node 24, Python 3.14, "
                   "and Ruby 3.4. A project pins its own versions with `mise use` or a .nvmrc, .python-version, "
                   "or .ruby-version file.", items)
        for extra in rt.mise_extra:
            r.todo("mise", "mise still reads these files. Move their tools to config/mise.toml.",
                   [f"~/{extra}"])
