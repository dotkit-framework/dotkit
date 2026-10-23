"""Tools of pipx and uv tool: to mise, as pipx:<package>."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .._compat import tomllib
from ..context import Context, read_json
from ..docs import Txn
from ..model import Choices, Facts, Page
from . import Migrator
from .tools import Package, env_path, page, plan_tools


@dataclass
class PythonFacts:
    packages: List[Package] = field(default_factory=list)


def facts_of(f: Facts) -> Optional[PythonFacts]:
    return f.get(PythonTools.key)


class PythonTools(Migrator):
    key = "python_tools"
    title = "pipx and uv tools"
    sections = ("Tools that moved to mise", "Tools that are not in mise", "mise", "Not carried, by your choice")

    def manual_steps(self, ctx: Context) -> str:
        return ("None of your pipx and uv tools is in config/mise.toml. They stay where they are, and mise does "
                "not install them. Add the ones you want with `mise use -g pipx:<package>`.")

    def scan(self, ctx: Context) -> PythonFacts:
        found: Dict[str, Package] = {}
        homes = [env_path(ctx, "PIPX_HOME", ".local/pipx"), ctx.home / "Library/Application Support/pipx"]
        for home in homes:
            for meta_path in sorted(home.glob("venvs/*/pipx_metadata.json")):
                meta = read_json(meta_path) or {}
                main = meta.get("main_package") or {}
                name = main.get("package") or meta_path.parent.name
                spec = main.get("package_or_url") or name
                ok, note = True, ""
                if re.search(r"[/:@]", spec) and not re.fullmatch(r"[A-Za-z0-9._\-\[\],]+", spec):
                    ok, note = False, f"installed from {spec}"
                injected = sorted((meta.get("injected_packages") or {}).keys())
                if injected:
                    note = "injected packages: " + ", ".join(injected)
                found.setdefault(name, Package(name, main.get("package_version", ""), "pipx", ok, note))
        tools = env_path(ctx, "UV_TOOL_DIR", ".local/share/uv/tools")
        for receipt in sorted(tools.glob("*/uv-receipt.toml")):
            try:
                data = tomllib.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            reqs = (data.get("tool") or {}).get("requirements") or []
            if not reqs:
                continue
            first = reqs[0] if isinstance(reqs[0], dict) else {"name": str(reqs[0])}
            name = first.get("name") or receipt.parent.name
            ok, note = True, ""
            if any(k in first for k in ("git", "url", "path", "directory", "editable")):
                ok, note = False, "installed from a URL or a directory"
            if len(reqs) > 1:
                note = "with " + ", ".join(r.get("name", "?") for r in reqs[1:] if isinstance(r, dict))
            found.setdefault(name, Package(name, "", "uv tool", ok, note))
        return PythonFacts(sorted(found.values(), key=lambda p: p.name))

    def pages(self, f: Facts) -> List[Page]:
        p = facts_of(f)
        if p is None:
            return []
        return [page("pipx", "pipx and uv tools",
                     "Tools of pipx and uv tool. The selected ones move to mise; the old copies stay until you "
                     "remove them.", p.packages)]

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        p = facts_of(f)
        if p is None:
            return
        plan_tools(f, c, txn, self.pages(f), "pipx", p.packages, "# From pipx and uv tool, see MIGRATION.md.",
                   "{remove}")
