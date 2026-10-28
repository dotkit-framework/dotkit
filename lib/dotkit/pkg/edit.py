"""Change the Nix lists, build, and apply (BEHAVIOR.md, Ad13 and Rm9).

apply_nix() backs up the lists, runs the edit, and builds. When the edit or
the build fails, or on a signal, it restores the lists. Then it activates the
system that it built and runs `mise install`, as `dotkit apply` does; the
lists keep the change when that fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Sequence

from ..core import backup, nix, proc, ui
from ..core.env import Env
from .entries import NIX_FILES

APPLY_FAILED = "dotkit apply failed. The Nix files keep the change. Fix the problem, then run dotkit apply."


def apply_nix(env: Env, edit: Callable[[], None], paths: Optional[Sequence[Path]] = None,
              created: Sequence[Path] = ()) -> None:
    """paths: the files that edit changes (default: the Nix lists); created: the files that it adds, which a
    restore deletes."""
    n = nix.Nix(env)
    copies = backup.Backup([env.repo / f for f in NIX_FILES] if paths is None else paths)

    def restore() -> None:
        copies.restore()
        for path in created:
            path.unlink(missing_ok=True)

    try:
        with backup.on_signals():
            try:
                edit()
            except OSError:
                restore()
                raise ui.die("could not change the Nix files. They are restored.")
            if n.build_system() != 0:
                restore()
                raise ui.die("the build failed. The Nix files are restored.")
    except (KeyboardInterrupt, backup.Stopped):
        restore()
        raise ui.Exit(130, "\ndotkit: stopped. The Nix files are restored.")
    copies.discard()
    if n.activate() != 0:
        raise ui.die(APPLY_FAILED)
    ui.progress("Installing the mise tools...")
    if not proc.mise(env.home, ["install"]).ok:
        raise ui.die(APPLY_FAILED)
