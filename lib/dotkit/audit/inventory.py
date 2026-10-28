"""The tools that dotkit installs, and their versions. Each collector runs its commands and returns data;
match.py makes the items and the queries.

- Nix: one evaluation of the configuration: the packages of Home Manager and of the system, with the
  source URL, the tag or commit, and the patches of each one. It builds nothing.
- mise: the [tools] tables of the base and of the repository; a key of the repository replaces the same
  key of the base. The version is the installed one (`mise ls --global --json`), else the one that an
  apply would install now (`mise latest`, with the minimum release age of the config).
- Homebrew: `brew vulns --json --deps` for the formulae of the configuration. Homebrew asks OSV itself
  and knows its own patches. With an installed formula it checks the installed version.
- dotkit: the Python packages in lib/dotkit/_vendor, and the inputs of its flake.lock.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core import mise_toml, proc
from ..core.env import Env
from ..core.nix import Nix
from . import match

# The packages of the configuration. A wrapper (neovim) has the source in its unwrapped package. The
# expression is part of the command line, which the scenario tests compare.
NIX_EXPR = '''
    sys:
    let
      lib = sys.pkgs.lib;
      users = lib.attrValues sys.config.home-manager.users;
      drvs = lib.concatMap (u: u.home.packages) users ++ sys.config.environment.systemPackages;
      str = x: if builtins.isString x then x else "";
      one =
        d:
        let
          src = d.src or (d.unwrapped.src or { });
          urls = src.urls or [ ];
          v = {
            name = str (d.pname or (d.name or ""));
            version = str (d.version or "");
            url = str (src.url or (if builtins.isList urls && urls != [ ] then builtins.head urls else ""));
            git = str (src.gitRepoUrl or "");
            tag = str (src.tag or "");
            rev = str (src.rev or "");
            homepage = str (d.meta.homepage or "");
            patches = map (p: str (p.name or (baseNameOf (toString p)))) (d.patches or [ ]);
          };
          r = builtins.tryEval (builtins.deepSeq v v);
        in
        if r.success then v else null;
    in
    builtins.filter (x: x != null) (map one drvs)'''


@dataclass
class Inventory:
    items: List[match.Item] = field(default_factory=list)
    # The findings of brew vulns, which asks OSV itself.
    brew: List[match.Finding] = field(default_factory=list)
    brew_checked: int = 0
    # (source, text) of a part that could not be checked. The report is not complete.
    errors: List[Tuple[str, str]] = field(default_factory=list)


def nix(env: Env, inv: Inventory) -> None:
    r = Nix(env).flake(["eval", "--json", f"{env.flake}#darwinConfigurations.mac", "--apply", NIX_EXPR],
                       stdout=proc.CAPTURE)
    try:
        packages = json.loads(r.out) if r.ok else None
    except ValueError:
        packages = None
    if not isinstance(packages, list):
        inv.errors.append(("nix", "Nix: the configuration did not evaluate, so the Nix packages are not checked."))
        return
    seen = set()
    for p in packages:
        item = match.nix_item(p) if isinstance(p, dict) else None
        if item is None or (item.name, item.version) in seen:
            continue
        seen.add((item.name, item.version))
        inv.items.append(item)


def _mise_installed(env: Env) -> Optional[Dict[str, List[str]]]:
    """The active versions of the global tools, key -> versions; None without mise."""
    r = proc.mise(env.home, ["ls", "--global", "--json"], stdout=proc.CAPTURE, stderr=proc.DEVNULL)
    try:
        data = json.loads(r.out) if r.ok else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None
    out: Dict[str, List[str]] = {}
    for key, entries in data.items():
        for e in entries if isinstance(entries, list) else []:
            if isinstance(e, dict) and e.get("installed") and e.get("active") and isinstance(e.get("version"), str):
                out.setdefault(key, []).append(e["version"])
    return out


def _requests(value: object) -> List[Tuple[str, str]]:
    """(version request, minimum release age) of a [tools] value: "24", { version = "latest", ... },
    ["22", "20"]."""
    if isinstance(value, str):
        return [(value, "")]
    if isinstance(value, dict) and isinstance(value.get("version"), str):
        age = value.get("minimum_release_age")
        return [(value["version"], age if isinstance(age, str) else "")]
    if isinstance(value, list):
        return [r for v in value for r in _requests(v)]
    return []


def mise(env: Env, inv: Inventory) -> None:
    try:
        base = mise_toml.tools(env.dotkit / "config/mise.toml")
        repo = mise_toml.tools(env.repo / "config/mise.toml")
        settings = {**mise_toml.settings(env.dotkit / "config/mise.toml"),
                    **mise_toml.settings(env.repo / "config/mise.toml")}
    except mise_toml.InvalidToml as e:
        inv.errors.append(("mise", f"mise: {e} The mise tools are not checked."))
        return
    tools = {**base, **repo}
    if not tools:
        return
    installed = _mise_installed(env)
    if installed is None:
        inv.errors.append(("mise", "mise: `mise ls --global --json` failed, so the mise tools are not checked."))
        return
    default_age = settings.get("minimum_release_age")
    for key, value in tools.items():
        versions = installed.get(key, [])
        if not versions:
            for req, age in _requests(value):
                age = age or (default_age if isinstance(default_age, str) else "")
                args = ["latest", *(["--minimum-release-age", age] if age else []),
                        key if req in ("", "latest") else f"{key}@{req}"]
                r = proc.mise(env.home, args, stdout=proc.CAPTURE, stderr=proc.DEVNULL)
                if r.ok and r.value:
                    versions.append(r.value)
        if not versions:
            inv.items.append(match.Item("mise", key, "", why="mise cannot resolve its version"))
            continue
        backends: List[str] = []
        if ":" not in key and key not in match.MISE_CORE and key != "go":
            r = proc.mise(env.home, ["registry", key], stdout=proc.CAPTURE, stderr=proc.DEVNULL)
            backends = r.value.split() if r.ok else []
        for v in dict.fromkeys(versions):
            inv.items.append(match.mise_item(key, v, backends))


def _severity(text: object) -> str:
    t = text.upper() if isinstance(text, str) else ""
    return {"MODERATE": "MEDIUM"}.get(t, t if t in ("LOW", "MEDIUM", "HIGH", "CRITICAL") else "")


def brew(env: Env, inv: Inventory) -> None:
    r = Nix(env).brewfile()
    if not r.ok:
        inv.errors.append(("brew", "Homebrew: the Brewfile of the configuration did not evaluate, so the formulae are not "
                          "checked."))
        return
    lines, taps = [], []
    for line in r.out.split("\n"):
        m = re.match(r'\s*brew\s+"([^"]+)"', line)
        if m and "/" in m[1]:
            # brew vulns checks only the formulae of trusted taps, and a runner of CI has no other taps.
            taps.append(m[1])
        elif m:
            lines.append(line)
    for name in taps:
        inv.items.append(match.Item("brew", name, "", why="brew vulns does not check the formulae of other taps"))
    if not lines:
        return
    exe = proc.which("brew") or "/opt/homebrew/bin/brew"
    if not os.access(exe, os.X_OK):
        inv.errors.append(("brew", "Homebrew: brew is not installed, so the formulae are not checked."))
        return
    with tempfile.TemporaryDirectory(prefix="dotkit-audit.") as tmp:
        brewfile = Path(tmp) / "Brewfile"
        brewfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = proc.run([exe, "vulns", "--json", "--deps", f"--brewfile={brewfile}"],
                     env={"HOMEBREW_NO_AUTO_UPDATE": "1"}, stdout=proc.CAPTURE, stderr=proc.CAPTURE)
    try:
        data = json.loads(r.out)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        if "Unknown command" in r.err:
            inv.errors.append(("brew", "Homebrew: brew vulns needs Homebrew 6.0.11 or newer, so the formulae are not "
                              "checked. Run `brew update`."))
        else:
            last = r.err.strip().split("\n")[-1] if r.err.strip() else f"exit code {r.code}"
            inv.errors.append(("brew", f"Homebrew: brew vulns failed ({last}), so the formulae are not checked."))
        return
    inv.brew_checked = data.get("checked") if isinstance(data.get("checked"), int) else 0
    for f in data.get("findings") or []:
        if not isinstance(f, dict) or not isinstance(f.get("formula"), str):
            continue
        item = match.Item("brew", f["formula"], str(f.get("version") or ""))
        for v in f.get("vulnerabilities") or []:
            if not isinstance(v, dict) or not isinstance(v.get("id"), str):
                continue
            fixed = [x for x in v.get("fixed_versions") or [] if isinstance(x, str) and not match.HEX40.fullmatch(x)]
            a = match.Advisory(v["id"], [a for a in v.get("aliases") or [] if isinstance(a, str)],
                               " ".join(str(v.get("summary") or "").split())[:200], _severity(v.get("severity")),
                               fixed, v["id"].startswith("MAL-"))
            inv.brew.append(match.Finding(item, a))
    skipped = [s for s in data.get("skipped_formulae") or [] if isinstance(s, str)]
    unscanned = data.get("unscanned_formulae")
    skipped += sorted(unscanned) if isinstance(unscanned, dict) else []
    for name in skipped:
        inv.items.append(match.Item("brew", name, "", why="Homebrew knows no source repository for it"))


def dotkit_own(env: Env, inv: Inventory) -> None:
    try:
        inv.items += match.vendored_items((env.dotkit / "lib/dotkit/_vendor/vendor.txt").read_text(encoding="utf-8"))
    except OSError:
        pass
    try:
        inv.items += match.flake_items(json.loads((env.dotkit / "flake.lock").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass


def collect(env: Env) -> Inventory:
    inv = Inventory()
    nix(env, inv)
    brew(env, inv)
    mise(env, inv)
    dotkit_own(env, inv)
    return inv
