"""dotkit migrate and tidy: the migration wizard (lib/dotkit/migrate) for an existing repository (BEHAVIOR.md, M).

Both run migrate/dotkit-migrate with the taps and the App Store apps of the
configuration, which only Nix can evaluate: the wizard reads them from
--declared. lib/dotkit/migrate/TIDY.md describes tidy.
"""

from __future__ import annotations

from typing import List

from ..core import nix, proc
from ..core.env import Env

DECLARED = "h: { taps = map (t: t.name) h.taps; masApps = h.masApps; }"


def _declared(env: Env) -> proc.Result:
    return nix.Nix(env).flake(["eval", "--json", f"{env.flake}#darwinConfigurations.mac.config.homebrew",
                               "--apply", DECLARED], stdout=proc.CAPTURE)


def _wizard(env: Env, mode: str, flags: List[str]) -> int:
    declared = _declared(env)
    if not declared.ok:
        return declared.code
    return proc.run([str(env.dotkit / "migrate/dotkit-migrate"), mode, "--repo", str(env.repo),
                     "--declared", declared.value, *flags]).code


def migrate(env: Env, dry_run: bool, yes: bool, all_tools: bool) -> int:
    flags = [f for f, on in (("--dry-run", dry_run), ("--yes", yes), ("--all", all_tools)) if on]
    return _wizard(env, "adopt", flags)


def tidy(env: Env, dry_run: bool, yes: bool, plan: str) -> int:
    flags = [f for f, on in (("--dry-run", dry_run), ("--yes", yes)) if on]
    if plan:
        flags += ["--plan", plan]
    return _wizard(env, "tidy", flags)
