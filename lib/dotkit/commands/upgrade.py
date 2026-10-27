"""dotkit upgrade: one plan for Nix, Homebrew, and mise, one question, then the upgrade (PARITY.md, rows U).

The lock files that `upgrade nix` updates: nix/flake.lock of the dotfiles
repository (the dotkit release), and with DOTKIT_DIR the flake.lock of the
checkout (Nixpkgs and the other inputs). The inputs of the checkout have the
label dotkit/. Until the switch, any exit restores the lock files: an error,
the answer no, Ctrl-C, or a signal.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

from ..core import backup, lists, nix, proc, ui
from ..core.env import Env


@dataclass
class Lock:
    file: Path
    flake: str
    label: str


def locks(env: Env) -> List[Lock]:
    out = [Lock(env.repo / "nix/flake.lock", env.flake, "")]
    if env.dotkit_dir is not None:
        out.append(Lock(env.dotkit_dir / "flake.lock", f"git+file://{env.dotkit_dir}", "dotkit/"))
    return out


def _read_json(path: Path) -> dict:
    """A lock file as a JSON object; {} when it does not exist."""
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise ui.die(f"{path} is not valid JSON: {e}.")
    if not isinstance(data, dict):
        raise ui.die(f"{path} is not a JSON object.")
    return data


def _root_inputs(lock: Any) -> List[Tuple[str, str]]:
    """(input name, node) for each input of the root that does not follow another input."""
    inputs = (((lock.get("nodes") or {}).get("root") or {}).get("inputs") or {}) if isinstance(lock, dict) else {}
    return [(k, v) for k, v in inputs.items() if isinstance(v, str)]


def flake_inputs(env: Env) -> List[Tuple[int, str]]:
    """(index of the lock file, the input name with the label of the lock file) for each input."""
    out = []
    for i, lock in enumerate(locks(env)):
        out += [(i, lock.label + name) for name, _ in _root_inputs(_read_json(lock.file))]
    return out


def _info(node: Any) -> str:
    locked = (node or {}).get("locked") or {}
    day = datetime.datetime.fromtimestamp(locked.get("lastModified") or 0, datetime.timezone.utc)
    return f"{day:%Y-%m-%d} {(locked.get('rev') or '')[:7]}"


def lock_changes(old_file: Path, new_file: Path, label: str) -> List[Tuple[str, str, str]]:
    """One plan row per input whose lock changed: the name with the label, before, after."""
    old, new = _read_json(old_file), _read_json(new_file)
    old_nodes, new_nodes = old.get("nodes") or {}, new.get("nodes") or {}
    rows = []
    for name, node in _root_inputs(new):
        before, after = old_nodes.get(node), new_nodes.get(node)
        if ((before or {}).get("locked") or {}) != ((after or {}).get("locked") or {}):
            rows.append((label + name, _info(before), _info(after)))
    return rows


def plan_row(src: str, name: str, before: str, after: str) -> str:
    return f"  {ui.pad(src, 5)} {ui.pad(name, 36)} {ui.pad(before, 22)} -> {after}"


def _tsv(value: Any) -> str:
    """A JSON value in a row: @tsv of jq, then `read -r`; null is empty."""
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value)


class Upgrade:
    def __init__(self, env: Env, dry_run: bool, yes: bool):
        self.env, self.dry_run, self.yes = env, dry_run, yes
        self.nix = nix.Nix(env)
        # The copies of the lock files while the upgrade runs, by index.
        self.copies: dict = {}

    def restore_locks(self) -> None:
        for copy in self.copies.values():
            copy.restore()
        self.copies = {}

    def keep_locks(self) -> None:
        for copy in self.copies.values():
            copy.discard()
        self.copies = {}

    def run(self, words: List[str]) -> int:
        try:
            with backup.on_signals():
                return self._run(words)
        finally:
            self.restore_locks()

    def _run(self, words: List[str]) -> int:
        up_nix = up_brew = up_mise = all_inputs = False
        selected: List[Tuple[int, str]] = []
        for a in words:
            if a == "nix":
                up_nix = all_inputs = True
            elif a == "brew":
                up_brew = True
            elif a == "mise":
                up_mise = True
            else:
                # A bare name also selects an input of the dotkit checkout: nixpkgs is dotkit/nixpkgs.
                inputs = flake_inputs(self.env)
                found = [(i, n) for i, n in inputs if n in (a, "dotkit/" + a)]
                if not found:
                    names = " ".join(n for _, n in inputs)
                    raise ui.usage_error(f"'{a}' is not a source or a flake input. Use nix, brew, mise, or: {names}.",
                                         "Run dotkit upgrade --help.")
                up_nix = True
                selected += found
        if not (up_nix or up_brew or up_mise):
            up_nix = up_brew = up_mise = True
        # `nix` means all inputs, also with an input name: upgrade nix nixpkgs.
        if all_inputs:
            selected = []

        nix_rows: List[Tuple[str, str, str]] = []
        brew_rows: List[Tuple[str, str, str]] = []
        mise_rows: List[Tuple[str, str, str]] = []
        if up_nix:
            ui.note("Checking Nix...")
            nix_rows = self._check_nix(selected)
        if up_brew:
            ui.note("Checking Homebrew...")
            brew_rows = self._check_brew()
        if up_mise:
            ui.note("Checking mise...")
            mise_rows = self._check_mise()
        if not (nix_rows or brew_rows or mise_rows):
            ui.out("Everything is up to date.")
            return 0
        for src, rows in (("nix", nix_rows), ("brew", brew_rows), ("mise", mise_rows)):
            for row in rows:
                ui.out(plan_row(src, *row))
        if nix_rows:
            code = self.nix.build_system()
            if code:
                return code
            ui.out("\nNix package changes:")
            code = proc.run(["nix", "store", "diff-closures", "/run/current-system", str(self.env.result)]).code
            if code:
                return code
        if self.dry_run:
            self.restore_locks()
            ui.note("No change. Run dotkit upgrade to apply.")
            return 0
        if not ui.confirm("Upgrade?", self.yes):
            self.restore_locks()
            raise ui.die("nothing changed.")

        if nix_rows:
            if self.nix.switch() != 0:
                self.restore_locks()
                raise ui.die("the switch failed. The lock files are restored.")
            self.keep_locks()
        if brew_rows:
            # Read the Brewfile from the flake, so it matches the config (PARITY.md, B4).
            brewfile = self.nix.brewfile()
            if not brewfile.ok:
                return brewfile.code
            code = proc.run(["brew", "bundle", "install", "--file=-"], env={"HOMEBREW_NO_INSTALL_CLEANUP": "1"},
                            stdin=brewfile.out).code
            if code:
                return code
        if mise_rows:
            code = proc.mise(self.env.home, ["upgrade"]).code
            if code:
                return code
        if nix_rows:
            ui.note("Commit nix/flake.lock to keep the upgrade.")
            if self.env.dotkit_dir is not None:
                ui.note(f"Commit flake.lock in {self.env.dotkit_dir} too, if it changed.")
        return 0

    def _check_nix(self, selected: List[Tuple[int, str]]) -> List[Tuple[str, str, str]]:
        rows: List[Tuple[str, str, str]] = []
        for i, lock in enumerate(locks(self.env)):
            # The input names for this lock file, without the label. None: all of them.
            inputs = [n[len(lock.label):] for j, n in selected if j == i]
            if selected and not inputs:
                continue
            copy = backup.Backup([lock.file])
            self.copies[i] = copy
            r = proc.run(["nix", "flake", "update", *inputs, "--flake", lock.flake], stdout=proc.CAPTURE,
                         stderr=proc.STDOUT)
            if not r.ok:
                ui.raw_err(r.value + "\n")
                raise ui.die(f"nix flake update failed for {lock.file}. The lock files are restored.")
            found = lock_changes(next(iter(copy.copies.values()), lock.file), lock.file, lock.label)
            if found:
                rows += found
            else:
                copy.restore()
                del self.copies[i]
        return rows

    def _check_brew(self) -> List[Tuple[str, str, str]]:
        r = proc.run(["brew", "update", "--quiet"], stdout=proc.CAPTURE, stderr=proc.STDOUT)
        if not r.ok:
            ui.raw_err(r.value + "\n")
            raise ui.die("brew update failed.")
        # Only the declared formulae and casks: brew bundle does not upgrade the others (for example
        # dependencies, or formulae installed by hand).
        declared = set()
        for root in (self.env.dotkit, self.env.repo):
            for rel in ("nix/brews.txt", "nix/casks.txt"):
                declared.update(lists.names(root / rel))
        r = proc.run(["brew", "outdated", "--json=v2"], env={"HOMEBREW_NO_AUTO_UPDATE": "1"}, stdout=proc.CAPTURE)
        if not r.ok:
            raise ui.Exit(r.code)
        data = r.json_object("brew outdated --json=v2")
        rows = []
        for item in (data.get("formulae") or []) + (data.get("casks") or []):
            if item.get("name") in declared:
                versions = " ".join(_tsv(v) for v in item.get("installed_versions") or [])
                rows.append((_tsv(item.get("name")), versions, _tsv(item.get("current_version"))))
        return rows

    def _check_mise(self) -> List[Tuple[str, str, str]]:
        r = proc.mise(self.env.home, ["outdated", "--json"], stdout=proc.CAPTURE)
        if not r.ok:
            raise ui.Exit(r.code)
        data = r.json_object("mise outdated --json")
        return [(k, _tsv((v or {}).get("current")), _tsv((v or {}).get("latest"))) for k, v in data.items()]


def upgrade(env: Env, words: List[str], dry_run: bool, yes: bool) -> Optional[int]:
    return Upgrade(env, dry_run, yes).run(words)
