"""The commands add, remove, info, and list (BEHAVIOR.md, rows L, I, Ad, and Rm). dotkit/cli.py parses them."""

from __future__ import annotations

import os
import re
from typing import List, Optional, Sequence, Set

from ..core import change, lists, mise_toml, nix, proc, ui
from ..core.env import Env
from . import declared, picker
from .entries import KINDS, NIX_FILES, Entry, kinds_of
from .lookups import Lookups
from .resolve import KEYS, Candidates, Outcome, Resolver, Selection

# The reason of a source that the user selected in the picker, when it is not the one of the rules.
PICKED = "selected in the list"


def print_candidates(c: Candidates, selected: Optional[Selection]) -> None:
    """One line per candidate; the selected one has a check mark (BEHAVIOR.md, Pc1)."""
    columns = ui.terminal_columns()
    width = None if columns is None else max(columns - 50, 20)
    for key in KEYS:
        found = c.found.get(key)
        if found is None:
            continue
        chosen = selected is not None and key == selected.key
        mark = ui.paint("✓", "ok") if chosen else " "
        desc = found.description if width is None else ui.cut(found.description, width)
        spec = ui.paint(ui.pad(c.spec_of(key), 30), "name" if chosen else "dim")
        ui.out(f"  {mark} {spec} {ui.paint(ui.pad(found.version or '?', 14), 'dim')} {ui.paint(desc, 'dim')}")


def _arrow(spec: str, file: str, role: str = "added") -> str:
    """SPEC -> FILE of a plan line, in colors on a terminal."""
    return f"{ui.paint(spec, role)} {ui.paint('->', 'dim')} {ui.paint(file, 'file')}"


def _tool(tool: str) -> str:
    return ui.paint(tool, "name") + ":"


def _files(kinds) -> str:
    """The files of the kinds, in the order of KINDS: "nix/brews.txt or nix/casks.txt"."""
    files = [k.file for k in KINDS.values() if k.name in kinds]
    return files[0] if len(files) == 1 else ", ".join(files[:-1]) + ("," if len(files) > 2 else "") + " or " + files[-1]


