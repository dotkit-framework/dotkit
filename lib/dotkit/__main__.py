"""The dotkit command in Python. lib/run.py starts it as `run.py dotkit ARGS`.

main() loads the environment, runs the command, and turns the exceptions of
core/ui.py into the exit code: ui.Exit prints its text, Ctrl-C exits 130,
and SIGTERM or SIGHUP exit 128 plus the signal.
"""

from __future__ import annotations

import signal
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    # A closed pipe ends dotkit quietly, as it ends a shell script: dotkit list | head.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    from .core import backup, env, ui

    argv = sys.argv[1:] if argv is None else argv
    try:
        e = env.load()
        from . import commands
        return commands.main(e, argv)
    except ui.Exit as x:
        if x.text:
            ui.note(x.text)
        return x.code
    except KeyboardInterrupt:
        return 130
    except backup.Stopped as x:
        return 128 + x.signum


if __name__ == "__main__":
    sys.exit(main())
