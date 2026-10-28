"""The lookups of a tool in each source (BEHAVIOR.md, R21).

Each lookup returns a Found, or None when the source does not have the tool.
nix() returns NIX_FAILED when the evaluation fails, so that an error in the
config is not "not found". The lookups run their commands without stderr.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional, Union

from ..core import nix as nix_mod
from ..core import proc, ui
from ..core.env import Env

NIX_FAILED = "nix-failed"


@dataclass(frozen=True)
class Found:
    version: str = ""
    description: str = ""
    # The command that the tool installs, when the source knows it.
    command: str = ""
    # A cask that installs a macOS app: an .app, a .pkg installer, or a suite of apps.
    app: bool = False


def nix_attr_ok(name: str) -> bool:
    """A valid Nix attribute path, safe to write into a list file."""
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*)*", name) is not None


def brew_name_ok(name: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+@/-]*", name) is not None


def spec_package(spec: str) -> str:
    """The package of a mise spec, without the backend and the version: npm:@scope/pkg@1.0 -> @scope/pkg."""
    pkg = spec.split(":", 1)[1] if ":" in spec else spec
    rest = pkg[1:]
    if "@" in rest:
        rest = rest[:rest.rindex("@")]
    return pkg[:1] + rest


# The expression of the Nixpkgs lookup. The text is part of the command line, which the scenario tests compare.
NIX_EXPR = '''
    pkgs:
    let
      lib = pkgs.lib;
      p = lib.attrByPath (lib.splitString "." "{attr}") null pkgs;
      info = {{
        version = p.version or "";
        description = p.meta.description or "";
        bin = p.meta.mainProgram or (p.pname or "");
      }};
      r = builtins.tryEval (
        if lib.isDerivation p && (p.meta.available or true)
        then builtins.deepSeq info info else null
      );
    in if r.success then r.value else null'''


COMPARE = 'builtins.compareVersions (builtins.getEnv "DOTKIT_VERSION_A") (builtins.getEnv "DOTKIT_VERSION_B")'


def _json(text: str) -> Any:
    """The JSON value of a text, or a ValueError."""
    return json.loads(text)


class Lookups:
    def __init__(self, env: Env):
        self.env = env
        self.nix_cmd = nix_mod.Nix(env)

    def _quiet(self, argv, **kw) -> proc.Result:
        return proc.run(argv, stdout=proc.CAPTURE, stderr=proc.DEVNULL, **kw)

    def _mise(self, args) -> proc.Result:
        return proc.mise(self.env.home, args, stdout=proc.CAPTURE, stderr=proc.DEVNULL)

    def nix(self, attr: str) -> Union[Found, None, str]:
        if not nix_attr_ok(attr):
            return None
        r = proc.run(["nix", "eval", "--json", f"{self.env.flake}#darwinConfigurations.mac.pkgs", "--apply",
                      NIX_EXPR.format(attr=attr), *self.env.override], stdout=proc.CAPTURE, stderr=proc.DEVNULL)
        if not r.ok:
            return NIX_FAILED
        try:
            info = _json(r.out)
        except ValueError:
            return None
        if info is None or info is False:
            return None
        if not isinstance(info, dict):
            return None
        return Found(ui.one_line(info.get("version")), ui.one_line(info.get("description")), ui.one_line(info.get("bin")))

    def version_ge(self, a: str, b: str) -> bool:
        """True when version a is the same as or newer than version b, by builtins.compareVersions: the
        order that Nixpkgs uses for its versions. A leading v does not count."""
        a, b = (v[1:] if v.startswith("v") else v for v in (a, b))
        # The versions go in the environment: no escaping, and `nix eval --expr` does not apply --argstr.
        r = proc.run(["nix", "eval", "--impure", "--expr", COMPARE], env={"DOTKIT_VERSION_A": a, "DOTKIT_VERSION_B": b},
                     stdout=proc.CAPTURE)
        if not r.ok or r.value not in ("-1", "0", "1"):
            raise ui.die(f"nix could not compare the versions {a} and {b}.")
        return r.value != "-1"

    def mise_description(self, name: str) -> str:
        r = self._mise(["tool", "--description", name])
        if not r.ok or r.value == "[none]":
            return ""
        return r.value.replace("\n", " ")

    def mise_registry(self, name: str) -> Optional[Found]:
        """The mise registry, without the asdf and vfox plugins: they often build from source and run
        third-party scripts."""
        r = self._mise(["registry", name])
        if not r.ok:
            return None
        if not any(not b.startswith(("asdf:", "vfox:")) for b in r.value.split()):
            return None
        latest = self._mise(["latest", name])
        version = latest.value if latest.ok else ""
        return Found(version, self.mise_description(name), name)

    def mise_spec(self, spec: str) -> Optional[Found]:
        """Any mise backend spec, for example npm:foo or github:owner/repo."""
        latest = self._mise(["latest", spec])
        if not latest.ok:
            return None
        pkg = spec_package(spec)
        command = pkg.rsplit("/", 1)[-1]
        # npm reports the command: npm:@anthropic-ai/claude-code installs claude.
        if spec.startswith("npm:"):
            found = self.npm(pkg)
            if found is not None:
                command = found.command
        return Found(latest.value, self.mise_description(spec), command)

    def brew(self, kind: str, name: str) -> Optional[Found]:
        if proc.which("brew") is None or not brew_name_ok(name):
            return None
        r = self._quiet(["brew", "info", "--json=v2", f"--{kind}", name], env={"HOMEBREW_NO_AUTO_UPDATE": "1"})
        if not r.ok:
            return None
        try:
            data = _json(r.out)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        if kind == "cask":
            items = data.get("casks") or [{}]
            c = items[0] or {}
            artifacts = [a for a in (c.get("artifacts") or []) if isinstance(a, dict)]
            targets = [a["target"].rsplit("/", 1)[-1] for a in artifacts
                       if "binary" in a and isinstance(a.get("target"), str)]
            app = any(k in a for a in artifacts for k in ("app", "pkg", "suite"))
            return Found(ui.one_line(c.get("version")), ui.one_line(c.get("desc")), ui.one_line(targets[0] if targets else None),
                         app)
        items = data.get("formulae") or [{}]
        f = items[0] or {}
        return Found(ui.one_line((f.get("versions") or {}).get("stable")), ui.one_line(f.get("desc")), ui.one_line(f.get("name")))

    def npm(self, name: str) -> Optional[Found]:
        """Only packages that install a command."""
        url = "https://registry.npmjs.org/" + name.replace("/", "%2F", 1) + "/latest"
        r = self._quiet(["curl", "-fsSL", "--max-time", "10", url])
        if not r.ok:
            return None
        try:
            data = _json(r.out)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        bin_ = data.get("bin")
        if bin_ is None or bin_ == {}:
            return None
        command = sorted(bin_)[0] if isinstance(bin_, dict) else None
        return Found(ui.one_line(data.get("version")), ui.one_line(data.get("description")), ui.one_line(command))

    def pypi(self, name: str) -> Optional[Found]:
        r = self._quiet(["curl", "-fsSL", "--max-time", "10", f"https://pypi.org/pypi/{name}/json"])
        if not r.ok:
            return None
        try:
            data = _json(r.out)
        except ValueError:
            return None
        info = (data.get("info") if isinstance(data, dict) else None) or {}
        if not isinstance(info, dict):
            return None
        return Found(ui.one_line(info.get("version")), ui.one_line(info.get("summary")), ui.one_line(info.get("name")))

    def mas_name(self, app_id: str) -> Union[str, int]:
        """The name of Mac App Store app app_id, from mas. 2: mas is not installed, 1: mas does not know the
        app, 3: its output cannot be read."""
        mas = proc.which("mas") or "/opt/homebrew/bin/mas"
        if not (os.path.isfile(mas) and os.access(mas, os.X_OK)):
            return 2
        # The installed apps first. `mas list` prints "ID  Name  (version)"; this is the regex of the scan of
        # the migration wizard (lib/dotkit/migrate/migrators/appstore.py).
        listed = self._quiet([mas, "list"])
        for line in listed.out.split("\n"):
            m = re.match(r"^\s*(\d+)\s+(.+?)\s+\(([^)]*)\)\s*$", line, re.ASCII)
            if m and m.group(1) == app_id:
                return m.group(2).strip()
        # Then the App Store. mas 7 `info --json` prints one JSON object per line (JSON Lines); the name of
        # the app is `.name` of the first one.
        info = self._quiet([mas, "info", "--json", app_id])
        if not info.ok:
            return 1
        try:
            first, _ = json.JSONDecoder().raw_decode(info.out.lstrip(" \t\n\r"))
        except ValueError:
            return 3
        name = first.get("name") if isinstance(first, dict) else None
        name = name.rstrip("\n") if isinstance(name, str) else ""
        return name if name else 3
