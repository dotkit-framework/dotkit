"""Select the source of a tool (PARITY.md, rows R).

resolve() looks up an argument and returns an Outcome: the Candidates that
the sources gave, and a Selection, or the reason when it selects none.
choose() has the rules for a bare name and runs no command, so its tests
are a table.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from . import lookups as lk
from .entries import Entry, split_prefix
from .lookups import Found, Lookups

# The order of the candidates in the plan.
KEYS = ("app", "nix", "mise", "formula", "cask", "npm", "pypi", "spec")
NIX_FAILED_REASON = "nix eval failed, so Nixpkgs cannot be checked. Run dotkit apply -n to see the error."


@dataclass
class Candidates:
    """What the sources have for one argument. found maps a key of KEYS to its Found."""
    name: str = ""
    spec: str = ""
    found: Dict[str, Found] = field(default_factory=dict)
    nix_failed: bool = False

    def spec_of(self, key: str) -> str:
        """The text to type for a candidate, for example nix:hk."""
        prefix = {"app": "cask:", "cask": "cask:", "formula": "brew:", "nix": "nix:", "mise": "mise:",
                  "npm": "npm:", "pypi": "pipx:"}
        return self.spec if key == "spec" else prefix[key] + self.name

    def set_cask(self, found: Found) -> None:
        self.found["app" if found.app else "cask"] = found


@dataclass
class Selection:
    key: str  # a key of KEYS; "" for an App Store app
    entry: Entry  # what goes into the config
    spec: str
    reason: str
    command: str = ""


@dataclass
class Outcome:
    candidates: Candidates
    selection: Optional[Selection] = None
    reason: str = ""
    # A bare name: the rules of choose() selected the source, so the picker can ask.
    bare: bool = False

    @property
    def ok(self) -> bool:
        return self.selection is not None


def choose(c: Candidates, version_ge: Callable[[str, str], bool]) -> Tuple[Optional[str], str]:
    """The key of the candidate for a bare name and the reason, or None and the reason when there is none.
    version_ge(a, b) is True when version a is the same as or newer than b."""
    f = c.found
    if c.nix_failed:
        return None, NIX_FAILED_REASON
    # 1. A macOS app. A formula with the same name is a command-line tool.
    if "app" in f and "formula" in f:
        return None, ("Homebrew has an app and a command-line tool with this name. "
                      "Add one of them by the name in the list.")
    if "app" in f:
        return "app", "a macOS app"
    # 2 and 3. Nixpkgs when it is current, otherwise mise.
    if "nix" in f and "mise" in f:
        nixv, misev = f["nix"].version, f["mise"].version
        if not nixv or not misev or version_ge(nixv, misev):
            return "nix", "Nixpkgs has the current release"
        return "mise", f"Nixpkgs has the older {nixv}"
    if "nix" in f:
        return "nix", "not in the mise registry"
    if "mise" in f:
        return "mise", "not in Nixpkgs"
    # 4. Homebrew, as `brew install` does: the formula, else the cask.
    if "formula" in f:
        return "formula", "not in Nixpkgs or mise"
    if "cask" in f:
        return "cask", "not in Nixpkgs or mise"
    if "npm" in f or "pypi" in f:
        return None, ("only npm or PyPI has this name. Anyone can publish there: check the package, "
                      "then add it by the name in the list.")
    return None, "no source has this name. Check the name, or use a prefix (dotkit add --help)."


class Resolver:
    def __init__(self, look: Lookups):
        self.look = look

    def select(self, c: Candidates, key: str, reason: str) -> Selection:
        kind, entry = {
            "app": ("cask", c.name), "cask": ("cask", c.name), "formula": ("formula", c.name),
            "nix": ("nix", c.name), "mise": ("mise", c.name), "npm": ("mise", f"npm:{c.name}"),
            "pypi": ("mise", f"pipx:{c.name}"), "spec": ("mise", c.spec),
        }[key]
        command = c.found[key].command
        # Formulae, mise tools, and PyPI packages report the package name, not the command. Nixpkgs often
        # knows the command (meta.mainProgram): tlrc installs tldr. npm and casks report their commands.
        if key in ("formula", "mise", "pypi") or (key == "spec" and not c.spec.startswith("npm:")):
            pkg = (lk.spec_package(c.spec) if key == "spec" else c.name).rsplit("/", 1)[-1]
            nix_found = c.found.get("nix")
            if nix_found is None:
                got = self.look.nix(pkg)
                nix_found = got if isinstance(got, Found) else None
            if nix_found is not None and nix_found.command:
                command = nix_found.command
        if not command:
            command = re.sub(r".*[:/]", "", entry)
        return Selection(key, Entry(kind, entry), c.spec_of(key), reason, command)

    def gather(self, name: str) -> Candidates:
        """Looks up a bare name in all sources at the same time."""
        c = Candidates(name=name)
        look = self.look
        jobs = {"cask": lambda: look.brew("cask", name), "formula": lambda: look.brew("formula", name),
                "nix": lambda: look.nix(name), "mise": lambda: look.mise_registry(name),
                "npm": lambda: look.npm(name), "pypi": lambda: look.pypi(name)}
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {k: pool.submit(f) for k, f in jobs.items()}
            results = {k: fut.result() for k, fut in futures.items()}
        if isinstance(results["cask"], Found):
            c.set_cask(results["cask"])
        for key in ("formula", "nix", "mise", "npm", "pypi"):
            if isinstance(results[key], Found):
                c.found[key] = results[key]
        c.nix_failed = results["nix"] == lk.NIX_FAILED
        return c

    def resolve(self, arg: str) -> Outcome:
        look = self.look
        prefix, name = split_prefix(arg)
        if prefix == "nix":
            c = Candidates(name=name)
            if not lk.nix_attr_ok(c.name):
                return Outcome(c, reason=f"'{c.name}' is not a valid Nix attribute path.")
            got = look.nix(c.name)
            if got == lk.NIX_FAILED:
                return Outcome(c, reason=NIX_FAILED_REASON)
            if not isinstance(got, Found):
                return Outcome(c, reason=f"Nixpkgs has no package '{c.name}' for this Mac.")
            c.found["nix"] = got
            return Outcome(c, self.select(c, "nix", "selected with nix:"))
        if prefix in ("brew", "cask"):
            c = Candidates(name=name)
            if not lk.brew_name_ok(c.name):
                return Outcome(c, reason=f"'{c.name}' is not a valid Homebrew name.")
            # brew: is the same as `brew install NAME`: the formula, else the cask.
            if prefix == "brew":
                got = look.brew("formula", c.name)
                if got is not None:
                    c.found["formula"] = got
                    return Outcome(c, self.select(c, "formula", "selected with brew:"))
            got = look.brew("cask", c.name)
            if got is None:
                what = "formula or cask" if prefix == "brew" else "cask"
                return Outcome(c, reason=f"Homebrew has no {what} '{c.name}'.")
            c.set_cask(got)
            return Outcome(c, self.select(c, "app" if got.app else "cask", f"selected with {prefix}:"))
        if prefix == "mise":
            c = Candidates(name=name)
            got = look.mise_registry(c.name)
            if got is None:
                return Outcome(c, reason=f"the mise registry has no tool '{c.name}' (without asdf or vfox).")
            c.found["mise"] = got
            return Outcome(c, self.select(c, "mise", "selected with mise:"))
        if prefix == "mas":
            return self._mas(arg)
        if prefix:
            c = Candidates(name=name, spec=arg)
            got = look.mise_spec(arg)
            if got is None:
                return Outcome(c, reason=f"mise cannot find '{arg}'.")
            c.found["spec"] = got
            return Outcome(c, self.select(c, "spec", f"selected with {prefix}:"))
        if re.fullmatch(r"@.*/.*", arg, re.S):
            c = Candidates(name=arg)
            got = look.npm(arg)
            if got is None:
                return Outcome(c, reason=f"npm has no package '{arg}' with a command.")
            c.found["npm"] = got
            return Outcome(c, self.select(c, "npm", "a scoped npm package"))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", arg):
            return Outcome(Candidates(), reason=f"'{arg}' is not a valid tool name.")
        c = self.gather(arg)
        key, reason = choose(c, look.version_ge)
        if key is None:
            return Outcome(c, reason=reason, bare=True)
        return Outcome(c, self.select(c, key, reason), bare=True)

    def _mas(self, arg: str) -> Outcome:
        c = Candidates(name=arg[4:])
        if not re.fullmatch(r"[1-9][0-9]*", c.name):
            return Outcome(c, reason=f"'{c.name}' is not an App Store ID. mas list shows the IDs.")
        name = self.look.mas_name(c.name)
        if name == 2:
            return Outcome(c, reason="mas is not installed, so the name of the app is unknown. "
                                     "Run dotkit add brew:mas first.")
        if name == 3:
            return Outcome(c, reason=f"cannot read the name of App Store app {c.name} from mas. "
                                     f"Add it to nix/mas.txt as `{c.name} Name`.")
        if not isinstance(name, str):
            return Outcome(c, reason=f"mas finds no App Store app with the ID {c.name}.")
        # The reason is the name of the app, for the plan.
        return Outcome(c, Selection("", Entry("mas", f"{c.name} {name}"), arg, name))
