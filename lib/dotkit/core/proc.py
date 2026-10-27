"""Run a command: the only place where dotkit starts processes.

Each stream is INHERIT (the terminal of dotkit), CAPTURE, or DEVNULL;
stderr can also go to stdout (STDOUT, like 2>&1) or through a line filter.
dotkit flushes its own output first, so the order of the lines stays.

Ctrl-C reaches the command too (it is in the same process group). run()
waits until the command ends, then raises KeyboardInterrupt, so that the
caller can restore files before dotkit exits.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Union

from . import ui
from .backup import Stopped, shielded

INHERIT = "inherit"
CAPTURE = "capture"
DEVNULL = "devnull"
STDOUT = "stdout"

LineFilter = Callable[[str], Optional[str]]


@dataclass
class Result:
    code: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    def json_object(self, what: str) -> dict:
        """stdout as a JSON object; die with a message that names `what` (the command) when it is not one."""
        try:
            data = json.loads(self.out)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ui.die(f"{what} did not print a JSON object.")
        return data

    @property
    def value(self) -> str:
        """stdout without the line breaks at its end, as $(...) gives it."""
        return self.out.rstrip("\n")


def _decode(data: Optional[bytes]) -> str:
    return (data or b"").decode("utf-8", "surrogateescape")


def _pump(fd: int, line_filter: LineFilter) -> None:
    """Copy the lines from fd to stderr through the filter, each line at once."""
    with os.fdopen(fd, "rb") as f:
        for raw in f:
            line = line_filter(_decode(raw))
            if line is not None:
                ui.raw_err(line)


def run(argv: Sequence[str], *, cwd: Union[str, Path, None] = None, env: Optional[Dict[str, str]] = None,
        stdin: Union[str, None] = None, stdout: str = INHERIT, stderr: str = INHERIT,
        err_filter: Optional[LineFilter] = None) -> Result:
    """Runs argv and returns its exit code and the captured text.

    stdin: None inherits it, DEVNULL is /dev/null, and other text goes to the
    command. err_filter: with stderr=INHERIT, each line of stderr goes through
    it; a line that it returns as None is dropped. A command that is not
    found gives the exit code 127, as in a shell.
    """
    streams = {INHERIT: None, CAPTURE: subprocess.PIPE, DEVNULL: subprocess.DEVNULL, STDOUT: subprocess.STDOUT}
    full_env = None if env is None else {**os.environ, **env}
    if stdin is None:
        stdin_arg, data = None, None
    elif stdin == DEVNULL:
        stdin_arg, data = subprocess.DEVNULL, None
    else:
        stdin_arg, data = subprocess.PIPE, stdin.encode("utf-8", "surrogateescape")
    err_arg = streams[stderr]
    pump = None
    read_fd = None
    if err_filter is not None and stderr == INHERIT:
        read_fd, err_arg = os.pipe()
    ui.flush()
    try:
        p = subprocess.Popen(list(argv), cwd=cwd, env=full_env, stdin=stdin_arg, stdout=streams[stdout],
                             stderr=err_arg)
    except OSError as e:
        if read_fd is not None:
            os.close(read_fd)
            os.close(err_arg)
        if stderr == INHERIT:
            ui.warn(f"{argv[0]}: command not found" if isinstance(e, FileNotFoundError) else f"{argv[0]}: {e}")
        return Result(127)
    if read_fd is not None:
        os.close(err_arg)
        pump = threading.Thread(target=_pump, args=(read_fd, err_filter), daemon=True)
        pump.start()
    try:
        out, err = p.communicate(data)
    except KeyboardInterrupt:
        # Ctrl-C reached the whole process group, so the command has it too.
        _wait_after_interrupt(p)
        raise
    except Stopped as x:
        # SIGTERM or SIGHUP reached only dotkit: pass it on, and let the command end before the caller
        # restores files that the command can still write.
        if x.signum != signal.SIGPIPE:
            p.send_signal(x.signum)
        _wait_after_interrupt(p)
        raise
    if pump is not None:
        pump.join()
    return Result(p.returncode, _decode(out), _decode(err))


def _wait_after_interrupt(p: subprocess.Popen) -> None:
    """Wait until the command ends, also through more signals."""
    with shielded():
        p.wait()


def mise(home: Path, args: List[str], **kw) -> Result:
    """mise in the home directory, so that only the global config and settings apply."""
    return run(["mise", *args], cwd=home, **kw)


def which(name: str) -> Optional[str]:
    """The path of a command on PATH, as `command -v` finds it."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d or ".", name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None