class Pkg:
    def __init__(self, env: Env, dry_run: bool = False, yes: bool = False, force: bool = False):
        self.env = env
        self.dry_run, self.yes, self.force = dry_run, yes, force
        self.look = Lookups(env)
        self.resolver = Resolver(self.look)

    def _lists(self) -> list:
        return [self.env.repo / f for f in NIX_FILES]

    # --- add

    def add(self, tools: Sequence[str]) -> int:
        seen: List[str] = []
        commands: List[str] = []
        nix_edits: List[Entry] = []
        mise_entries: List[Entry] = []
        failed = False
        for tool in tools:
            if tool in seen:
                continue
            seen.append(tool)
            found = declared.find(self.env, tool)
            if found:
                ui.out(f"{_tool(tool)} already in {ui.paint(found[0].label, 'file')}")
                continue
            outcome = self.resolver.resolve(tool)
            if self._can_ask(outcome):
                outcome = self._pick(tool, outcome)
            if not outcome.ok:
                ui.warn(f"{tool}: {outcome.reason}")
                print_candidates(outcome.candidates, None)
                failed = True
                continue
            s = outcome.selection
            if s.entry in nix_edits + mise_entries:
                ui.out(f"{_tool(tool)} same entry as an earlier argument ({s.spec})")
                continue
            # An App Store app has no command to check.
            if s.entry.kind == "mas":
                planned = self._plan_mas(tool, s, nix_edits)
                if planned is None:
                    failed = True
                else:
                    nix_edits.append(planned)
                continue
            # A tool can have another name in the config: rg is ripgrep, and tealdeer supplies tldr.
            if not self.force:
                path = declared.existing_command(self.env, s.command)
                if path is not None:
                    ui.warn(f"{tool}: '{s.command}' is already installed ({declared.origin(path, self.env.home)})."
                            f" See dotkit info {s.command}, or use --force to add it anyway.")
                    failed = True
                    continue
            if s.command in commands:
                ui.warn(f"{tool}: an earlier argument also supplies the command '{s.command}'. "
                        "Add only one of them.")
                failed = True
                continue
            commands.append(s.command)
            if tool == s.spec:
                ui.out(_arrow(s.spec, s.entry.file))
            else:
                ui.out(f"{_tool(tool)} {_arrow(s.spec, s.entry.file)} {ui.paint(f'({s.reason})', 'dim')}")
            print_candidates(outcome.candidates, s)
            (nix_edits if s.entry.nix else mise_entries).append(s.entry)
        if failed:
            raise ui.die(ui.FAILED)
        planned = len(nix_edits) + len(mise_entries)
        if planned == 0:
            return 0
        if self.dry_run:
            ui.hint("No change. Run the command without -n to add.")
            return 0
        what = "tool" if planned == 1 else f"{planned} tools"
        ui.confirm_or_stop(f"Add the {what}?", self.yes)
        # Nix first: a failed build restores the Nix files, and mise is not changed yet.
        if nix_edits:
            def edit_lists() -> None:
                for e in nix_edits:
                    lists.append(self.env.repo / e.file, e.text)
            change.apply_nix(self.env, edit_lists, self._lists())
        # `mise use` writes "latest" when the spec has no version.
        for e in mise_entries:
            if not proc.mise(self.env.home, ["use", "-g", e.text]).ok:
                raise ui.die(f"mise could not add {e.text}. The tools before it in the list are added.")
        ui.success(f"Added {what}." if planned > 1 else f"Added {(nix_edits + mise_entries)[0].text}.")
        return 0

    def _can_ask(self, outcome: Outcome) -> bool:
        """The picker asks for a bare name in a terminal, without -y and -n, when two sources or more have
        the tool, or when the rules select none of them (BEHAVIOR.md, Pk1)."""
        c = outcome.candidates
        if not outcome.bare or c.nix_failed or not c.found or self.yes or self.dry_run:
            return False
        if not (os.isatty(0) and os.isatty(2)):
            return False
        return len(c.found) > 1 or not outcome.ok

    def _pick(self, tool: str, outcome: Outcome) -> Outcome:
        """The outcome with the source that the user selects. The source of the rules is the first row; when
        the rules select none, the rows have the order of KEYS. Esc stops add with "nothing changed."."""
        c = outcome.candidates
        keys = [k for k in KEYS if k in c.found]
        if outcome.ok:
            keys.remove(outcome.selection.key)
            keys.insert(0, outcome.selection.key)
        rows = [picker.Row(c.spec_of(k).split(":", 1)[0], c.found[k].version, c.found[k].description,
                           picker.REGISTRY_NOTE if k in ("npm", "pypi") else "") for k in keys]
        index = picker.ask(tool, rows)
        if index is None:
            raise ui.die("nothing changed.")
        key = keys[index]
        if outcome.ok and key == outcome.selection.key:
            return outcome
        return Outcome(c, self.resolver.select(c, key, PICKED), bare=True)

    def _plan_mas(self, tool: str, s: Selection, nix_edits: List[Entry]) -> Optional[Entry]:
        """The entry of an App Store app, or None after the warning (BEHAVIOR.md, Ad5)."""
        if not (self.env.repo / KINDS["mas"].file).is_file():
            ui.warn(f"{tool}: nix/mas.txt does not exist. Create it, and list it in dotkit.lists.masApps in "
                    "nix/darwin.nix.")
            return None
        # homebrew.masApps lists apps by name, and the name is only a label. A name that another app has
        # gets the label "Name (ID)".
        app_id, label = s.entry.text.split(" ", 1)[0], s.reason
        entry = s.entry
        planned = [tuple(e.text.split(" ", 1)) for e in nix_edits if e.kind == "mas"]
        mas_txt = self.env.repo / KINDS["mas"].file
        if lists.mas_id_of(label, mas_txt, planned) is not None:
            label = f"{s.reason} ({app_id})"
            if lists.mas_id_of(label, mas_txt, planned) is not None:
                ui.warn(f"{tool}: other apps have the names '{s.reason}' and '{label}'. Add it to nix/mas.txt "
                        f"under another name: {app_id} <name>")
                return None
            entry = Entry("mas", f"{app_id} {label}")
        ui.out(f"{_arrow(s.spec, 'nix/mas.txt')} {ui.paint(f'({label})', 'dim')}")
        if label != s.reason:
            ui.out(f"nix/mas.txt has another app named {s.reason}; added as '{label}'.")
        return entry

    # --- remove

    def remove(self, tools: Sequence[str]) -> int:
        nix_edits: List[Entry] = []
        mise_entries: List[Entry] = []
        in_base: Set[Entry] = set()
        failed = False
        for tool in tools:
            # A prefix selects the kinds: nix:jq is only in nix/packages.txt.
            kinds = kinds_of(tool)
            found = [d for d in declared.find(self.env, tool) if d.entry.kind in kinds]
            base = [d for d in found if d.base]
            found = [d for d in found if not d.base]
            if base and not found:
                ui.warn(f"{tool}: in the dotkit base ({base[0].label}). Only dotkit can remove it.")
                failed = True
                continue
            if not found:
                ui.warn(f"{tool}: not in {_files(kinds)}. See dotkit info {tool}.")
                failed = True
                continue
            if len(found) > 1:
                ui.warn(f"{tool}: in more than one place: {','.join(d.label for d in found)}. Use a prefix (nix:, "
                        "brew:, cask:, mise:), or remove it by hand.")
                failed = True
                continue
            entry = found[0].entry
            if entry in nix_edits + mise_entries:
                continue
            # The base has the same entry: remove the copy of the repository, and keep the tool installed.
            kept = [d for d in base if d.entry == entry]
            if kept:
                in_base.add(entry)
                ui.out(f"{_tool(tool)} remove {ui.paint(entry.text, 'removed')} from {ui.paint(entry.file, 'file')}; "
                       f"the dotkit base keeps it ({kept[0].label})")
            else:
                ui.out(f"{_tool(tool)} remove {ui.paint(entry.text, 'removed')} from {ui.paint(entry.file, 'file')}")
            (nix_edits if entry.nix else mise_entries).append(entry)
        if failed:
            raise ui.die(ui.FAILED)
        if self.dry_run:
            ui.hint("No change. Run the command without -n to remove.")
            return 0
        ui.confirm_or_stop("Remove?", self.yes)
        if nix_edits:
            def edit_lists() -> None:
                for e in nix_edits:
                    (lists.delete_mas if e.kind == "mas" else lists.delete)(self.env.repo / e.file, e.text)
            change.apply_nix(self.env, edit_lists, self._lists())
            # Activation does not uninstall Homebrew packages (homebrew.onActivation.cleanup in
            # nix/darwin.nix), so uninstall the removed ones here. With cleanup = "uninstall", activation did
            # it already.
            for e in nix_edits:
                if e in in_base:
                    continue
                if e.kind in ("formula", "cask"):
                    self._brew_uninstall(e.kind, e.text)
                elif e.kind == "mas":
                    ui.warn(f"the App Store app {e.text} stays installed. dotkit does not uninstall App Store apps.")
        for e in mise_entries:
            # The base still uses the versions of a key that it has: do not prune them.
            args = ["unuse", "-g", "--no-prune", e.text] if e in in_base else ["unuse", "-g", e.text]
            if not proc.mise(self.env.home, args).ok:
                raise ui.die(f"mise could not remove {e.text}. The tools before it in the list are removed.")
        removed = nix_edits + mise_entries
        ui.success(f"Removed {removed[0].text}." if len(removed) == 1 else f"Removed {len(removed)} tools.")
        return 0

    def _brew_uninstall(self, kind: str, name: str) -> None:
        """Uninstalls a Homebrew formula or cask when it is installed."""
        if proc.which("brew") is None:
            return
        quiet = {"HOMEBREW_NO_AUTO_UPDATE": "1"}
        if not proc.run(["brew", "list", f"--{kind}", name], env=quiet, stdout=proc.DEVNULL,
                        stderr=proc.DEVNULL).ok:
            return
        if not proc.run(["brew", "uninstall", f"--{kind}", name], env=quiet).ok:
            ui.warn(f"could not uninstall the {kind} {name}. Run: brew uninstall --{kind} {name}")

    # --- info

    def info(self, tools: Sequence[str]) -> int:
        status = 0
        for tool in tools:
            found_any = False
            ui.out(ui.paint(tool, "heading"))
            found = declared.find(self.env, tool)
            if found:
                found_any = True
                for d in found:
                    ui.out(f"  {ui.paint(ui.pad('declared', 9), 'dim')} {ui.paint(d.label, 'file')} ({d.entry.text})")
            else:
                ui.out(f"  {ui.paint(ui.pad('declared', 9), 'dim')} {ui.paint('no', 'changed')}")
            outcome = self.resolver.resolve(tool)
            if outcome.ok:
                ui.out(f"  {ui.paint('add picks', 'dim')} {ui.paint(outcome.selection.spec, 'added')} "
                       f"({outcome.selection.reason})")
            else:
                ui.out(f"  {ui.paint('add picks', 'dim')} {ui.paint('none', 'changed')}: {outcome.reason}")
            command = (outcome.selection.command if outcome.ok else "") or mise_toml.short(tool)
            path = declared.existing_command(self.env, command)
            if path is not None:
                found_any = True
                ui.out(f"  {ui.paint(ui.pad('command', 9), 'dim')} {ui.paint(path, 'file')} "
                       f"({declared.origin(path, self.env.home)})")
            if outcome.candidates.found:
                found_any = True
                ui.out("  " + ui.paint("sources", "dim"))
                print_candidates(outcome.candidates, outcome.selection)
            if not found_any:
                status = 1
        return status

    # --- list

    def list(self, sources: Sequence[str]) -> int:
        for src in sources or ("nix", "brew", "mise"):
            # The dotkit base first, marked "(dotkit)".
            for root, base in ((self.env.dotkit, ui.paint(" (dotkit)", "dim")), (self.env.repo, "")):
                if src == "nix":
                    for n in lists.names(root / "nix/packages.txt"):
                        ui.out(f"{ui.paint('nix', 'nix')}   {n}{base}")
                elif src == "brew":
                    for n in lists.names(root / "nix/brews.txt"):
                        ui.out(f"{ui.paint('brew', 'brew')}  {ui.pad(n, 36)} {ui.paint('formula', 'dim')}{base}")
                    for n in lists.names(root / "nix/casks.txt"):
                        ui.out(f"{ui.paint('brew', 'brew')}  {ui.pad(n, 36)} {ui.paint('cask', 'dim')}{base}")
                else:
                    for key, value in mise_toml.tools(root / "config/mise.toml").items():
                        ui.out(f"{ui.paint('mise', 'mise')}  {ui.pad(key, 36)} "
                               f"{ui.paint(mise_toml.version_text(value), 'dim')}{base}")
            if src == "brew":
                self._list_app_store()
        return 0

    def _list_app_store(self) -> None:
        r = nix.Nix(self.env).flake(["eval", "--json", f"{self.env.flake}#darwinConfigurations.mac.config."
                                     "homebrew.masApps"], stdout=proc.CAPTURE)
        if not r.ok:
            raise ui.Exit(r.code)
        apps = r.json_object("nix eval of homebrew.masApps")
        for name in sorted(apps):
            # The names in nix/mas.txt are escaped for the Brewfile: \\, \", and \#.
            name = re.sub(r'\\([\\"#])', r"\1", name)
            ui.out(f"{ui.paint('brew', 'brew')}  {ui.pad(name, 36)} {ui.paint('app store', 'dim')}")
