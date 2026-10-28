"""`dotkit __complete`: names for the zsh completion (nix/completions/_dotkit), one per line.

It runs on each Tab, so it reads files only, runs no nix, and imports neither argparse nor the modules of the
other commands (tests/commands/test_commands.py checks this).
"""

from __future__ import annotations

from typing import List

from .core import ui
from .core.env import Env


def main(env: Env, rest: List[str]) -> int:
    what = rest[0] if rest else ""
    if what == "inputs":
        from .commands import upgrade
        for _, name in upgrade.flake_inputs(env):
            ui.out(name)
        return 0
    if what in ("tools-user", "tools-all"):
        from .pkg import names
        for n in names.declared(env, everything=what == "tools-all"):
            ui.out(n)
        return 0
    raise ui.Exit(2, "dotkit: __complete takes inputs, tools-user, or tools-all.")
