"""The paths of a run: the dotfiles repository, dotkit, the home, and the dotkit checkout of DOTKIT_DIR.

load() checks the environment in this order (BEHAVIOR.md,
rows G1 to G5): DOTFILES must be set and must be a directory, then DOTKIT_DIR
must be a directory when it is set. It exports the resolved DOTFILES for the
commands that dotkit runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, MutableMapping, Optional

from . import ui

# The root of dotkit: lib/dotkit/core/env.py -> the directory with the dotkit launcher.
DOTKIT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Env:
    repo: Path
    dotkit: Path
    home: Path
    dotkit_dir: Optional[Path] = None

    @property
    def flake(self) -> str:
        return f"path:{self.repo}/nix"

    @property
    def result(self) -> Path:
        """The link to the built system."""
        return self.repo / ".build/system"

    @property
    def override(self) -> List[str]:
        """The nix options that select the checkout of DOTKIT_DIR instead of the locked release."""
        if self.dotkit_dir is None:
            return []
        return ["--override-input", "dotkit", f"git+file://{self.dotkit_dir}", "--no-write-lock-file",
                "--option", "warn-dirty", "false"]


def _directory(value: str) -> Optional[Path]:
    """The physical path of a directory, as `cd -- VALUE && pwd -P` gives it; None when it is not one."""
    path = Path(value)
    if not path.is_dir() or not os.access(path, os.X_OK):
        return None
    return Path(os.path.realpath(path))


def load(environ: Optional[MutableMapping[str, str]] = None, dotkit: Path = DOTKIT) -> Env:
    environ = os.environ if environ is None else environ
    value = environ.get("DOTFILES", "")
    if not value:
        raise ui.Exit(2, "dotkit: DOTFILES is not set. Run dotkit, or ./dotkit in your dotfiles repository.")
    repo = _directory(value)
    if repo is None:
        raise ui.Exit(1, f"dotkit: DOTFILES is not a directory: {value}")
    dotkit_dir = None
    value = environ.get("DOTKIT_DIR", "")
    if value:
        dotkit_dir = _directory(value)
        if dotkit_dir is None:
            raise ui.Exit(2, f"dotkit: DOTKIT_DIR is not a directory: {value}")
    environ["DOTFILES"] = str(repo)
    home = Path(environ.get("HOME") or os.path.expanduser("~"))
    return Env(repo=repo, dotkit=dotkit, home=home, dotkit_dir=dotkit_dir)
