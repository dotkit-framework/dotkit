"""Change files of the repository, build, and apply (BEHAVIOR.md, Ad13, Rm9, and Ow9).

apply_nix() backs up the files that the edit changes, runs the edit, and
builds. When the edit or the build fails, or on a signal, it restores the
files and deletes the ones that the edit added (they did not exist before). Then it activates the system
that it built and runs `mise install`, as `dotkit apply` does
(activate_and_install); the files keep the change when that fails. add and
remove change the lists, own the link lines of nix/.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from . import backup, nix, proc, ui
from .env import Env

APPLY_FAILED = "dotkit apply failed. The Nix files keep the change. Fix the problem, then run dotkit apply."


def activate_and_install(env: Env) -> int:
    """Activates the system that build_system linked, then `mise install`. The exit code of the first failure."""
    code = nix.Nix(env).activate()
    if code:
        return code
    ui.progress("Installing the mise tools...")
    return proc.mise(env.home, ["install"]).code


def apply_nix(env: Env, edit: Callable[[], None], paths: Sequence[Path]) -> None:
    """paths: the files that edit changes or adds; a restore deletes the ones that did not exist."""
    n = nix.Nix(env)
    copies = backup.Backup(paths)
    try:
        with backup.on_signals():
            try:
                edit()
            except OSError:
                copies.restore()
                raise ui.die("could not change the Nix files. They are restored.")
            if n.build_system() != 0:
                copies.restore()
                raise ui.die("the build failed. The Nix files are restored.")
    except (KeyboardInterrupt, backup.Stopped):
        copies.restore()
        raise ui.Exit(130, "\ndotkit: stopped. The Nix files are restored.")
    copies.discard()
    if activate_and_install(env) != 0:
        raise ui.die(APPLY_FAILED)
