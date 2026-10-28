"""The mise tools: the [tools] tables of config/mise.toml of dotkit and of the repository; a key of the
repository replaces the same key of the base. The version is the installed one (`mise ls`), else the one
that an apply installs now (`mise latest`, with the minimum release age of the config).

A package of npm, PyPI, crates.io, Go, or RubyGems is a query of its ecosystem; there OSV also has the
malicious releases (MAL-*). A tool from GitHub is a query of the GIT ecosystem. Node and Python are their
repositories. OSV has the Ruby CVEs without versions; Bitnami has them with versions, for the records
that name a fixed version.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from ...core import mise_toml, proc
from .. import match
from ..model import Item, Part, git_query, package_query
from .nix import tag

NAME, LABEL, NOUNS = "mise", "mise", ("mise tool", "mise tools")

# The mise backends with an OSV ecosystem, and the ones that are a GitHub repository.
ECOSYSTEMS = {"npm": "npm", "pipx": "PyPI", "pypi": "PyPI", "cargo": "crates.io", "go": "Go", "gem": "RubyGems"}
GITHUB_BACKENDS = ("github", "aqua", "ubi")
# The core tools of mise: the repository and the tag of a version. Go is the stdlib package of the Go
# ecosystem, Ruby the ruby package of Bitnami.
MISE_CORE: Dict[str, Tuple[str, str]] = {
    "node": ("https://github.com/nodejs/node", "v{v}"),
    "python": ("https://github.com/python/cpython", "v{v}"),
    "bun": ("https://github.com/oven-sh/bun", "bun-v{v}"),
    "deno": ("https://github.com/denoland/deno", "v{v}"),
}
# Registry tools whose advisories are in a package ecosystem that the mise registry does not name.
MISE_ALIASES = {
    "claude-code": "npm:@anthropic-ai/claude-code",
    "codex": "npm:@openai/codex",
    "gemini-cli": "npm:@google/gemini-cli",
}


def backend_queries(spec: str, version: str) -> List[dict]:
    """The queries of a mise backend spec (npm:cowsay, github:owner/repo); [] for the other backends."""
    backend, _, name = spec.partition(":")
    # A spec can have options in brackets (github:o/r[exe=x]) or a version (npm:x@1).
    name = name.split("[", 1)[0]
    if backend in ECOSYSTEMS and name:
        if name[1:].find("@") > 0:
            name = name[:name.rindex("@")]
        return [package_query(ECOSYSTEMS[backend], name, version)]
    if backend in GITHUB_BACKENDS and re.fullmatch(r"[\w.-]+/[\w.-]+", name):
        repo = f"https://github.com/{name}"
        # mise drops the v of a tag: query both forms; only one is a tag of the repository.
        return [git_query(repo, f"v{version}"), git_query(repo, version)]
    return []


def _bitnami(record: dict, query: dict) -> Optional[str]:
    return None if match.has_fix(record, query) else ""


def item(key: str, version: str, backends: Sequence[str] = ()) -> Item:
    """The item of a mise tool: KEY of the [tools] table, at VERSION. backends: what `mise registry KEY`
    prints, for a key without a backend."""
    out = Item(NAME, key, version)
    if key == "go":
        out.queries = [package_query("Go", "stdlib", version)]
    elif key == "ruby":
        out.queries = [package_query("Bitnami", "ruby", version)]
        out.accept = _bitnami
    elif key in MISE_CORE:
        repo, template = MISE_CORE[key]
        out.queries = [git_query(repo, tag(template, version))]
    elif ":" in key:
        out.queries = backend_queries(key, version)
    else:
        specs = [MISE_ALIASES[key]] if key in MISE_ALIASES else []
        specs += [b for b in backends if not b.startswith(("asdf:", "vfox:"))]
        seen: List[dict] = []
        for spec in specs:
            for q in backend_queries(spec, version):
                if q not in seen:
                    seen.append(q)
        out.queries = seen
    if not out.queries:
        out.why = "OSV has no data for its source"
    return out


def requests(value: object) -> List[Tuple[str, str]]:
    """(version request, minimum release age) of a [tools] value: "24", { version = "latest", ... },
    ["22", "20"]."""
    if isinstance(value, str):
        return [(value, "")]
    if isinstance(value, dict) and isinstance(value.get("version"), str):
        age = value.get("minimum_release_age")
        return [(value["version"], age if isinstance(age, str) else "")]
    if isinstance(value, list):
        return [r for v in value for r in requests(v)]
    return []


def collect(ctx) -> Part:
    env = ctx.env
    try:
        base = mise_toml.tools(env.dotkit / "config/mise.toml")
        repo = mise_toml.tools(env.repo / "config/mise.toml")
        settings = {**mise_toml.settings(env.dotkit / "config/mise.toml"),
                    **mise_toml.settings(env.repo / "config/mise.toml")}
    except mise_toml.InvalidToml as e:
        return Part(error=f"mise: {e} The mise tools are not checked.")
    tools = {**base, **repo}
    if not tools:
        return Part()
    if ctx.mise_ls() is None:
        return Part(error="mise: `mise ls --global --json` failed, so the mise tools are not checked.")
    installed = ctx.mise_installed()
    default_age = settings.get("minimum_release_age")
    part = Part()
    for key, value in tools.items():
        versions = [e["version"] for e in installed.get(key, [])]
        if not versions:
            for req, age in requests(value):
                age = age or (default_age if isinstance(default_age, str) else "")
                args = ["latest", *(["--minimum-release-age", age] if age else []),
                        key if req in ("", "latest") else f"{key}@{req}"]
                r = proc.mise(env.home, args, stdout=proc.CAPTURE, stderr=proc.DEVNULL)
                if r.ok and r.value:
                    versions.append(r.value)
        if not versions:
            part.items.append(Item(NAME, key, "", why="mise cannot resolve its version"))
            continue
        backends: List[str] = []
        if ":" not in key and key not in MISE_CORE and key not in ("go", "ruby"):
            r = proc.mise(env.home, ["registry", key], stdout=proc.CAPTURE, stderr=proc.DEVNULL)
            backends = r.value.split() if r.ok else []
        part.items += [item(key, v, backends) for v in dict.fromkeys(versions)]
    return part
