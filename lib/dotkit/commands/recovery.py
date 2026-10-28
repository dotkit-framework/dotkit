"""dotkit history, rollback, and clean (BEHAVIOR.md, rows H and K)."""

from __future__ import annotations

import os
import re

from ..core import proc, ui
from ..core.env import Env

# The Nix profiles of the system. history reads the generations here.
PROFILES = "/nix/var/nix/profiles"


def history(env: Env) -> int:
    """Compares the active generation with the one before it."""
    try:
        current = os.readlink(f"{PROFILES}/system")
    except OSError:
        raise ui.die(f"{PROFILES}/system is not a link to a generation. Run dotkit apply first.")
    digits = re.sub(r"[^0-9]", "", current)
    prev = f"{PROFILES}/system-{(int(digits) if digits else 0) - 1}-link"
    if not os.path.exists(prev):
        raise ui.die("there is no previous generation.")
    ui.out(f"{ui.paint(os.path.basename(prev), 'dim')} {ui.paint('->', 'dim')} {ui.paint(current, 'heading')}")
    return proc.run(["nix", "store", "diff-closures", prev, f"{PROFILES}/{current}"]).code


def rollback() -> int:
    return proc.run(["sudo", "darwin-rebuild", "switch", "--rollback"]).code


def clean(env: Env, age: str, dry_run: bool, yes: bool) -> int:
    if dry_run:
        for argv in (["sudo", "nix-collect-garbage", "--delete-older-than", age, "--dry-run"],
                     ["brew", "cleanup", "--dry-run"]):
            code = proc.run(argv).code
            if code:
                return code
        code = proc.mise(env.home, ["prune", "--dry-run"]).code
        if code:
            return code
        ui.hint("No change. Run dotkit clean to delete.")
        return 0
    if not ui.confirm(f"Delete Nix generations older than {age}, old Homebrew downloads, and unused mise "
                      "versions?", yes):
        raise ui.die("nothing changed.")
    for argv in (["sudo", "nix-collect-garbage", "--delete-older-than", age], ["brew", "cleanup"]):
        code = proc.run(argv).code
        if code:
            return code
    code = proc.mise(env.home, ["prune", "--yes"]).code
    if not code:
        ui.success("Cleaned up.")
    return code
