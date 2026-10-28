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

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Union

from ..core import nix_links, paths, ui
from ..core.env import Env
from ..core.manifest import Manifest
from ..core.nix_links import nix_string
from ..migrate.base import OWN_FILES
from ..pkg import edit
from . import overlap

FAILED = "nothing changed. Fix the errors above, then run the command again."
SKIP = "skip"  # nothing to do for an argument, and not an error
# The header of a copy, instead of the first comment block of a base file that starts with "# dotkit base".
HEADER = ("# From the dotkit base (dotkit own). After a dotkit upgrade, dotkit doctor shows\n"
          "# how this file differs from the base file.\n")


@dataclass
class Change:
    arg: str
    target: str
    edit: nix_links.Edit
    # own: the base file to copy, and its place in the repository.
    copy_from: Optional[Path] = None
    repo_file: str = ""


def resolve(arg: str, manifest: Manifest, home: Path) -> List[str]:
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

    return [t for t in sorted(manifest.base) if not manifest.is_script(t) and arg in names(t)]


def _yours(env: Env, source: str) -> str:
    return paths.display(source, env.repo, env.dotkit, env.home)


class Own:
    def __init__(self, env: Env, dry_run: bool = False, yes: bool = False):
        self.env, self.dry_run, self.yes = env, dry_run, yes

    def run(self, args: Sequence[str], release: bool) -> int:
        manifest = Manifest.evaluate(self.env)
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
        nix_files = sorted({c.edit.path for c in changes})
        created = [self.env.repo / c.repo_file for c in changes if c.copy_from is not None]
        edit.apply_nix(self.env, lambda: self._write(changes), paths=nix_files, created=created)
        if not release:
            # The new link replaces a base file on purpose: the next report does not show it as new.
            overlap.acknowledge(self.env, self.env.result, [f"link:{c.target}" for c in changes])
        if release:
            ui.success("Released " + ", ".join(f"~/{c.target}" for c in changes) + ".")
        else:
            ui.success("Owned " + ", ".join(c.repo_file for c in changes) + ".")
            ui.hint("Commit " + ", ".join(c.repo_file for c in changes) + ". After a dotkit upgrade, dotkit doctor "
                    "shows how yours differs from the base file.")
        return 0

    def _own(self, arg: str, target: str, manifest: Manifest, planned: List[Change]) -> Union[Change, str, None]:
        base, links, layers = manifest.base, manifest.links, manifest.layers
        where = f"~/{target}"
        if target not in base:
            if links.get(target) is not None:
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
        if manifest.is_script(target):
            ui.warn(f"{arg}: {where} is a script of the dotkit base, and cannot be owned.")
            return None
        if target in links and links[target] is None:
            ui.warn(f"{arg}: {where} has no link: your configuration sets it to null. Remove that line, or run "
                    f"dotkit own --release {where} first.")
            return None
        if manifest.replaced(target):
            ui.out(f"{ui.paint(arg, 'name')}: {where} is yours already ({_yours(self.env, links.get(target))}). dotkit doctor shows "
                   "how it differs from the base file.")
            return SKIP
        repo_file = "config/" + base[target].rsplit("/", 1)[-1]
        if (self.env.repo / repo_file).exists() or any(c.repo_file == repo_file for c in planned):
            ui.warn(f"{arg}: {repo_file} exists already. Move it, or link it yourself in dotkit.links.")
            return None
        e = nix_links.find_insert(self.env.repo, target, repo_file)
        if e is None:
            ui.warn(f"{arg}: cannot find where nix/ sets dotkit.links. Add this line to nix/home.nix, copy the base "
                    f"file to {repo_file}, and run dotkit apply: dotkit.links.{nix_string(target)} = "
                    f"{nix_string(repo_file)};")
            return None
        ui.out(f"{ui.paint(arg, 'name')}: copy the base file to {ui.paint(repo_file, 'added')}, and link it as "
               f"{ui.paint(where, 'file')} {ui.paint(f'({e.path.relative_to(self.env.repo)})', 'dim')}")
        return Change(arg, target, e, Path(base[target]), repo_file)

    def _release(self, arg: str, target: str, manifest: Manifest) -> Optional[Change]:
        where = f"~/{target}"
        if not manifest.replaced(target):
            ui.warn(f"{arg}: {where} is not a base file that you own." if target in manifest.base else
                    f"{arg}: {where} is not a config file of the dotkit base.")
            return None
        found = nix_links.find_rows(self.env.repo, target)
        if len(found) != 1:
            ui.warn(f"{arg}: cannot find the one line in nix/ that links {where}. Remove it by hand, then run "
                    "dotkit apply.")
            return None
        path = found[0].path
        mine = manifest.links.get(target)
        keep = f"; {_yours(self.env, mine)} stays in the repository" if mine and mine.startswith(f"{self.env.repo}/") \
            else ""
        ui.out(f"{ui.paint(arg, 'name')}: remove the link of {ui.paint(where, 'file')} from "
               f"{ui.paint(str(path.relative_to(self.env.repo)), 'removed')}, so the base file comes back{keep}")
        return Change(arg, target, found[0])

    def _write(self, changes: List[Change]) -> None:
        try:
            nix_links.apply([c.edit for c in changes])
        except OSError as e:
            ui.warn(str(e))
            raise
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
