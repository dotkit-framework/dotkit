"""Running other programs, read-only. Tests replace the runner with a fake."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

class Runner:
    """Runs a command and returns its output, or None when it fails."""

    def which(self, name: str, extra: tuple[str, ...] = ()) -> str | None:
        """The path of a command: PATH first, then the extra directories."""
        found = shutil.which(name)
        if found:
            return found
        for d in extra:
            p = Path(d).expanduser() / name
            if os.access(p, os.X_OK) and p.is_file():
                return str(p)
        return None

    def run(self, argv: list[str], timeout: float = 120, env: dict[str, str] | None = None) -> str | None:
        try:
            r = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                env={**os.environ, **(env or {})},
                cwd=os.environ.get("HOME") or "/",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0:
            return None
        return r.stdout
