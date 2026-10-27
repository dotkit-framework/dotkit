"""Rules of the fake tools (fakebin.py) for the parity scenarios, with the output formats of the real tools.

Each function returns a list of rules. The JSON shapes come from real output:
brew 5 (`brew info --json=v2`, `brew outdated --json=v2`), mise 2026.10
(`registry`, `latest`, `tool --description`, `outdated --json`, `which`), the
npm and PyPI registries, mas 7, and nix 2.35.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

SYSTEM = "@SB/system-1"
DRV = "/nix/store/0000000000000000000000000000000a-darwin-system.drv"


def _r(tool: str, pattern: str, stdout: str = "", **kw) -> dict:
    return {"tool": tool, "re": pattern, "stdout": stdout, **kw}


# --- nix

def nix_pkg(attr: str, version: str = "1.0", desc: str = "", bin: Optional[str] = None) -> List[dict]:
    """The Nixpkgs lookup of dotkit add: a package with its version, description, and command."""
    out = json.dumps({"version": version, "description": desc, "bin": attr if bin is None else bin})
    return [_r("nix", rf'(?s)^eval --json \S+#darwinConfigurations\.mac\.pkgs --apply .*"{re.escape(attr)}"\)',
               out + "\n")]


def nix_no_pkg(attr: str) -> List[dict]:
    return [_r("nix", rf'(?s)^eval --json \S+#darwinConfigurations\.mac\.pkgs --apply .*"{re.escape(attr)}"\)',
               "null\n")]


def nix_eval_fails() -> List[dict]:
    """Every evaluation of the configuration fails, as with an error in nix/darwin.nix."""
    return [_r("nix", r"^eval ", stderr="error: undefined variable 'oops'\n", exit=1)]


def build(fails: bool = False) -> List[dict]:
    """nix build of the system, which links .build/system to SYSTEM."""
    if fails:
        return [_r("nix", r"^build ", stderr="error: builder for '/nix/store/x.drv' failed\n", exit=1)]
    return [_r("nix", r"^build ", actions=[{"link_after": "--out-link", "to": SYSTEM}])]


def build_once(fails: bool = False) -> List[dict]:
    r = build(fails)[0]
    r["once"] = True
    return [r]


def drv_path() -> List[dict]:
    """With DOTKIT_DIR: the evaluation of the derivation, with the lock warning of --override-input."""
    warning = ("warning: not writing modified lock file of flake 'path:@SB/repo/nix':\n"
               "• Updated input 'dotkit':\n"
               "    'github:podkovyrin/dotkit/ccccccc' (2025-10-09)\n"
               "  → 'git+file://@SB/checkout' (2025-10-10)\n"
               "warning: Git tree '@SB/checkout' is dirty\n")
    return [_r("nix", r"^eval --raw \S+#darwinConfigurations\.mac\.system\.drvPath", DRV, stderr=warning)]


def diff_closures(text: str = "hello: ∅ → 2.12.2, +268.5 KiB\n") -> List[dict]:
    return [_r("nix", r"^store diff-closures ", text)]


def cleanup_mode(mode: str = "none") -> List[dict]:
    return [_r("nix", r"^eval --raw \S+homebrew\.onActivation\.cleanup", mode)]


def brewfile(text: str = 'brew "wget"\ncask "ghostty"\ncask "zed"\n') -> List[dict]:
    return [_r("nix", r"^eval --raw \S+homebrew\.brewfile", text)]


def declared_homebrew() -> List[dict]:
    return [_r("nix", r"^eval --json \S+#darwinConfigurations\.mac\.config\.homebrew --apply",
               '{"masApps":{"Keynote":409183694},"taps":[]}\n')]


def mas_apps(apps: dict) -> List[dict]:
    return [_r("nix", r"^eval --json \S+homebrew\.masApps$", json.dumps(apps) + "\n")]


def flake_update(lock: str, new: dict, fails: bool = False, pattern: str = r"^flake update ") -> List[dict]:
    """nix flake update: writes the new lock file (a dict) at lock, a path in the sandbox."""
    if fails:
        return [_r("nix", pattern, stderr="error: cannot fetch input 'dotkit'\n", exit=1)]
    return [_r("nix", pattern, stderr="• Updated input 'dotkit'\n",
               actions=[{"write": lock, "text": json.dumps(new, indent=2) + "\n"}])]


def lock(inputs: dict, follows: Optional[dict] = None) -> dict:
    """A flake.lock: inputs maps a name to (lastModified, rev)."""
    nodes = {"root": {"inputs": {**{k: k for k in inputs}, **(follows or {})}}}
    for k, (t, rev) in inputs.items():
        nodes[k] = {"locked": {"lastModified": t, "rev": rev}}
    return {"nodes": nodes, "root": "root", "version": 7}


# --- the activation

def switch_ok() -> List[dict]:
    return [_r("sudo", "")]


def switch_fails() -> List[dict]:
    return [_r("sudo", r"nix-env", stderr="sudo: a password is required\n", exit=1)]


def apply_ok() -> List[dict]:
    """A whole dotkit apply: build, the two sudo commands, mise install."""
    return build() + switch_ok() + mise_ok("install")


# --- Homebrew

def formula(name: str, version: str = "1.0", desc: str = "") -> List[dict]:
    out = {"formulae": [{"name": name, "desc": desc, "versions": {"stable": version, "head": None, "bottle": True}}],
           "casks": []}
    return [_r("brew", rf"^info --json=v2 --formula {re.escape(name)}$", json.dumps(out))]


def cask(name: str, version: str = "1.0", desc: str = "", app: Optional[str] = None,
         binary: Optional[str] = None, pkg: bool = False) -> List[dict]:
    """A cask with an app (app="Zed.app"), a .pkg installer, and/or a command (binary="zed")."""
    artifacts: list = [{"uninstall": [{"quit": f"com.example.{name}"}]}]
    if app:
        artifacts.append({"app": [app], "target": f"/Applications/{app}"})
    if pkg:
        artifacts.append({"pkg": [f"{name}.pkg"]})
    if binary:
        artifacts.append({"binary": [f"bin/{binary}"], "target": f"/opt/homebrew/bin/{binary}"})
    artifacts.append({"zap": [{"trash": [f"~/Library/Caches/{name}"]}]})
    out = {"formulae": [], "casks": [{"token": name, "version": version, "desc": desc, "artifacts": artifacts}]}
    return [_r("brew", rf"^info --json=v2 --cask {re.escape(name)}$", json.dumps(out))]


def outdated(formulae: list = (), casks: list = ()) -> List[dict]:
    """brew outdated --json=v2: (name, [installed versions], current version) for each."""
    def item(n, installed, current):
        return {"name": n, "installed_versions": installed, "current_version": current, "pinned": False}
    out = {"formulae": [item(*f) for f in formulae], "casks": [item(*c) for c in casks]}
    return [_r("brew", r"^outdated --json=v2$", json.dumps(out))]


def brew(pattern: str, stdout: str = "", **kw) -> List[dict]:
    return [_r("brew", pattern, stdout, **kw)]


# --- mise

def mise_tool(name: str, backends: str, latest: Optional[str] = "1.0", desc: str = "[none]") -> List[dict]:
    """A tool of the mise registry: `mise registry`, `mise latest`, `mise tool --description`."""
    rules = [_r("mise", rf"^registry {re.escape(name)}$", backends + "\n"),
             _r("mise", rf"^tool --description {re.escape(name)}$", desc + "\n")]
    if latest is not None:
        rules.append(_r("mise", rf"^latest {re.escape(name)}$", latest + "\n"))
    return rules


def mise_spec(spec: str, latest: str = "1.0", desc: str = "[none]") -> List[dict]:
    """Any mise backend spec: `mise latest SPEC` and `mise tool --description SPEC`."""
    return [_r("mise", rf"^latest {re.escape(spec)}$", latest + "\n"),
            _r("mise", rf"^tool --description {re.escape(spec)}$", desc + "\n")]


def mise_which(bin: str, path: str) -> List[dict]:
    return [_r("mise", rf"^which {re.escape(bin)}$", path + "\n")]


def mise_ok(pattern: str, stdout: str = "") -> List[dict]:
    return [_r("mise", "^" + pattern, stdout)]


def mise_fails(pattern: str, stderr: str = "mise ERROR failed\n") -> List[dict]:
    return [_r("mise", "^" + pattern, stderr=stderr, exit=1)]


def mise_outdated(tools: dict) -> List[dict]:
    """mise outdated --json: name -> (current, latest)."""
    out = {k: {"name": k, "requested": "latest", "current": c, "bump": None, "latest": latest}
           for k, (c, latest) in tools.items()}
    return [_r("mise", r"^outdated --json$", json.dumps(out, indent=2) + "\n")]


# --- npm, PyPI

def npm(name: str, version: str = "1.0.0", desc: str = "", bin=None) -> List[dict]:
    """The npm registry: bin is a dict (command -> file), a string, or None (no command)."""
    out = {"name": name, "version": version, "description": desc}
    if bin is not None:
        out["bin"] = bin
    url = "https://registry.npmjs.org/" + name.replace("/", "%2F", 1) + "/latest"
    return [_r("curl", rf"^-fsSL --max-time 10 {re.escape(url)}$", json.dumps(out))]


def pypi(name: str, version: str = "1.0", summary: str = "") -> List[dict]:
    out = {"info": {"name": name, "version": version, "summary": summary}}
    return [_r("curl", rf"^-fsSL --max-time 10 https://pypi\.org/pypi/{re.escape(name)}/json$", json.dumps(out))]


# --- mas

MAS_LIST = (" 361285480  Keynote         (15.4)\n"
            " 409203825  Numbers         (14.5)\n"
            "1289583905  Pixelmator Pro  (3.8)\n"
            "      1234  Keynote         (1.0)\n"
            "        55  Back\\slash      (1.0)\n")


def mas(info: Optional[dict] = None) -> List[dict]:
    """mas list (MAS_LIST), and mas info --json for the IDs in info (ID -> JSON object)."""
    rules = [_r("mas", r"^list$", MAS_LIST)]
    for app_id, obj in (info or {}).items():
        rules.append(_r("mas", rf"^info --json {app_id}$", json.dumps(obj) + "\n"))
    rules.append(_r("mas", r"^info --json ", stderr="Error: No apps found in the App Store for ADAM ID\n", exit=1))
    return rules
