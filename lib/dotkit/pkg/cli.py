"""The commands add, remove, info, and list (PARITY.md, rows L, I, Ad, and Rm). dotkit/cli.py parses them."""

from __future__ import annotations

import os
import re
from typing import List, Optional, Sequence

from ..core import lists, mise_toml, nix, proc, ui
from ..core.env import Env
from . import declared, edit, picker
from .entries import KINDS, Entry, kinds_of
from .lookups import Lookups
from .resolve import KEYS, Candidates, Outcome, Resolver, Selection

FAILED = "nothing changed. Fix the errors above, then run the command again."
# The reason of a source that the user selected in the picker, when it is not the one of the rules.
PICKED = "selected in the list"


def print_candidates(c: Candidates, selected: Optional[Selection]) -> None:
    """One line per candidate; the selected one has a check mark (PARITY.md, Pc1)."""
    columns = ui.terminal_columns()
    width = None if columns is None else max(columns - 50, 20)
    for key in KEYS:
        found = c.found.get(key)
        if found is None:
            continue
        mark = "✓" if selected is not None and key == selected.key else " "
        desc = found.description if width is None else ui.cut(found.description, width)
        ui.out(f"  {mark} {ui.pad(c.spec_of(key), 30)} {ui.pad(found.version or '?', 14)} {desc}")


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
                ui.out(f"{tool}: already in {found[0].label}")
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
                ui.out(f"{tool}: same entry as an earlier argument ({s.spec})")
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
                ui.out(f"{s.spec} -> {s.entry.file}")
            else:
                ui.out(f"{tool}: {s.spec} -> {s.entry.file} ({s.reason})")
            print_candidates(outcome.candidates, s)
            (nix_edits if s.entry.nix else mise_entries).append(s.entry)
        if failed:
            raise ui.die(FAILED)
        planned = len(nix_edits) + len(mise_entries)
        if planned == 0:
            return 0
        if self.dry_run:
            ui.note("No change. Run the command without -n to add.")
            return 0
        what = "tool" if planned == 1 else f"{planned} tools"
        if not ui.confirm(f"Add the {what}?", self.yes):
            raise ui.die("nothing changed.")
        # Nix first: a failed build restores the Nix files, and mise is not changed yet.
        if nix_edits:
            def change() -> None:
                for e in nix_edits:
                    lists.append(self.env.repo / e.file, e.text)
            edit.apply_nix(self.env, change)
        # `mise use` writes "latest" when the spec has no version.
        for e in mise_entries:
            if not proc.mise(self.env.home, ["use", "-g", e.text]).ok:
                raise ui.die(f"mise could not add {e.text}. The tools before it in the list are added.")
        return 0

    def _can_ask(self, outcome: Outcome) -> bool:
        """The picker asks for a bare name in a terminal, without -y and -n, when two sources or more have
        the tool, or when the rules select none of them (PARITY.md, Pk1)."""
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
        """The entry of an App Store app, or None after the warning (PARITY.md, Ad5)."""
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
        ui.out(f"{s.spec} -> nix/mas.txt ({label})")
        if label != s.reason:
            ui.out(f"nix/mas.txt has another app named {s.reason}; added as '{label}'.")
        return entry

    # --- remove

    def remove(self, tools: Sequence[str]) -> int:
        nix_edits: List[Entry] = []
        mise_entries: List[Entry] = []
        failed = False
        for tool in tools:
            # A prefix selects the kinds: nix:jq is only in nix/packages.txt.
            kinds = kinds_of(tool)
            found = [d for d in declared.find(self.env, tool) if d.entry.kind in kinds]
            base = [d for d in found if d.base]
            if base:
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
            ui.out(f"{tool}: remove {entry.text} from {entry.file}")
            (nix_edits if entry.nix else mise_entries).append(entry)
        if failed:
            raise ui.die(FAILED)
        if self.dry_run:
            ui.note("No change. Run the command without -n to remove.")
            return 0
        if not ui.confirm("Remove?", self.yes):
            raise ui.die("nothing changed.")
        if nix_edits:
            def change() -> None:
                for e in nix_edits:
                    (lists.delete_mas if e.kind == "mas" else lists.delete)(self.env.repo / e.file, e.text)
            edit.apply_nix(self.env, change)
            # Activation does not uninstall Homebrew packages (homebrew.onActivation.cleanup in
            # nix/darwin.nix), so uninstall the removed ones here. With cleanup = "uninstall", activation did
            # it already.
            for e in nix_edits:
                if e.kind in ("formula", "cask"):
                    self._brew_uninstall(e.kind, e.text)
                elif e.kind == "mas":
                    ui.warn(f"the App Store app {e.text} stays installed. dotkit does not uninstall App Store apps.")
        for e in mise_entries:
            if not proc.mise(self.env.home, ["unuse", "-g", e.text]).ok:
                raise ui.die(f"mise could not remove {e.text}. The tools before it in the list are removed.")
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
            ui.out(tool)
            found = declared.find(self.env, tool)
            if found:
                found_any = True
                for d in found:
                    ui.out(f"  {ui.pad('declared', 9)} {d.label} ({d.entry.text})")
            else:
                ui.out(f"  {ui.pad('declared', 9)} no")
            outcome = self.resolver.resolve(tool)
            if outcome.ok:
                ui.out(f"  add picks {outcome.selection.spec} ({outcome.selection.reason})")
            else:
                ui.out(f"  add picks none: {outcome.reason}")
            command = (outcome.selection.command if outcome.ok else "") or mise_toml.short(tool)
            path = declared.existing_command(self.env, command)
            if path is not None:
                found_any = True
                ui.out(f"  {ui.pad('command', 9)} {path} ({declared.origin(path, self.env.home)})")
            if outcome.candidates.found:
                found_any = True
                ui.out("  sources")
                print_candidates(outcome.candidates, outcome.selection)
            if not found_any:
                status = 1
        return status

    # --- list

    def list(self, sources: Sequence[str]) -> int:
        for src in sources or ("nix", "brew", "mise"):
            # The dotkit base first, marked "(dotkit)".
            for root, base in ((self.env.dotkit, " (dotkit)"), (self.env.repo, "")):
                if src == "nix":
                    for n in lists.names(root / "nix/packages.txt"):
                        ui.out(f"nix   {n}{base}")
                elif src == "brew":
                    for n in lists.names(root / "nix/brews.txt"):
                        ui.out(f"brew  {ui.pad(n, 36)} formula{base}")
                    for n in lists.names(root / "nix/casks.txt"):
                        ui.out(f"brew  {ui.pad(n, 36)} cask{base}")
                else:
                    for key, value in mise_toml.tools(root / "config/mise.toml").items():
                        ui.out(f"mise  {ui.pad(key, 36)} {mise_toml.version_text(value)}{base}")
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
            ui.out(f"brew  {ui.pad(name, 36)} app store")
