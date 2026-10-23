"""A read-only inventory of this Mac: packages, tools, shell files, and config.

Nothing here writes a file or installs anything. Commands run with a
timeout; a command that fails gives an empty result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import secrets
from .context import Context

@dataclass
class Scan:
    home: str
    secret_files: list[str] = field(default_factory=list)
    # Scan steps that failed, with the error. The report lists them.
    warnings: list[str] = field(default_factory=list)


class Scanner:
    def __init__(self, ctx: Context):
        self.home = ctx.home
        self.base = ctx.base

    def h(self, rel: str) -> Path:
        return self.home / rel

    # --- mise and version managers

    def scan_other_config(self) -> None:
        for rel in secrets.SECRET_FILES:
            if self.h(rel).exists():
                self.s.secret_files.append(rel)

    def run(self) -> Scan:
        self.s = Scan(home=str(self.home))
        for step in (
            self.scan_other_config,
        ):
            try:
                step()
            except Exception as e:  # noqa: BLE001 - one failed step must not stop the others
                self.s.warnings.append(f"{step.__name__.removeprefix('scan_')}: {type(e).__name__}: {e}")
        return self.s
