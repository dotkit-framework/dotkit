"""Copies of files while a command changes them, so that a failure or a signal can put them back.

restore() writes each copy back into the file (a link stays a link), and
both restore() and discard() remove the copies. Stopped is the exception for
SIGTERM and SIGHUP; on_signals() turns them into it, so that a `finally` or
an `except` can restore the files, as the traps of the Bash scripts did.
"""

from __future__ import annotations

import contextlib
import shutil
import signal
import tempfile
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional


class Stopped(BaseException):
    """dotkit got SIGTERM or SIGHUP."""

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


# The signals that stop dotkit while it changes files. SIGINT raises KeyboardInterrupt already. SIGPIPE: the
# reader of the output went away (dotkit upgrade -n | head).
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGPIPE)


@contextlib.contextmanager
def on_signals() -> Iterator[None]:
    """While the block runs, the signals of STOP_SIGNALS raise Stopped, so that a `finally` can restore files."""
    def handler(signum, _frame):
        raise Stopped(signum)

    old = {s: signal.signal(s, handler) for s in STOP_SIGNALS}
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)


@contextlib.contextmanager
def shielded() -> Iterator[None]:
    """While the block runs, SIGINT and the signals of STOP_SIGNALS wait: for the restore of files."""
    signals = (signal.SIGINT, *STOP_SIGNALS)
    old = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


class Backup:
    def __init__(self, paths: Iterable[Path]):
        """Copies the files that exist. A file that does not exist is not restored."""
        self.dir: Optional[Path] = Path(tempfile.mkdtemp(prefix="dotkit-"))
        self.copies: Dict[Path, Path] = {}
        for i, path in enumerate(paths):
            if path.exists():
                copy = self.dir / f"{i}-{path.name}"
                shutil.copyfile(path, copy)
                self.copies[path] = copy

    def restore(self) -> None:
        with shielded():
            for path, copy in self.copies.items():
                shutil.copyfile(copy, path)
            self.discard()

    def discard(self) -> None:
        if self.dir is not None:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None
            self.copies = {}
