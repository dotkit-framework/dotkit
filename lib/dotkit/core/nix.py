"""Nix commands for the flake of the dotfiles repository.

With DOTKIT_DIR, the commands that read the flake get the override of
env.Env.override, and their stderr goes through LockWarning: it drops the
warning that Nix prints for each command with --override-input (BEHAVIOR.md,
rows A9 and A10).
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

from . import proc
from .env import Env

WARNING = re.compile(r"^warning: not writing (modified )?lock file of flake")
CONTINUED = re.compile(r"^(• |  →|    )")


class LockWarning:
    """A line filter: the lock file warning and its list of inputs are dropped, the other lines pass."""

    def __init__(self) -> None:
        self.skip = False

    def __call__(self, line: str) -> Optional[str]:
        if WARNING.match(line):
            self.skip = True
            return None
        if self.skip and CONTINUED.match(line):
            return None
        self.skip = False
        # awk prints each line with a line break, also the last one.
        return line if line.endswith("\n") else line + "\n"


class Nix:
    def __init__(self, env: Env):
        self.env = env

    def flake(self, args: List[str], **kw) -> proc.Result:
        """nix ARGS for a command that reads the flake: with DOTKIT_DIR, the override and the filter."""
        if not self.env.override:
            return proc.run(["nix", *args], **kw)
        return proc.run(["nix", *args, *self.env.override], err_filter=LockWarning(), **kw)

    def eval_raw(self, attr: str) -> proc.Result:
        return self.flake(["eval", "--raw", f"{self.env.flake}#{attr}"], stdout=proc.CAPTURE)

    def build_system(self) -> int:
        """Builds the system and links it to env.result. With DOTKIT_DIR, nix evaluates through flake()
        and builds the derivation without the flake: no lock file warning, and nix keeps its progress bar."""
        if not self.env.override:
            return proc.run(["nix", "build", f"{self.env.flake}#darwinConfigurations.mac.system",
                             "--out-link", str(self.env.result)]).code
        drv = self.eval_raw("darwinConfigurations.mac.system.drvPath")
        if not drv.ok:
            return drv.code
        return proc.run(["nix", "build", f"{drv.value}^*", "--out-link", str(self.env.result)]).code

    def switch(self) -> int:
        """Builds as this user, then activates as root; the first failure stops it.

        darwin-rebuild switch would evaluate the flake again as root, and root
        cannot read the Git repository of another user (a git+file input such
        as DOTKIT_DIR).
        """
        code = self.build_system()
        if code:
            return code
        return self.activate()

    def activate(self) -> int:
        """Activates the system that build_system linked: nix-env --set, then darwin-rebuild activate."""
        system = os.readlink(self.env.result)
        code = proc.run(["sudo", "-H", "/usr/bin/env", "NIX_REMOTE=daemon", f"{system}/sw/bin/nix-env",
                         "-p", "/nix/var/nix/profiles/system", "--set", system]).code
        if code:
            return code
        return proc.run(["sudo", f"{system}/sw/bin/darwin-rebuild", "activate"]).code

    def brewfile(self) -> proc.Result:
        return self.eval_raw("darwinConfigurations.mac.config.homebrew.brewfile")
