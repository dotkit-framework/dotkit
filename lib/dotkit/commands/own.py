"""dotkit own: make a base config file yours, or give it back to the base (BEHAVIOR.md, rows Ow).

Only the base files that are the one file of their tool can be owned: a
copy goes to config/ of the repository, and a link in dotkit.links replaces
the base link. The tool can then save its own changes there, for example
the theme of herdr. A base file that its tool reads before a file of yours
(dotkit.layers: zsh, git, Ghostty, mise) cannot be owned: set what you need
in that file. Scripts of the base cannot be owned either.

The links come from the manifest of the configuration (nix/links.nix), by
`nix eval`, so the command sees the links of the repository as they are
now. `dotkit own --release` removes the link line, so the base file comes
back; the copy stays in the repository.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

from ..core import nix, ui
from ..core.env import Env
from ..migrate.base import OWN_FILES
from ..pkg import edit
from . import overlap

ATTR = 'darwinConfigurations.mac.config.environment.etc."dotkit/links.json".text'
FAILED = "nothing changed. Fix the errors above, then run the command again."
SKIP = "skip"  # nothing to do for an argument, and not an error
# The header of a copy, instead of the first comment block of a base file that starts with "# dotkit base".
HEADER = ("# From the dotkit base (dotkit own). After a dotkit upgrade, dotkit doctor shows\n"
          "# how this file differs from the base file.\n")
BLOCK = re.compile(r"^(?P<indent>[ \t]*)dotkit\.links = \{[ \t]*$")
DOTTED = re.compile(r'^(?P<indent>[ \t]*)dotkit\.links\."')


@dataclass
class Change:
    arg: str
    target: str
    nix_file: Path
    # own: the line to add after line index `at`, and the copy; release: the line index to delete.
    at: int
    line: str = ""
    copy_from: Optional[Path] = None
    repo_file: str = ""


def _manifest(env: Env) -> dict:
    r = nix.Nix(env).eval_raw(ATTR)
    if not r.ok:
        raise ui.Exit(r.code)
    try:
        data = json.loads(r.value)
    except ValueError:
        data = None
    if not overlap.valid_manifest(data):
        raise ui.die("the configuration has no link manifest that this dotkit knows. Run dotkit upgrade dotkit.")
    return data


def _script(manifest: dict, target: str) -> bool:
    dotkit = manifest.get("dotkit") or ""
    return manifest["base"][target].startswith(f"{dotkit}/scripts/")


def resolve(arg: str, manifest: dict, home: Path) -> List[str]:
    """The home-relative targets of an argument: a path (~/x, an absolute path in the home, or .config/x),
    or a name of a base file (herdr, starship, gemrc)."""
    if arg.startswith("~/"):
        return [arg[2:]]
    if arg.startswith("/"):
        path = os.path.normpath(arg)
        return [path[len(str(home)) + 1:]] if path.startswith(f"{home}/") else [arg]
    if "/" in arg or arg.startswith("."):
        return [os.path.normpath(arg)]

    def names(target: str) -> List[str]:
        base_name = target.rsplit("/", 1)[-1].lstrip(".")
        return target.split("/") + [base_name, base_name.rsplit(".", 1)[0]]

    return [t for t in sorted(manifest["base"]) if not _script(manifest, t) and arg in names(t)]


def _nix_files(env: Env) -> List[Path]:
    return sorted((env.repo / "nix").glob("*.nix"))


def _read(path: Path) -> List[str]:
    return path.read_text(encoding="utf-8").split("\n")


def _insert_point(env: Env, target: str, source: str) -> Optional[tuple]:
    """(file, line index, line) for a new link: in the one `dotkit.links = {` block, else after the last
    `dotkit.links."..." = ...;` line. None when the files have neither, or more than one block."""
    blocks, dotted = [], []
    for path in _nix_files(env):
        for i, line in enumerate(_read(path)):
            m = BLOCK.match(line)
            if m:
                blocks.append((path, i, f'{m.group("indent")}  "{target}" = "{source}";'))
            m = DOTTED.match(line)
            if m:
                dotted.append((path, i, f'{m.group("indent")}dotkit.links."{target}" = "{source}";'))
    if len(blocks) == 1:
        return blocks[0]
    if not blocks and dotted:
        return dotted[-1]
    return None


def _link_lines(env: Env, target: str) -> List[tuple]:
    """(file, line index) of each line that sets the link of target."""
    row = re.compile(r'^\s*(dotkit\.links\.)?"' + re.escape(target) + r'"\s*=\s*(null|"[^"]*")\s*;\s*(#.*)?$')
    return [(path, i) for path in _nix_files(env) for i, line in enumerate(_read(path)) if row.match(line)]


def _yours(env: Env, source: Optional[str]) -> str:
    if source is None:
        return "null"
    return source[len(str(env.repo)) + 1:] if source.startswith(f"{env.repo}/") else source


class Own:
    def __init__(self, env: Env, dry_run: bool = False, yes: bool = False):
        self.env, self.dry_run, self.yes = env, dry_run, yes

    def run(self, args: Sequence[str], release: bool) -> int:
        manifest = _manifest(self.env)
        changes: List[Change] = []
        failed = False
        for arg in args:
            targets = resolve(arg, manifest, self.env.home)
            if not targets:
                ui.warn(f"{arg}: no config file of the dotkit base has this name. Use its path, for example "
                        "~/.config/starship.toml.")
                failed = True
                continue
            if len(targets) > 1:
                ui.warn(f"{arg}: more than one base file has this name: "
                        + ", ".join(f"~/{t}" for t in targets) + ". Use the path.")
                failed = True
                continue
            target = targets[0]
            if any(c.target == target for c in changes):
                continue
            change = self._release(arg, target, manifest) if release else self._own(arg, target, manifest, changes)
            if change is None:
                failed = True
            elif change != SKIP:
                changes.append(change)
        if failed:
            raise ui.die(FAILED)
        if not changes:
            return 0
        if self.dry_run:
            ui.hint(f"No change. Run the command without -n to {'release' if release else 'own'} "
                    f"{'it' if len(changes) == 1 else 'them'}.")
            return 0
        n = len(changes)
        question = ("Release" if release else "Own") + (" the file?" if n == 1 else f" the {n} files?")
        if not ui.confirm(question, self.yes):
            raise ui.die("nothing changed.")
        nix_files = sorted({c.nix_file for c in changes})
        created = [self.env.repo / c.repo_file for c in changes if c.copy_from is not None]
        edit.apply_nix(self.env, lambda: self._write(changes), paths=nix_files, created=created)
        if release:
            ui.success("Released " + ", ".join(f"~/{c.target}" for c in changes) + ".")
        else:
            ui.success("Owned " + ", ".join(c.repo_file for c in changes) + ".")
            ui.hint("Commit " + ", ".join(c.repo_file for c in changes) + ". After a dotkit upgrade, dotkit doctor "
                    "shows how yours differs from the base file.")
        return 0

    def _own(self, arg: str, target: str, manifest: dict, planned: List[Change]) -> Union[Change, str, None]:
        base, links, layers = manifest["base"], manifest["links"], manifest["layers"]
        where = f"~/{target}"
        if target not in base:
            if target in links:
                ui.out(f"{ui.paint(arg, 'name')}: {where} is yours already ({_yours(self.env, links[target])}).")
                return SKIP
            own = next((o for o in OWN_FILES if o.target == target), None)
            hint = f" Its tool reads it after the dotkit settings, which are in {own.base_place}: create it." if own \
                else ""
            ui.warn(f"{arg}: {where} is not a config file of the dotkit base.{hint}")
            return None
        if target in layers:
            layer = layers[target]
            mine = links.get(layer)
            where_mine = f"your {_yours(self.env, mine)}" if mine else "it: link a file of yours there in dotkit.links"
            ui.warn(f"{arg}: {where} cannot be owned. Its tool reads ~/{layer} after it, so set what you need in "
                    f"{where_mine}.")
            return None
        if _script(manifest, target):
            ui.warn(f"{arg}: {where} is a script of the dotkit base, and cannot be owned.")
            return None
        if links.get(target, base[target]) != base[target]:
            ui.out(f"{ui.paint(arg, 'name')}: {where} is yours already ({_yours(self.env, links.get(target))}). dotkit doctor shows "
                   "how it differs from the base file.")
            return SKIP
        repo_file = "config/" + base[target].rsplit("/", 1)[-1]
        if (self.env.repo / repo_file).exists() or any(c.repo_file == repo_file for c in planned):
            ui.warn(f"{arg}: {repo_file} exists already. Move it, or link it yourself in dotkit.links.")
            return None
        point = _insert_point(self.env, target, repo_file)
        if point is None:
            ui.warn(f"{arg}: cannot find where nix/ sets dotkit.links. Add this line to nix/home.nix, copy the base "
                    f"file to {repo_file}, and run dotkit apply: dotkit.links.\"{target}\" = \"{repo_file}\";")
            return None
        path, at, line = point
        ui.out(f"{ui.paint(arg, 'name')}: copy the base file to {ui.paint(repo_file, 'added')}, and link it as "
               f"{ui.paint(where, 'file')} {ui.paint(f'({path.relative_to(self.env.repo)})', 'dim')}")
        return Change(arg, target, path, at, line, Path(base[target]), repo_file)

    def _release(self, arg: str, target: str, manifest: dict) -> Optional[Change]:
        base, links = manifest["base"], manifest["links"]
        where = f"~/{target}"
        if target not in base or links.get(target, base[target]) == base[target]:
            ui.warn(f"{arg}: {where} is not a base file that you own." if target in base else
                    f"{arg}: {where} is not a config file of the dotkit base.")
            return None
        found = _link_lines(self.env, target)
        if len(found) != 1:
            ui.warn(f"{arg}: cannot find the one line in nix/ that links {where}. Remove it by hand, then run "
                    "dotkit apply.")
            return None
        path, at = found[0]
        mine = links.get(target)
        keep = f"; {_yours(self.env, mine)} stays in the repository" if mine and mine.startswith(f"{self.env.repo}/") \
            else ""
        ui.out(f"{ui.paint(arg, 'name')}: remove the link of {ui.paint(where, 'file')} from "
               f"{ui.paint(str(path.relative_to(self.env.repo)), 'removed')}, so the base file comes back{keep}")
        return Change(arg, target, path, at)

    def _write(self, changes: List[Change]) -> None:
        by_file: Dict[Path, List[Change]] = {}
        for c in changes:
            by_file.setdefault(c.nix_file, []).append(c)
        for path, cs in by_file.items():
            lines = _read(path)
            # From the bottom, so that the line indexes stay right; at the same line, the last argument first, so
            # that the new lines keep the order of the arguments.
            for c in sorted(cs, key=lambda c: (c.at, changes.index(c)), reverse=True):
                if c.line:
                    lines.insert(c.at + 1, c.line)
                else:
                    del lines[c.at]
            path.write_text("\n".join(lines), encoding="utf-8")
        for c in changes:
            if c.copy_from is not None:
                dest = self.env.repo / c.repo_file
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(_copy_text(c.copy_from.read_bytes()))
                dest.chmod(0o644)


def _copy_text(data: bytes) -> bytes:
    """The base file for the repository: its first comment block, about the base file, becomes HEADER."""
    lines = data.split(b"\n")
    if not lines[0].startswith(b"# dotkit base"):
        return data
    n = next((i for i, line in enumerate(lines) if not line.startswith(b"#")), len(lines))
    return HEADER.encode() + b"\n".join(lines[n:])


def own(env: Env, targets: Sequence[str], release: bool, dry_run: bool, yes: bool) -> int:
    return Own(env, dry_run, yes).run(targets, release)
