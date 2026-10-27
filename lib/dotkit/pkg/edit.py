"""Change the Nix lists, build, and apply (PARITY.md, Ad13 and Rm9).

apply_nix() backs up the lists, runs the edit, and builds. When the edit or
the build fails, or on SIGINT, SIGTERM, or SIGHUP, it restores the lists.
Then it runs `dotkit apply`; the lists keep the change when that fails.
"""

from __future__ import annotations

import signal
from typing import Callable

from ..core import backup, nix, proc, ui
from ..core.env import Env

LISTS = ("packages.txt", "brews.txt", "casks.txt")


def apply_nix(env: Env, edit: Callable[[], None]) -> None:
    names = list(LISTS)
    if (env.repo / "nix/mas.txt").is_file():
        names.append("mas.txt")
    copies = backup.Backup([env.repo / "nix" / n for n in names])
    try:
        with backup.on_signals():
            try:
                edit()
            except OSError:
                copies.restore()
                raise ui.die("could not change the Nix files. They are restored.")
            if nix.Nix(env).build_system() != 0:
                copies.restore()
                raise ui.die("the build failed. The Nix files are restored.")
    except (KeyboardInterrupt, backup.Stopped):
        # Ignore a second signal while the files go back.
        old = signal.signal(signal.SIGINT, signal.SIG_IGN)
        copies.restore()
        signal.signal(signal.SIGINT, old)
        raise ui.Exit(130, "\ndotkit: stopped. The Nix files are restored.")
    copies.discard()
    if proc.run([str(env.dotkit / "dotkit"), "apply"]).code != 0:
        raise ui.die("dotkit apply failed. The Nix files keep the change. Fix the problem, then run dotkit apply.")
