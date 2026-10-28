"""The sources of the audit: one module per kind of tool or package. Each one has

  NAME     the key of its findings and issues: nix, brew, mise, ...
  LABEL    what the report shows after a version: (Nix)
  NOUNS    one and many, for "Checked 38 Nix packages"
  collect  (Context) -> model.Part

A source runs its own commands, through core/proc.py, and keeps its decisions in pure functions that
its tests check. When a source does not apply here, for example the tools are not installed in CI, its
part is skipped, not failed. SOURCES is the order of the report.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from ...core import proc
from ...core.env import Env
from ..model import Part
from . import actions, binaries, brew, dotkit, mise, nix, packages

SOURCES = (nix, brew, mise, packages, binaries, actions, dotkit)
NAMES = tuple(s.NAME for s in SOURCES)
LABELS = {s.NAME: s.LABEL for s in SOURCES}
NOUNS = {s.NAME: s.NOUNS for s in SOURCES}


CONFIG_ONLY = "--config-only checks the configuration, not the installed tools"


class Context:
    """What the sources share: the paths of the run, and the output of `mise ls`, which runs once.

    root: the directory that the absolute paths of the system are in (/etc/profiles, /run/current-system,
    /opt/homebrew). It is / but for the scenario tests, which set DOTKIT_AUDIT_ROOT.
    """

    def __init__(self, env: Env, root: Optional[Path] = None, config_only: bool = False):
        self.env = env
        # --config-only: what the configuration declares, not the tools that are installed here (CI).
        self.config_only = config_only
        self.root = root or Path(os.environ.get("DOTKIT_AUDIT_ROOT") or "/")
        self._mise: Optional[Dict[str, List[dict]]] = None
        self._mise_done = False

    def system(self, path: str) -> Path:
        """An absolute path of the system, under root."""
        return self.root / path.lstrip("/")

    def mise_ls(self) -> Optional[Dict[str, List[dict]]]:
        """`mise ls --global --json` in $HOME: key -> the entries of the tool; None without mise."""
        if not self._mise_done:
            self._mise_done = True
            r = proc.mise(self.env.home, ["ls", "--global", "--json"], stdout=proc.CAPTURE, stderr=proc.DEVNULL)
            try:
                data = json.loads(r.out) if r.ok else None
            except ValueError:
                data = None
            if isinstance(data, dict):
                self._mise = {k: [e for e in v if isinstance(e, dict)] for k, v in data.items() if isinstance(v, list)}
        return self._mise

    def mise_installed(self) -> Dict[str, List[dict]]:
        """The installed and active entries of each global tool; none with config_only."""
        if self.config_only:
            return {}
        return {k: [e for e in v if e.get("installed") and e.get("active") and isinstance(e.get("version"), str)]
                for k, v in (self.mise_ls() or {}).items()}


def collect(ctx: Context) -> Dict[str, Part]:
    return {s.NAME: s.collect(ctx) for s in SOURCES}
