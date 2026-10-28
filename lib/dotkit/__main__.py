"""The dotkit command in Python. lib/run.py starts it as `run.py dotkit ARGS`.

main() loads the environment, runs the command, and turns the exceptions of
core/ui.py into the exit code: ui.Exit prints its text, Ctrl-C exits 130,
and SIGTERM or SIGHUP exit 128 plus the signal.
"""

from __future__ import annotations

import os
import signal
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    # A closed pipe ends dotkit quietly, as it ends a shell script: dotkit list | head. While dotkit changes
    # files, backup.on_signals() turns it into backup.Stopped, so that the files go back first.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    from .core import backup, env, ui

    argv = sys.argv[1:] if argv is None else argv
    # --no-color goes with every command, before `--`.
    end = argv.index("--") if "--" in argv else len(argv)
    if "--no-color" in argv[:end]:
        ui.no_color()
        argv = [a for i, a in enumerate(argv) if i >= end or a != "--no-color"]
    try:
        e = env.load()
        if argv[:1] == ["__complete"]:
            from . import complete
            return complete.main(e, argv[1:])
        from . import cli
        return cli.main(e, argv)
    except ui.Exit as x:
        if x.text:
            ui.note(ui.error_text(x.text))
        return x.code
    except KeyboardInterrupt:
        return 130
    except (backup.Stopped, BrokenPipeError) as x:
        code = 128 + (x.signum if isinstance(x, backup.Stopped) else signal.SIGPIPE)
        if code == 128 + signal.SIGPIPE:
            # Python flushes stdout at exit: send it to /dev/null, so that no second error shows.
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return code
    except (OSError, ValueError) as x:
        # A file that dotkit cannot read or write, or text that is not what it expects: a message, not a
        # traceback. The text of OSError names the file.
        ui.note(ui.error_text(f"dotkit: {x}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
