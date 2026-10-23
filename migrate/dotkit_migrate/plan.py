"""Turn a scan and the choices into a plan: the files of the new repository and the report.

Safe defaults: Homebrew packages stay in Homebrew and go to the lists, tools
of other package managers go to mise, safe shell lines are carried, and
config files stay yours. Nothing is installed or uninstalled. Secrets never
go into a file that Git tracks.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Callable
from pathlib import Path

from . import base as base_mod
from . import catalog, rcparse, secrets
from . import docs as docs_mod
from .docs import mas_label, mas_problem, nix_string, tap_name_problem, tap_problem
from .model import Choices, Facts, Item, Page
from .report import plural
from . import scan as scan_mod
from .context import tilde
from .scan import Scan, ShellFile

# --- The choices

OPTION_TEXT = {
    "layer": "yours, read after the dotkit file",
    "replace": "yours, instead of the dotkit file",
    "dotkit": "the dotkit file; yours is renamed to <name>.before-dotkit",
    "keep": "leave yours in place, outside the repository",
    "adopt": "yours, in the repository",
}

# The intros of "Not carried, by your choice": items that were on by default and that you deselected.
# Homebrew formulae and casks get the first one.
NOT_CARRIED_BREW = ("You left these out of the lists. They stay on this Mac, and dotkit does not manage them. "
                    "With `homebrew.onActivation.cleanup = \"uninstall\"` in nix/darwin.nix, activation would "
                    "uninstall them.")
NOT_CARRIED = "You left these out. They stay where they are, and dotkit does not manage them."


def _file_item(target: str, options: list[str], default: str, note: str) -> Item:
    return Item(f"file:{target}", f"~/{target}", note, options=tuple(options), default_option=default,
                option_text={o: OPTION_TEXT[o] for o in options})


class Planner:
    def __init__(self, scan: Scan, base: base_mod.Base | None = None,
                 check_syntax: Callable[[str, str], bool] | None = None):
        self.scan = scan
        self.base = base or base_mod.Base()
        # check_syntax(shell, text): True when the text parses. None: no check.
        self.check_syntax = check_syntax
        # The parsed shell files, by the set of the deselected ones (see rc_results).
        self._rc: dict[frozenset[str], dict[str, rcparse.Result]] = {}

    # --- What the wizard shows

    def brew_split(self, names: list[str], kind: str) -> tuple[list[str], dict[str, str]]:
        keep, redundant = [], {}
        for n in names:
            p = self.base.provides_brew(n, kind)
            if p:
                redundant[n] = p
            else:
                keep.append(n)
        return keep, redundant

    def runtime_lines(self) -> dict[str, list[str]]:
        """mise tool -> versions, default first, for the version managers mise can replace."""
        out: dict[str, list[str]] = {}
        for vm in self.scan.version_managers:
            if not catalog.version_manager(vm.name).mise_replaces:
                continue
            for tool, versions in vm.versions.items():
                vs = [v.lstrip("v") for v in versions if re.fullmatch(r"v?\d+(\.\d+)*", v)]
                d = vm.default.get(tool, "").lstrip("v")
                if d and re.fullmatch(r"\d+(\.\d+)*", d):
                    # The default can be a prefix, as nvm's 20: it covers 20.11.0.
                    vs = [d] + [v for v in vs if v != d and not v.startswith(d + ".")]
                elif vs:
                    vs = vs[::-1]
                for v in vs:
                    out.setdefault(tool, [])
                    if v not in out[tool]:
                        out[tool].append(v)
        return out

    def rc_results(self, skipped: frozenset[str] = frozenset()) -> dict[str, rcparse.Result]:
        """The parsed shell files, by name. Layer files that were not read before are not parsed.

        The variables that the carried lines set pass on to the next file. A
        file in `skipped` (deselected) is parsed too, for its findings and the
        names of its secret lines, but it passes on nothing: the next file starts
        with the variables known before it. The pages use the full chain.
        """
        if skipped not in self._rc:
            out: dict[str, rcparse.Result] = {}
            files = {f.name: f for f in self.scan.shell_files if not f.managed}
            for shell, names in (("zsh", (".zshenv", ".zshenv.local", ".zprofile", ".zprofile.local",
                                          ".zshrc", ".zshrc.local")),
                                 ("bash", (".bash_profile", ".bash_profile.local"))):
                parser = rcparse.Parser(self.scan.home, shell)
                known: dict[str, str] = {}
                for name in names:
                    f = files.get(name)
                    if f is None or not f.read:
                        continue
                    r = parser.parse(name, f.text, known)
                    if name not in skipped:
                        known = r.variables
                    out[name] = r
            self._rc[skipped] = out
        return self._rc[skipped]

    def pages(self) -> list[Page]:
        """The questions, one page per source, in the order of the plan. Pages without items are left out."""
        s = self.scan
        out: list[Page] = []

        def checklist(key: str, title: str, dest: str, intro: str, items: list[Item], note: str = "") -> None:
            unique: dict[str, Item] = {}
            for i in items:  # a package in two places is one item
                unique.setdefault(i.id, i)
            out.append(Page(key, title, dest, intro, "checklist", list(unique.values()), note))

        formulae, _ = self.brew_split(s.formulae, "formula")
        casks, _ = self.brew_split(s.casks, "cask")
        checklist("formulae", "Homebrew formulae", "nix/brews.txt",
                  "The formulae that you installed with Homebrew. The selected ones go to the list; all of them "
                  "stay installed. MIGRATION.md lists the ones that the dotkit base has already.",
                  [Item(f"brew:{n}", n, f"`dotkit add brew:{n}` adds it later.") for n in formulae])
        checklist("casks", "Homebrew casks", "nix/casks.txt",
                  "The apps that you installed with Homebrew. The selected ones go to the list; all of them stay "
                  "installed. MIGRATION.md lists the ones that the dotkit base has already.",
                  [Item(f"cask:{n}", n, f"`dotkit add cask:{n}` adds it later.") for n in casks])
        lines, _ = tap_lines(s.taps, set())
        checklist("taps", "Homebrew taps", "nix/taps.txt",
                  "The taps of Homebrew. The selected ones go to the list, so that a new Mac has them too.",
                  [Item(f"tap:{line.split()[0]}", line.split()[0],
                        f"From {line.split()[1]}" if " " in line else "") for line in lines])
        checklist("mas", "App Store apps", "nix/mas.txt",
                  "The apps that mas lists. The selected ones go to the list, and a new Mac installs them from "
                  "the App Store.",
                  [Item(f"mas:{i}", n.strip(), f"ID {i}") for i, n in s.mas_apps if mas_problem(i, n.strip()) is None])
        for key, title, pkgs, intro in (
            ("npm", "npm, pnpm, and bun tools", [p for p in s.npm if p.name not in catalog.NPM_NOT_IN_MISE],
             "Global tools of npm, pnpm, and bun. The selected ones move to mise; the old copies stay until you "
             "remove them."),
            ("pipx", "pipx and uv tools", s.pipx,
             "Tools of pipx and uv tool. The selected ones move to mise; the old copies stay until you remove "
             "them."),
            ("cargo", "cargo install tools", s.cargo,
             "Tools of cargo install. The selected ones move to mise; the old copies stay until you remove them."),
            ("go", "Go tools", s.go,
             "The tools in your Go bin directory. The selected ones move to mise; the old copies stay until you "
             "remove them."),
        ):
            off = key in catalog.BUILT_FROM_SOURCE
            checklist(key, title, "config/mise.toml", intro,
                      [Item(f"{key}:{p.name}", p.name, f"{p.version or 'installed'} in {p.where}", default=not off)
                       for p in pkgs if p.ok],
                      f"off: mise builds them from source with {key}" if off else "")
        managers: dict[str, list[str]] = {}
        for vm in s.version_managers:
            for tool in vm.versions:
                managers.setdefault(tool, []).append(vm.name)
        checklist("runtimes", "Runtime versions of version managers", "config/mise.toml",
                  "The versions that your version managers have. The selected ones go to config/mise.toml, and "
                  "mise installs them.",
                  [Item(f"runtime:{t}", f"{t} {', '.join(v)}", "From " + ", ".join(managers.get(t, [])),
                        default=False) for t, v in self.runtime_lines().items()],
                  "off: mise installs them; the base has Node, Python, and Ruby")
        shell_items = []
        for name, r in self.rc_results().items():
            if not (r.statements or r.secrets):
                continue
            main = name.removesuffix(".local")
            layer, repo_file = base_mod.SHELL_LAYERS[main]
            parts = [plural(r.statements, "line")] if r.statements else []
            detail = f"To {repo_file} (~/{layer})."
            if r.secrets:
                parts.append(plural(len(r.secrets), "line") + " with a secret")
                detail += " The lines with a secret go to local/, which Git ignores."
            label = f"~/{name}: " + " and ".join(parts)
            shell_items.append(Item(f"shell:{name}", label, detail))
        checklist("shell", "Shell lines", "config/zsh, config/bash",
                  "The lines of your shell files that dotkit can carry, by file. MIGRATION.md lists the lines "
                  "that it cannot carry.", shell_items)
        out.append(Page("files", "Your config files", "config/", "dotkit has its own file for each of these. "
                        "Choose which one each tool reads.", "choices", self._file_items()))
        other = []
        for o in s.other_config:
            if o.adoptable:
                what = (f"a directory with {plural(o.files, 'file')}") if o.is_dir else "a file"
                other.append(Item(f"other:{o.rel}", f"~/{o.rel}",
                                  f"~/{o.rel}: {what}. It goes to config/ and is linked back.", default=False))
        checklist("other", "Other config to keep in the repository", "config/",
                  "These are not part of dotkit. By default they stay in place and keep working. Select the ones "
                  "to copy into the repository.", other)
        return [p for p in out if p.items]

    def _file_items(self) -> list[Item]:
        s = self.scan
        items: list[Item] = []
        for c in s.config_files:
            link = next(link for link in base_mod.LINKS if link.target == c.target)
            options = []
            if link.layer and not (Path(s.home, link.layer).exists() and link.layer != ".gitconfig"):
                options.append("layer")
            if c.target != ".config/git/config":
                options.append("replace")
            options += ["dotkit", "keep"]
            note = ""
            if c.secrets:
                # A file with a secret never goes into the repository.
                options, default = ["keep", "dotkit"], "keep"
                note = "has secrets: " + ", ".join(sorted({x.what for x in c.secrets})) + \
                    ". It stays out of the repository"
            elif c.same_as_base:
                default, note = "dotkit", "the same as the dotkit file"
            else:
                default = options[0]
            items.append(_file_item(c.target, options, default, note))
        if s.gitconfig:
            g = s.gitconfig
            if g.secrets:
                items.append(_file_item(".gitconfig", ["keep"], "keep",
                                        "has secrets: " + ", ".join(sorted({x.what for x in g.secrets})) +
                                        ". It stays out of the repository"))
            else:
                items.append(_file_item(".gitconfig", ["adopt", "keep"], "adopt",
                                        "Git reads it after the dotkit file"))
        if s.mise_config:
            if secrets.has_secret(s.mise_config):
                items.append(_file_item(".config/mise/config.toml", ["keep"], "keep",
                                        "has secrets. It stays out of the repository"))
            else:
                items.append(_file_item(".config/mise/config.toml", ["layer", "keep"], "layer",
                                        "your global mise tools; goes to config/mise.toml"))
        return items

    # --- The plan

    def plan(self, facts: Facts, c: Choices, txn: docs_mod.Txn) -> None:
        _Build(self, facts, c, txn).run()


def tap_line(t: scan_mod.Tap) -> str:
    """The line of a tap in nix/taps.txt: the name, and the clone URL of a tap that is not on GitHub."""
    return f"{t.name} {t.remote}" if t.custom else t.name


def tap_line_problem(t: scan_mod.Tap) -> str | None:
    """Why a tap cannot go into nix/taps.txt, or None: its name first, as the name has no spaces."""
    return tap_name_problem(t.name) or tap_problem(tap_line(t))


def tap_lines(taps: list[scan_mod.Tap], declared: set[str]) -> tuple[list[str], list[str]]:
    """The nix/taps.txt lines of the taps that are not declared, and to-dos for the ones that cannot go there."""
    lines, todos, seen = [], [], set(declared)
    for t in taps:
        if t.name in seen:
            continue
        problem = tap_line_problem(t)
        if problem is None:
            seen.add(t.name)
            lines.append(tap_line(t))
        elif problem.startswith("its name"):
            todos.append(f"`{t.name}`: it cannot go into nix/taps.txt: {problem}.")
        elif problem.startswith("it has a secret"):
            todos.append(f"`{t.name}`: its URL has a secret ({problem[len('it has a secret ('):-1]}), so it is not "
                         "in nix/taps.txt. Take the credential out of the URL (for example with an SSH URL or a "
                         "git credential helper), then add the tap to nix/taps.txt. Do not put the URL with the "
                         "credential into a file that Git tracks.")
        else:
            todos.append(f"`{t.name}`: its URL cannot go into nix/taps.txt ({problem}). Add it by hand to "
                         f"`homebrew.taps` in nix/darwin.nix as "
                         f"`{{ name = {nix_string(t.name)}; clone_target = {nix_string(t.remote)}; }}`.")
    return lines, todos


def mas_lines(apps: list[tuple[int, str]], taken: dict[str, int],
              declared: set[int]) -> tuple[list[tuple[int, str]], list[str], list[str]]:
    """The App Store apps for nix/mas.txt as (ID, label), notes on the labels, and to-dos for the rest.

    homebrew.masApps lists apps by name, and the name is only a label. An app
    whose name is `taken` (in nix/mas.txt, by an earlier app, or by a name in
    the configuration) gets the label `<name> (<ID>)`. An app whose ID is
    `declared` already is left out.
    """
    out, notes, todos, taken = [], [], [], dict(taken)
    for i, n in apps:
        n = n.strip()
        if i in declared:
            continue
        problem = mas_problem(i, n)
        if problem:
            todos.append(f"`{n}` (ID {i}): it cannot go into nix/mas.txt: {problem}.")
            continue
        label = mas_label(i, n, taken)
        if label is None or label in taken:
            label = f"{n} ({i})"
            todos.append(f"`{n}` (ID {i}): other apps have this name and `{label}`, so it is not in nix/mas.txt. "
                         f"Add it there under another name: `{i} <name>`.")
            continue
        if label != n:
            notes.append(f"`{n}` (ID {i}): another app has this name, so nix/mas.txt lists it as `{label}`.")
        taken[label] = i
        out.append((i, label))
    return out, notes, todos


class _Build:
    def __init__(self, planner: Planner, facts: Facts, c: Choices, txn: docs_mod.Txn):
        self.p = planner
        self.s = planner.scan
        self.identity = facts.identity
        self.base = planner.base
        self.c = c
        self.home = self.s.home
        # The items of the pages: a deselected item that was on by default is "Not carried, by your choice".
        self.items = {i.id: i for page in planner.pages() for i in page.items}
        # The planning steps write only through this transaction.
        self.txn = txn
        self.r = self.txn.report
        # Repository files that are merged from several of yours, and what they are from.
        self.merged: dict[str, str] = {}

    def t(self, path: str | Path) -> str:
        return tilde(path, self.home)

    def picked(self, page: str, item_id: str) -> bool:
        return self.c.selected(page, item_id)

    def left_out(self, item_id: str) -> bool:
        """True for an item that was on by default and that you deselected. Off by default: its own to-do."""
        item = self.items.get(item_id)
        return item is not None and item.default

    def not_carried(self, intro: str, items: list[str]) -> None:
        if items:
            self.r.todo("Not carried, by your choice", intro, items)

    def run(self) -> None:
        # Your mise config first: the tools that it has are not added again.
        if self.mise_layered():
            self.txn.mise().set_base(self.s.mise_config.read_text(encoding="utf-8", errors="replace"))
        self.homebrew()
        self.app_store()
        self.tools()
        self.version_managers()
        self.shell()
        self.config_files()
        self.other_config()
        self.secret_files()

    # --- Homebrew and the App Store

    def homebrew(self) -> None:
        s, r = self.s, self.r
        if not s.brew:
            return
        left: list[str] = []
        for kind, names, key, dest in (("formula", s.formulae, "formulae", "nix/brews.txt"),
                                       ("cask", s.casks, "casks", "nix/casks.txt")):
            keep, redundant = self.p.brew_split(names, kind)
            flag = "--cask " if kind == "cask" else ""
            prefix = "cask" if kind == "cask" else "brew"
            chosen = [n for n in keep if self.picked(key, f"{prefix}:{n}")]
            for n in chosen:
                self.txn.list(dest).add(n)
            if chosen:
                r.carried(plural(len(chosen), "Homebrew formula" if kind == "formula" else "Homebrew cask",
                                  "Homebrew formulae" if kind == "formula" else "Homebrew casks"), dest)
            left += [f"`{n}` ({kind}): `dotkit add {prefix}:{n}`" for n in keep
                     if n not in chosen and self.left_out(f"{prefix}:{n}")]
            if redundant:
                r.todo("Homebrew copies of dotkit tools",
                       "The dotkit base has these tools, so they are not in your lists. The Homebrew copy "
                       "stays installed. Remove it when nothing else needs it; brew says when another "
                       "formula depends on it.",
                       [f"`{n}`: dotkit has {what}. `brew uninstall {flag}{n}`" for n, what in redundant.items()])
        lines, todos = tap_lines(s.taps, set())
        chosen = [line for line in lines if self.picked("taps", f"tap:{line.split()[0]}")]
        for line in chosen:
            self.txn.list("nix/taps.txt").add(line)
        if chosen:
            r.carried(plural(len(chosen), "Homebrew tap"), "nix/taps.txt")
            names = {line.split()[0] for line in chosen}
            custom = [t for t in s.taps if t.custom and t.name in names]
            if custom:
                r.todo("Homebrew taps", "These taps are not on GitHub, so the list has their URL. Check it.",
                       [f"`{t.name}`: {t.remote}" for t in custom])
        self.not_carried(NOT_CARRIED_BREW, left)
        self.not_carried(NOT_CARRIED, [f"`{line.split()[0]}` (tap): add `{line}` to nix/taps.txt" for line in lines
                                       if line not in chosen and self.left_out(f"tap:{line.split()[0]}")])
        if todos:
            r.todo("Homebrew taps", "", todos)
        if s.old_taps:
            r.todo("Homebrew taps", "Homebrew merged these taps into its own. Remove them.",
                   [f"`{t}`: `brew untap {t}`" for t in s.old_taps])

    def app_store(self) -> None:
        s, r = self.s, self.r
        # The apps that cannot go into the list are not items; mas_lines() writes their to-dos.
        chosen = [(i, n) for i, n in s.mas_apps
                  if self.picked("mas", f"mas:{i}") or f"mas:{i}" not in self.items]
        apps, notes, todos = mas_lines(chosen, {}, set())
        for i, n in apps:
            self.txn.list("nix/mas.txt").add(f"{i} {n}")
        if apps:
            r.carried(plural(len(apps), "App Store app"), "nix/mas.txt")
        if notes or todos:
            r.todo("App Store apps", "", notes + todos)
        self.not_carried(NOT_CARRIED, [f"`{n.strip()}` (App Store app): `dotkit add mas:{i}`"
                                       for i, n in s.mas_apps if (i, n) not in chosen and self.left_out(f"mas:{i}")])
        if s.store_apps:
            r.todo("App Store apps",
                   "These apps came from the App Store. dotkit installs App Store apps with mas, which was not "
                   "on this Mac, so the scan has no IDs. Run `dotkit add brew:mas`, then `mas list`, and "
                   "`dotkit add mas:ID` for each app.",
                   [f"{n}" for n in s.store_apps])

    # --- Tools of other package managers

    def mise_layered(self) -> bool:
        """Your mise config goes into the repository: config/mise.toml is your file."""
        s = self.s
        return bool(s.mise_config and self.c.option("file:.config/mise/config.toml") == "layer"
                    and not secrets.has_secret(s.mise_config))

    def tools(self) -> None:
        s, r = self.s, self.r
        mise = self.txn.mise()

        def add(key: str, comment: str) -> bool:
            return mise.add_tool(key, "latest", comment)

        groups = (
            ("npm", s.npm, "npm", "# From npm, pnpm, and bun (global packages), see MIGRATION.md.",
             "npm uninstall -g {name}"),
            ("pipx", s.pipx, "pipx", "# From pipx and uv tool, see MIGRATION.md.", "{remove}"),
            ("cargo", s.cargo, "cargo", "# From cargo install, see MIGRATION.md.", "cargo uninstall {name}"),
            ("go", s.go, "go", "# From ~/go/bin, see MIGRATION.md.", "rm {where}"),
        )
        for key, pkgs, backend, comment, remove in groups:
            carried, manual, old, left = [], [], [], []
            for p in pkgs:
                if key == "npm" and p.name in catalog.NPM_NOT_IN_MISE:
                    manual.append(f"`{p.name}` ({p.where}): {catalog.NPM_NOT_IN_MISE[p.name]}")
                    continue
                if not p.ok:
                    manual.append(f"`{p.name}` ({p.where}): {p.note}. Add it to config/mise.toml by hand.")
                    continue
                spec = f"{backend}:{p.name}"
                if self.picked(key, f"{key}:{p.name}"):
                    if add(spec, comment):
                        carried.append(p)
                        rm = remove.format(name=p.name, where=p.where,
                                           remove=("pipx uninstall " if p.where == "pipx" else "uv tool uninstall ")
                                           + p.name)
                        old.append(f"`{spec}`, was {p.version or 'installed'} in {p.where}. "
                                   f"Old copy: `{rm}`" + (f". Note: {p.note}" if p.note else ""))
                elif self.left_out(f"{key}:{p.name}"):
                    left.append(f"`{p.name}` ({p.where}): `mise use -g {spec}`")
                else:
                    manual.append(f"`{p.name}` ({p.where}): `mise use -g {spec}`")
            self.not_carried(NOT_CARRIED, left)
            if carried:
                r.carried(plural(len(carried), f"{key} tool"), "config/mise.toml")
                r.todo("Tools that moved to mise",
                       "mise installs these at the end of setup (`mise install`). The old copies stay, and "
                       "they can come first in PATH. Remove each one when the mise copy works.",
                       old)
            if manual:
                why = ""
                if key in catalog.BUILT_FROM_SOURCE:
                    why = f"mise builds {catalog.BUILT_FROM_SOURCE[key]} tools from source, so they are off by default."
                r.todo("Tools that are not in mise", (why + " " if why else "") +
                       "They stay where they are. To add one to config/mise.toml:", manual)
            if key == "cargo" and carried and not s.has_cargo:
                r.todo("Tools that moved to mise", "", ["mise needs cargo for these: `mise use -g rust`."])
            if key == "go" and carried and not s.has_go:
                r.todo("Tools that moved to mise", "", ["mise needs go for these: `mise use -g go`."])

    def version_managers(self) -> None:
        s, r = self.s, self.r
        lines = self.p.runtime_lines()
        picked = {t for t in lines if self.picked("runtimes", f"runtime:{t}")}
        for tool, versions in lines.items():
            if tool in picked:
                self.txn.mise().add_tool(tool, versions[0] if len(versions) == 1 else versions)
        if picked:
            r.carried("runtime versions of " + ", ".join(
                vm.name for vm in s.version_managers
                if vm.versions and (not catalog.version_manager(vm.name).mise_replaces or picked & set(vm.versions))),
                "config/mise.toml")
        items = []
        for vm in s.version_managers:
            parts = []
            for tool, versions in vm.versions.items():
                d = vm.default.get(tool)
                parts.append(f"{tool} {', '.join(versions)}" + (f" (default {d})" if d else ""))
            what = "; ".join(parts) or "no versions"
            mise = ""
            if not catalog.version_manager(vm.name).mise_replaces:
                mise = ("dotkit has the Zulu 17 JDK (JAVA_HOME). For another JDK: `mise ls-remote java`, then "
                        "`mise use -g java@<version>`")
            else:
                tools = [t for t in vm.versions if t]
                done = [t for t in tools if t in picked]
                rest = [t for t in tools if t not in picked]
                mise = ", ".join(f"`mise use -g {t}@{(lines.get(t) or ['<version>'])[0]}`" for t in rest)
                if done and not rest:
                    mise = "config/mise.toml has these versions now"
                elif done:
                    names = ", ".join(done[:-1]) + " and " + done[-1] if len(done) > 1 else done[0]
                    mise = f"config/mise.toml has the {names} versions now. With mise: " + mise
                elif mise:
                    mise = "With mise: " + mise
            steps = ". ".join(x for x in (what, mise) if x)
            items.append(f"{vm.name} ({vm.path}): {steps}. Then remove its lines from the shell files "
                         f"(the report lists them) and `rm -rf {vm.path}`")
        if items:
            r.todo("Version managers",
                   "mise replaces them; dotkit's shell does not load them. The base has Node 24, Python 3.14, "
                   "and Ruby 3.4. A project pins its own versions with `mise use` or a .nvmrc, .python-version, "
                   "or .ruby-version file.", items)
        for extra in s.mise_extra:
            r.todo("mise", "mise still reads these files. Move their tools to config/mise.toml.",
                   [f"~/{extra}"])

    # --- Shell files

    def shell(self) -> None:
        s, r = self.s, self.r
        # The chain of the selected files. A copy: a failed syntax check below changes the results.
        skipped = frozenset(n for n in self.p.rc_results()
                            if f"shell:{n}" in self.items and not self.picked("shell", f"shell:{n}"))
        results = copy.deepcopy(self.p.rc_results(skipped))
        unread = [f for f in s.shell_files if not f.managed and not f.read]
        by_repo: dict[str, list[tuple[ShellFile | None, rcparse.Result | None]]] = {}
        files = {f.name: f for f in s.shell_files if not f.managed}
        for main, (layer, _) in base_mod.SHELL_LAYERS.items():
            for name in (main, layer):
                f = files.get(name)
                if f is None:
                    continue
                by_repo.setdefault(main, []).append((f, results.get(name)))

        for main, entries in by_repo.items():
            layer, repo_file = base_mod.SHELL_LAYERS[main]
            shell = "bash" if main == ".bash_profile" else "zsh"
            rc = self.txn.shell_rc(main)
            written = False
            for f, res in entries:
                if res is None:
                    continue
                on = self.picked("shell", f"shell:{f.name}")
                carried = res.carried_text() if on else ""
                if carried and self.p.check_syntax and not self.p.check_syntax(shell, carried):
                    # Keep the lines that parse on their own; report the functions.
                    for cl in [c for c in res.carried if c.kind == "function"]:
                        res.findings.append(rcparse.Finding(cl.lines, rcparse.BLOCKS, cl.text,
                                                            f"{shell} -n could not check it"))
                    res.carried = [c for c in res.carried if c.kind != "function"]
                    res.findings.sort(key=lambda x: x.lines)
                    carried = res.carried_text()
                    if carried and not self.p.check_syntax(shell, carried):
                        for cl in res.carried:
                            if cl.kind != "comment":
                                res.findings.append(rcparse.Finding(cl.lines, rcparse.COMMANDS, cl.text,
                                                                    f"{shell} -n could not check it"))
                        res.carried = []
                        carried = ""
                if carried:
                    rc.add_part(f"# From ~/{f.name}. MIGRATION.md lists the lines that are not here.", carried)
                    written = True
                    r.carried(plural(res.statements, "line") + f" of ~/{f.name}", f"{repo_file} (~/{layer})")
                if res.secrets and on:
                    rc.add_secret("\n".join([f"# From ~/{f.name}."] + [x.text for x in res.secrets]))
                    written = True
                    r.todo("Secrets", "These lines have a secret, so they are not in Git. They are in a file in "
                                      "local/ of this repository, which Git ignores, and the repository file reads "
                                      "it. On a new Mac, copy the file by hand, or keep its lines in a manager for "
                                      "passwords.",
                           [f"~/{f.name}:{x.lines[0]}: `{x.name}` -> {rc.local}"
                            for x in res.secrets])
                if not on and self.left_out(f"shell:{f.name}"):
                    # What the page offered: the full chain, as the page label counts it.
                    self.not_carried(NOT_CARRIED, [_shell_left_out(f.name, self.p.rc_results()[f.name], repo_file)])
                self.findings(f.name, res)
            # A file for each layer that exists, also when it carries nothing: the
            # link moves an old layer file out of the way, so that dotkit does not
            # read a file that was not read before.
            if not (written or main == ".zshrc" or layer in files):
                continue
            rc.ensure()
            if main != ".zshrc":
                self.txn.links().link(layer, repo_file)
        for f in unread:
            r.todo("Shell lines", "", [f"~/{f.name} was not read by ~/{f.name.removesuffix('.local')}, so nothing "
                                       f"of it is carried. dotkit links its own ~/{f.name} and renames yours to "
                                       f"~/{f.name}.before-dotkit. Copy what you need."])
        for name in s.other_shell_files:
            if self.picked("other", f"other:{name}"):
                continue
            note = {
                ".bashrc": "It stays in place. dotkit's ~/.bash_profile reads it in an interactive bash.",
                ".profile": "bash -l no longer reads it, because dotkit links ~/.bash_profile. Copy what you "
                            "need to config/bash/bash_profile and link it as ~/.bash_profile.local.",
                ".bash_login": "bash -l no longer reads it, because dotkit links ~/.bash_profile.",
                ".zlogin": "It stays in place, and zsh still reads it after the dotkit files. Check that it "
                           "still works, or move its lines to config/zsh/zshrc.",
                ".zlogout": "It stays in place, and zsh still reads it.",
            }[name]
            r.todo("Other shell files", "", [f"~/{name}: {note}"])
        if s.frameworks:
            r.todo("Shell frameworks",
                   "dotkit's zsh does not load them. Remove each one when you no longer need it.",
                   [f"{n}: `rm -rf {p}`" for n, p in s.frameworks.items()])
        login_shell = self.identity.login_shell
        shell = os.path.basename(login_shell or "")
        if login_shell and not login_shell.endswith("/sw/bin/zsh") and shell != "zsh":
            r.todo("Login shell",
                   f"Your login shell is {login_shell}. dotkit makes the zsh of Nix the login shell, so the "
                   f"config of {shell} is not used in new terminals.", [])

    def findings(self, name: str, res: rcparse.Result) -> None:
        order = [c for c, _ in rcparse.CATEGORIES]
        by_cat: dict[str, list[rcparse.Finding]] = {}
        for f in res.findings:
            by_cat.setdefault(f.category, []).append(f)
        zshrc = "config/zsh/zshrc" if name.startswith(".zsh") else "config/bash/bash_profile"
        zshenv = "config/zsh/zshenv" if name.startswith(".zsh") else "config/bash/bash_profile"
        for cat in order:
            fs = by_cat.get(cat)
            if not fs:
                continue
            items = []
            for f in fs:
                text = "(not shown: it has a secret)" if f.secret else _code(f.text)
                items.append(f"~/{name}:{f.where} {text}" + (f" ({f.note})" if f.note else ""))
            self.r.todo(f"Shell lines: {cat}", rcparse.hint(cat, zshrc, zshenv), items)

    # --- Config files

    def config_files(self) -> None:
        s, r = self.s, self.r
        links = {link.target: link for link in base_mod.LINKS}
        git_choice = self.c.option("file:.gitconfig") or ("adopt" if s.gitconfig else "")
        if s.gitconfig and s.gitconfig.secrets:
            git_choice = "keep"
        git_parts: list[tuple[str, Path]] = []
        for c in s.config_files:
            link = links[c.target]
            choice = self.c.option(f"file:{c.target}") or "dotkit"
            if c.secrets and choice not in ("keep", "dotkit"):
                choice = "keep"
            where = self.t(c.path)
            if c.target == ".config/git/config" and choice == "layer":
                if s.gitconfig and git_choice != "adopt":
                    choice = "dotkit"
                    r.todo("Config files", "", [f"{where}: dotkit links its own file here, and yours is renamed to "
                                                f"{where}.before-dotkit. Copy its settings to ~/.gitconfig."])
                else:
                    git_parts.append((where, c.path))
                    continue
            if choice == "layer":
                self.txn.copies.add(link.repo_file, c.path)
                self.txn.links().link(link.layer, link.repo_file)
                r.carried(where, f"{link.repo_file} (~/{link.layer}, read after the dotkit file)")
                if c.target == ".config/ghostty/config":
                    r.todo("Config files", "", [f"{link.repo_file}: Ghostty reads it after the dotkit settings. "
                                                "Delete the lines that you do not need to set again."])
            elif choice == "replace":
                self.txn.copies.add(link.repo_file, c.path)
                self.txn.links().link(c.target, link.repo_file)
                r.carried(where, f"{link.repo_file} (instead of the dotkit file)")
                r.todo("Config files", "", [f"{link.repo_file} replaces the dotkit file "
                                            f"config/{Path(link.source).name}. Compare the two; the dotkit file "
                                            f"has settings that yours does not, for example "
                                            f"{_base_example(c.target)}."])
            elif choice == "keep":
                self.txn.links().link(c.target, None)
                why = "it has secrets (" + ", ".join(sorted({x.what for x in c.secrets})) + ")" if c.secrets else \
                    "you chose to keep it"
                r.todo("Config files", "", [f"{where} stays in place and outside the repository, because {why}. "
                                            f"dotkit does not link its own file there. To get the dotkit "
                                            f"settings, copy them from config/{Path(link.source).name} of dotkit."])
            else:
                if not c.same_as_base:
                    r.todo("Config files", "", [f"{where} is replaced by the dotkit file and renamed to "
                                                f"{where}.before-dotkit. Copy the settings you miss to "
                                                + (f"~/{link.layer}" if link.layer else "your own copy") + "."])
        if s.gitconfig:
            where = self.t(s.gitconfig.path)
            if git_choice == "adopt":
                git_parts.append((where, s.gitconfig.path))
            else:
                why = "it has secrets" if s.gitconfig.secrets else "you chose to keep it"
                r.todo("Config files", "", [f"{where} stays in place, because {why}. Git reads it after the "
                                            "dotkit file, as before."])
        if git_parts:
            if len(git_parts) == 1:
                self.txn.copies.add("config/gitconfig", git_parts[0][1])
            else:
                self.txn.file("config/gitconfig").set("".join(
                    f"# From {w}.\n" + p.read_text(encoding="utf-8", errors="replace").rstrip("\n") + "\n\n" for w, p in git_parts))
                self.merged["config/gitconfig"] = " and ".join(w for w, _ in git_parts)
            self.txn.links().link(".gitconfig", "config/gitconfig")
            r.carried(" and ".join(w for w, _ in git_parts), "config/gitconfig (~/.gitconfig, read after the "
                                                              "dotkit file)")
            r.todo("Config files", "", ["config/gitconfig: Git reads it after the dotkit settings "
                                        "(config/gitconfig of dotkit). Delete the settings that are the same. "
                                        "Your name and email are in ~/.gitconfig.user."])
        if s.ghostty_app_support:
            r.todo("Config files", "", ["~/Library/Application Support/com.mitchellh.ghostty/config: Ghostty reads "
                                        "it after ~/.config/ghostty/config, so it wins over the dotkit settings. "
                                        "Move its lines to config/ghostty.conf of this repository."])
        # The mise config. run() made it the base of config/mise.toml when it is layered.
        if self.mise_layered():
            r.carried("~/.config/mise/config.toml", "config/mise.toml")
        elif s.mise_config:
            self.txn.links().link(".config/mise/config.toml", None)
            r.todo("mise", "", ["~/.config/mise/config.toml stays in place, outside the repository, and dotkit "
                                "does not link config/mise.toml. `dotkit add` writes mise tools to it."])

    def other_config(self) -> None:
        r = self.r
        adopted, left, skipped, taken = [], [], [], []
        for o in self.s.other_config:
            if self.picked("other", f"other:{o.rel}") and o.adoptable:
                name = o.rel.removeprefix(".config/").lstrip(".")
                dest = f"config/{name}" if o.rel.startswith(".config/") else f"config/home/{name}"
                if self.txn.copies.add(dest, Path(self.home, o.rel)):
                    self.txn.links().link(o.rel, dest)
                    adopted.append(f"~/{o.rel} -> {dest}")
                else:
                    how, where = self.txn.copies.taken(dest)
                    src = self.txn.copies.source(where)
                    has = self.t(src) if src else self.merged.get(where, "another file")
                    keep = ("To keep it in the repository, copy it under another name and link it in "
                            "nix/home.nix.")
                    if how == "contains":
                        taken.append(f"~/{o.rel}: its place in the repository, {dest}, would contain {where}; "
                                     f"it stays in place. {keep}")
                    elif how == "inside":
                        taken.append(f"~/{o.rel}: its place in the repository, {dest}, is inside {where}, "
                                     f"which has {has} already. It stays in place. {keep}")
                    else:
                        taken.append(f"~/{o.rel}: its place in the repository, {dest}, has {has} already. It "
                                     f"stays in place. {keep}")
            elif o.adoptable:
                left.append(f"~/{o.rel}")
            elif o.reason not in ("empty", "a Home Manager link"):
                skipped.append(f"~/{o.rel}: {o.reason}")
        if adopted:
            r.carried(plural(len(adopted), "other config file or directory", "other config files and directories"),
                      "config/, linked with dotkit.links")
        if taken:
            r.todo("Other config", "", taken)
        if left:
            r.todo("Other config", "These stay in place and keep working. To keep one in the repository, copy "
                                   "it to config/ and link it in nix/home.nix, for example "
                                   "`dotkit.links.\".config/nvim\" = \"config/nvim\";`.", left)
        if skipped:
            r.todo("Other config that stays out of the repository",
                   "These stay in place. They hold state, caches, or logins, so they are not for Git.", skipped)

    def secret_files(self) -> None:
        steps = {
            ".config/gh/hosts.yml": "on a new Mac, `gh auth login`",
            ".netrc": "copy it by hand",
            ".config/github-copilot": "sign in to Copilot again on a new Mac",
            ".config/gcloud": "on a new Mac, `gcloud auth login`",
            ".config/configstore": "the tools that use it ask again on a new Mac",
            ".config/op": "sign in to 1Password again on a new Mac",
            ".docker/config.json": "on a new Mac, `docker login`",
            ".aws/credentials": "on a new Mac, `aws configure`",
            ".gem/credentials": "on a new Mac, `gem signin`",
            ".pypirc": "copy it by hand",
        }
        items = [f"~/{f}: {steps.get(f, 'copy it by hand')}" for f in self.s.secret_files]
        if items:
            self.r.todo("Logins", "These files hold logins. They stay in place and are not in the repository.",
                        items)


def _shell_left_out(name: str, res: rcparse.Result, repo_file: str) -> str:
    """The "Not carried, by your choice" item of a shell file: what could be carried, and the secrets by name."""
    parts = []
    if res.statements:
        parts.append(f"{plural(res.statements, 'line')} that could be carried to {repo_file}. Copy the ones "
                     "you need.")
    if res.secrets:
        names = ", ".join(f"`{x.name}` (line {x.lines[0]})" for x in res.secrets)
        parts.append(f"Its lines with secrets are not carried: {names}. Keep them out of files that Git tracks.")
    assert parts, f"~/{name} is a shell item, so it has lines or secrets"
    return f"~/{name}: " + " ".join(parts)


def _code(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    if len(first) > 100:
        first = first[:99] + "…"
    more = text.strip().count("\n")
    first = first.replace("`", "'")
    return f"`{first}`" + (f" (+{more} lines)" if more else "")


def _base_example(target: str) -> str:
    return {
        ".npmrc": "min-release-age=7 and prefix=${HOME}/.local",
        ".config/uv/uv.toml": "the 7-day wait for new releases",
        ".config/starship.toml": "the Nerd Font symbols and the Git status",
        ".gemrc": "the 7-day wait for new gems",
        ".bunfig.toml": "the 7-day wait for new releases",
        "Library/Preferences/pnpm/config.yaml": "the 7-day wait for new releases",
        ".editorconfig": "the defaults for indentation and line ends",
        ".config/herdr/config.toml": "the Git status plugin",
        ".config/git/ignore": "the local files of coding agents",
    }.get(target, "its defaults")
