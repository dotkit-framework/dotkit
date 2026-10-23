"""The data that the parts of the migration pass to each other."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Plan:
    repo: Path
    keymap: str
    # Files of the new repository, relative to it, with their content. They
    # replace the template files of the same name.
    files: dict[str, str] = field(default_factory=dict)
    # Files and directories to copy into the repository: repository path -> source.
    copies: dict[str, Path] = field(default_factory=dict)
    # Files with secrets, in local/ of the repository, which Git ignores.
    local: dict[str, str] = field(default_factory=dict)
    links: dict[str, str | None] = field(default_factory=dict)
    # Links of yours (not of Home Manager) where the new configuration links a
    # file, relative to the home directory. Home Manager does not move a link
    # aside, so scripts/setup renames each one to <name>.before-dotkit right
    # before the first activation. writer.write_new() lists them in .build/links-in-way.
    move_links: list[str] = field(default_factory=list)
    # Why the plan cannot be applied, for example ~/.config is a link. The
    # wizard shows them, and nothing is written.
    blockers: list[str] = field(default_factory=list)
    home: str = ""
    summary: list[str] = field(default_factory=list)
    report: str = ""
