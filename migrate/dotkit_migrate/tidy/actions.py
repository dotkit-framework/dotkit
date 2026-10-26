"""The changes of dotkit tidy: install a cask, quit an app, uninstall a tool, move a path to the Trash (TIDY.md,
sections 6 and 8.1).

This is the only module of the package that changes the Mac. cli.py creates
the one Actions object; the tests give a fake with the same methods.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import List, Tuple

from ..catalog import BREW_ENV


class Actions:
    def __init__(self, brew: str):
        self.brew = brew

    def brew_install(self, token: str, how: str) -> bool:
        """`brew install --cask --adopt` or `--force`. brew writes to the terminal, so a pkg installer can ask for
        the password. True when brew exits with 0."""
        assert how in ("adopt", "force")
        try:
            r = subprocess.run([self.brew, "install", "--cask", f"--{how}", token],
                               env={**os.environ, **BREW_ENV}, check=False)
        except OSError:
            return False
        return r.returncode == 0

    def uninstall(self, argv: List[str], path_first: str = "") -> bool:
        """Run the uninstall command of a tool's package manager, with its output on the terminal. path_first goes
        first in PATH, so that the npm of an nvm node runs with that node. True when it exits with 0."""
        env = dict(os.environ)
        if path_first:
            env["PATH"] = path_first + os.pathsep + env.get("PATH", "")
        if argv and os.path.basename(argv[0]) == "brew":
            env.update(BREW_ENV)
        try:
            r = subprocess.run(argv, env=env, check=False)
        except OSError:
            return False
        return r.returncode == 0

    def running(self, path: str) -> bool:
        try:
            out = subprocess.run(["/bin/ps", "-axo", "comm="], capture_output=True, text=True, check=False,
                                 errors="replace").stdout
        except OSError:
            return False
        return any(line.startswith(path.rstrip("/") + "/") for line in out.splitlines())

    def quit_app(self, bundle_id: str, path: str, wait: float = 30) -> bool:
        """Ask the app to quit, and wait until no process runs from its bundle. True when it does not run."""
        if not self.running(path):
            return True
        if bundle_id:
            script = 'quit app id "' + bundle_id.replace("\\", "\\\\").replace('"', '\\"') + '"'
            try:
                subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, timeout=wait,
                               check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if not self.running(path):
                return True
            time.sleep(0.5)
        return not self.running(path)

    def trash(self, path: str) -> Tuple[bool, str]:
        """Move an app, a file, or a directory to the Trash, where the Finder can put it back. (True, "") or (False,
        the error)."""
        tools: List[List[str]] = []
        if os.access("/usr/bin/trash", os.X_OK):
            tools.append(["/usr/bin/trash", path])
        # The Finder does the same on a macOS without /usr/bin/trash.
        tools.append(["/usr/bin/osascript", "-e", "on run argv", "-e",
                      "tell application \"Finder\" to delete (POSIX file (item 1 of argv))", "-e", "end run", path])
        error = ""
        for argv in tools:
            try:
                r = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False, errors="replace")
            except (OSError, subprocess.TimeoutExpired) as e:
                error = str(e)
                continue
            if r.returncode == 0 and not os.path.lexists(path):
                return True, ""
            said = (r.stderr or r.stdout).strip().splitlines()
            error = said[-1] if said else f"{argv[0]} exited with {r.returncode}"
        return False, error or "the app is still there"
