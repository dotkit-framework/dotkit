"""The manifest of the links: etc/dotkit/links.json of a system (nix/links.nix, nix/darwin.nix).

It has the dotkit directory, the base links (target -> file of the base),
the layers (base target -> the file of yours that its tool reads after it),
the links of the configuration (target -> source, or None for a link set to
null), the copies, and the backup extension of Home Manager. The overlap
report reads it from a built system; dotkit own evaluates it from the
configuration as it is now.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from . import nix, ui
from .env import Env

PATH = "etc/dotkit/links.json"
ATTR = 'darwinConfigurations.mac.config.environment.etc."dotkit/links.json".text'


@dataclass(frozen=True)
class Manifest:
    dotkit: str  # "" when the manifest does not have it
    base: Dict[str, str]
    layers: Dict[str, str]
    links: Dict[str, Optional[str]]
    copies: Dict[str, str]
    backup_ext: str  # "" without one

    @classmethod
    def parse(cls, data: object) -> Optional[Manifest]:
        """The manifest of JSON data; None for another format (version 1, with base, layers, and links)."""
        if not (isinstance(data, dict) and data.get("version") == 1
                and all(isinstance(data.get(k), dict) for k in ("base", "layers", "links"))):
            return None

        def text(key: str) -> str:
            value = data.get(key)
            return value if isinstance(value, str) else ""

        copies = data.get("copies")
        return cls(text("dotkit"), data["base"], data["layers"], data["links"],
                   copies if isinstance(copies, dict) else {}, text("backupFileExtension"))

    @classmethod
    def from_system(cls, system: Path) -> Optional[Manifest]:
        """The manifest of a built system; None, with a warning when it cannot be read, or without one when the
        system has none (a release of dotkit before the manifest)."""
        path = system / PATH
        try:
            data = json.loads(path.read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            ui.warn(f"cannot read {path}: {e}. The config files are not compared with the base.")
            return None
        manifest = cls.parse(data)
        if manifest is None:
            ui.warn(f"{path} has a format that this dotkit does not know. The config files are not compared.")
        return manifest

    @classmethod
    def evaluate(cls, env: Env) -> Manifest:
        """The manifest of the configuration as it is now, by nix eval. Stops with the exit code of nix, or with
        an error for another format."""
        r = nix.Nix(env).eval_raw(ATTR)
        if not r.ok:
            raise ui.Exit(r.code)
        try:
            manifest = cls.parse(json.loads(r.value))
        except ValueError:
            manifest = None
        if manifest is None:
            raise ui.die("the configuration has no link manifest that this dotkit knows. Run dotkit upgrade dotkit.")
        return manifest

    def source(self, target: str) -> Optional[str]:
        """The source of the link of target: the configuration's, else the base file; None for none."""
        return self.links.get(target, self.base.get(target))

    def replaced(self, target: str) -> bool:
        """A base target that the configuration links to another source, or sets to null."""
        return target in self.base and self.source(target) != self.base[target]

    def is_script(self, target: str) -> bool:
        """A base target that is a script of the base, not a config file."""
        return bool(self.dotkit) and self.base.get(target, "").startswith(f"{self.dotkit}/scripts/")
