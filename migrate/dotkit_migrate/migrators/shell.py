"""Shell files: the lines of ~/.zshenv, ~/.zprofile, ~/.zshrc, ~/.bash_profile, and their .local files.

rcparse sorts each statement: carried to the repository file of its layer,
a secret, or a finding for the report. An assignment with a secret goes to
~/.secrets, outside the repository; another statement with a secret goes to
the report, without its text. dotkit
links its own shell files, so the other shell files get to-dos; the
"Login shell" to-do is core (registry.py), as it reads only the identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

from .. import base as base_mod
from .. import rcparse, secrets_file
from ..context import Context, executables, is_managed, read_text
from ..docs import Txn
from ..model import Choices, Facts, Item, Page
from ..report import plural
from . import NOT_CARRIED, Migrator, and_list, left_out, other_config

# Shell files that the base links, and the layer files of yours that the
# base reads after them, in the order zsh and bash read them.
SHELL_FILES = (
    ".zshenv", ".zshenv.local", ".zprofile", ".zprofile.local", ".zshrc", ".zshrc.local",
    ".bash_profile", ".bash_profile.local",
)
# The layer file of each base shell file, for the files above.
SHELL_MAIN = {
    ".zshenv.local": ".zshenv",
    ".zprofile.local": ".zprofile",
    ".zshrc.local": ".zshrc",
    ".bash_profile.local": ".bash_profile",
}
# Shell files that dotkit does not link. They stay and keep working, or
# stop being read: see the "Other shell files" to-dos.
OTHER_SHELL_FILES = (".bashrc", ".profile", ".bash_login", ".zlogin", ".zlogout")


@dataclass
class ShellFile:
    name: str  # relative to the home directory
    path: Path
    text: str
    managed: bool = False  # a dotkit or Home Manager link already
    # For a layer file (~/.zshrc.local): the main file reads it.
    read: bool = True


@dataclass
class ShellFacts:
    home: str
    files: List[ShellFile] = field(default_factory=list)
    other_files: List[str] = field(default_factory=list)  # OTHER_SHELL_FILES that exist
    # The parsed files of the full chain, for the page labels and for what a deselected file offered.
    results: Dict[str, rcparse.Result] = field(default_factory=dict)
    # The executables of each directory that a carried PATH line adds, by directory: name -> link target ("" for
    # a file that is not a link), for the leftovers.
    executables: Dict[str, Dict[str, str]] = field(default_factory=dict)


def facts_of(f: Facts) -> Optional[ShellFacts]:
    return f.get(Shell.key)


def parse_chain(home: str, files: List[ShellFile], skipped: FrozenSet[str] = frozenset()) -> Dict[str, rcparse.Result]:
    """The parsed shell files, by name. Layer files that were not read before are not parsed.

    The variables that the carried lines set pass on to the next file. A
    file in `skipped` (deselected) is parsed too, for its findings and the
    names of its secret lines, but it passes on nothing: the next file starts
    with the variables known before it. The scan parses the full chain.
    """
    out: Dict[str, rcparse.Result] = {}
    by_name = {f.name: f for f in files if not f.managed}
    for shell, names in (("zsh", (".zshenv", ".zshenv.local", ".zprofile", ".zprofile.local",
                                  ".zshrc", ".zshrc.local")),
                         ("bash", (".bash_profile", ".bash_profile.local"))):
        parser = rcparse.Parser(home, shell)
        known: Dict[str, str] = {}
        for name in names:
            f = by_name.get(name)
            if f is None or not f.read:
                continue
            r = parser.parse(name, f.text, known)
            if name not in skipped:
                known = r.variables
            out[name] = r
    return out


def _shell_of(name: str) -> str:
    return "bash" if name.startswith(".bash") else "zsh"


def _check(f: Facts, shell: str, res: rcparse.Result) -> None:
    """Drops the carried lines that do not pass the syntax check, and reports them."""
    if not (res.carried_text() and f.check_syntax) or f.check_syntax(shell, res.carried_text()):
        return
    # Keep the lines that parse on their own; report the functions.
    for cl in [k for k in res.carried if k.kind == "function"]:
        res.findings.append(rcparse.Finding(cl.lines, rcparse.BLOCKS, cl.text, f"{shell} -n could not check it"))
    res.carried = [k for k in res.carried if k.kind != "function"]
    res.findings.sort(key=lambda y: y.lines)
    if res.carried_text() and not f.check_syntax(shell, res.carried_text()):
        for cl in res.carried:
            if cl.kind != "comment":
                res.findings.append(rcparse.Finding(cl.lines, rcparse.COMMANDS, cl.text,
                                                    f"{shell} -n could not check it"))
        res.carried = []


def planned(f: Facts, c: Choices) -> Dict[str, rcparse.Result]:
    """The parsed files as the plan carries them: the chain of the selected files, then the syntax check of each
    selected file. A deselected file is parsed too, for its findings, and carries nothing. {} without the facts."""
    sh = facts_of(f)
    if sh is None:
        return {}
    items = {i.id for page in Shell().pages(f) for i in page.items}
    skipped = frozenset(n for n in sh.results if f"shell:{n}" in items and not c.selected("shell", f"shell:{n}"))
    results = parse_chain(sh.home, sh.files, skipped)
    for name, res in results.items():
        if c.selected("shell", f"shell:{name}"):
            _check(f, _shell_of(name), res)
    return results


def carried_paths(f: Facts, c: Choices) -> List[Tuple[str, Tuple[int, int], Tuple[str, ...], Tuple[str, ...], str]]:
    """The carried PATH lines of this plan: (shell file, its lines, the directories, those of them that go in
    front of PATH, the repository file)."""
    out = []
    for name, res in planned(f, c).items():
        if not c.selected("shell", f"shell:{name}"):
            continue
        repo_file = base_mod.SHELL_LAYERS[name.removesuffix(".local")][1]
        out += [(name, cl.lines, cl.dirs, cl.front, repo_file) for cl in res.carried if cl.kind == "path"]
    return out


class Shell(Migrator):
    key = "shell"
    title = "Shell lines"
    sections = ("Secrets", "Shell lines", "Other shell files", "Not carried, by your choice")

    def manual_steps(self, ctx: Context) -> str:
        """Section 7.5: what the template does without this migrator, from base.SHELL_LAYERS."""
        layers = base_mod.SHELL_LAYERS
        zshrc_layer, zshrc_file = layers[".zshrc"]
        others = [f"~/{layer}" for main, (layer, _) in layers.items() if main != ".zshrc"]
        dirs: List[str] = []
        for _, repo_file in layers.values():
            d = str(Path(repo_file).parent) + "/"
            if d not in dirs:
                dirs.append(d)
        return (f"Nothing of your shell files is carried. dotkit links its own "
                f"{and_list([f'~/{main}' for main in layers])}, and the template's {zshrc_file} as "
                f"~/{zshrc_layer}. The first apply renames your files at these paths to <name>.before-dotkit. "
                f"Your {and_list(others)} stay in place if they exist, and dotkit's files source them, also if "
                "your old files did not. Check what they run (a framework, a version manager, PATH changes). They "
                f"stay outside the repository. Copy the lines you need from the .before-dotkit files to "
                f"{and_list(dirs)} of this repository, and keep lines with secrets out of files that Git tracks.")

    def scan(self, ctx: Context) -> ShellFacts:
        sh = ShellFacts(str(ctx.home))
        texts: Dict[str, str] = {}
        for name in SHELL_FILES:
            p = ctx.home / name
            if not (p.is_file() or p.is_symlink()):
                continue
            managed = is_managed(p)
            text = "" if managed else read_text(p)
            texts[name] = text
            sh.files.append(ShellFile(name, p, text, managed))
        for f in sh.files:
            main = SHELL_MAIN.get(f.name)
            if main and not f.managed:
                # Read before only when the main file sourced it.
                f.read = f.name in texts.get(main, "")
        for name in OTHER_SHELL_FILES:
            p = ctx.home / name
            if p.is_file() and not is_managed(p):
                sh.other_files.append(name)
        sh.results = parse_chain(sh.home, sh.files)
        for r in sh.results.values():
            for d in (d for cl in r.carried for d in cl.dirs):
                if d not in sh.executables:
                    sh.executables[d] = executables(Path(d))
        return sh

    def pages(self, f: Facts) -> List[Page]:
        sh = facts_of(f)
        if sh is None:
            return []
        items = []
        for name, r in sh.results.items():
            if not (r.statements or r.secrets):
                continue
            main = name.removesuffix(".local")
            layer, repo_file = base_mod.SHELL_LAYERS[main]
            parts = [plural(r.statements, "line")] if r.statements else []
            detail = f"To {repo_file} (~/{layer})."
            if r.secrets:
                parts.append(plural(len(r.secrets), "line") + " with a secret")
                detail += " Variables with a secret go to ~/.secrets, outside the repository."
            label = f"~/{name}: " + " and ".join(parts)
            items.append(Item(f"shell:{name}", label, detail))
        return [Page("shell", "Shell lines", "config/zsh, config/bash",
                     "The lines of your shell files that dotkit can carry, by file. MIGRATION.md lists the lines "
                     "that it cannot carry.", "checklist", items)]

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        sh, r = facts_of(f), txn.report
        pages = self.pages(f)
        # The chain of the selected files, parsed again, after the syntax check.
        results = planned(f, c)
        unread = [x for x in sh.files if not x.managed and not x.read]
        by_repo: Dict[str, List[Tuple[ShellFile, Optional[rcparse.Result]]]] = {}
        files = {x.name: x for x in sh.files if not x.managed}
        for main, (layer, _) in base_mod.SHELL_LAYERS.items():
            for name in (main, layer):
                x = files.get(name)
                if x is None:
                    continue
                by_repo.setdefault(main, []).append((x, results.get(name)))

        for main, entries in by_repo.items():
            layer, repo_file = base_mod.SHELL_LAYERS[main]
            rc = txn.shell_rc(main)
            written = False
            for x, res in entries:
                if res is None:
                    continue
                on = c.selected("shell", f"shell:{x.name}")
                carried = res.carried_text() if on else ""
                if carried:
                    rc.add_part(f"# From ~/{x.name}. MIGRATION.md lists the lines that are not here.", carried)
                    written = True
                    r.carried(plural(res.statements, "line") + f" of ~/{x.name}", f"{repo_file} (~/{layer})")
                if res.secrets and on:
                    _secrets(txn, x.name, res.secrets)
                if left_out(pages, c, f"shell:{x.name}"):
                    # What the page offered: the full chain, as the page label counts it.
                    r.todo("Not carried, by your choice", NOT_CARRIED,
                           [_left_out_item(x.name, sh.results[x.name], repo_file)])
                _findings(txn, x.name, res)
            # A file for each layer that exists, also when it carries nothing: the
            # link moves an old layer file out of the way, so that dotkit does not
            # read a file that was not read before.
            if not (written or main == ".zshrc" or layer in files):
                continue
            rc.ensure()
            if main != ".zshrc":
                txn.links().link(layer, repo_file)
        for x in unread:
            r.todo("Shell lines", "", [f"~/{x.name} was not read by ~/{x.name.removesuffix('.local')}, so nothing "
                                       f"of it is carried. dotkit links its own ~/{x.name} and renames yours to "
                                       f"~/{x.name}.before-dotkit. Copy what you need."])
        for name in sh.other_files:
            if other_config.adopted(f, c, name):
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


SECRETS_INTRO = ("These variables have a secret, so they are not in Git. They are in ~/.secrets now, which the dotkit "
                 "~/.zprofile and ~/.bash_profile read, so login shells and the programs they start have them. "
                 "On a new Mac, copy ~/.secrets by hand; keep it out of Git.")
NOT_MOVED_INTRO = ("These lines have a secret, and they are not a plain assignment that ~/.secrets can take, so they "
                   "are not carried. Put the secret in ~/.secrets as a variable, for example "
                   "`export MY_TOKEN='...'`, and copy the line, with the variable instead of the secret, to the "
                   "repository file.")


def _secrets(txn: Txn, name: str, lines: List[rcparse.SecretLine]) -> None:
    """The lines with a secret of one shell file: an assignment to ~/.secrets, another statement to the report."""
    r, moved, refused, left = txn.report, [], [], []
    for s in lines:
        m = rcparse._ASSIGN.match(s.text) if "\n" not in s.text.strip() else None
        if m is None:
            left.append(f"~/{name}:{s.lines[0]}: {s.name}")
            continue
        var, value = m.group("name"), m.group("value")
        line = ("export " if m.group("export") else "") + f"{var}={value}"
        if txn.secrets().export(var, secrets_file.value_of(value), line, f"~/{name}"):
            moved.append(f"~/{name}:{s.lines[0]}: `{var}`")
        else:
            refused.append(f"~/{name}:{s.lines[0]}: `{var}`. ~/.secrets has `{var}` with another value already, "
                           "so this one is not added. Check which one you need.")
    if moved:
        r.carried(plural(len(moved), "variable") + f" with a secret of ~/{name}", "~/.secrets")
        r.todo("Secrets", SECRETS_INTRO, moved)
    if refused:
        r.todo("Secrets", "", refused)
    if left:
        r.todo("Secrets", NOT_MOVED_INTRO, left)


def _findings(txn: Txn, name: str, res: rcparse.Result) -> None:
    order = [c for c, _ in rcparse.CATEGORIES]
    by_cat: Dict[str, List[rcparse.Finding]] = {}
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
            text = finding_code(f)
            items.append(f"~/{name}:{f.where} {text}" + (f" ({f.note})" if f.note else ""))
        txn.report.todo(f"Shell lines: {cat}", rcparse.hint(cat, zshrc, zshenv), items)


def _left_out_item(name: str, res: rcparse.Result, repo_file: str) -> str:
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


def finding_code(f: rcparse.Finding) -> str:
    """A line that is not carried, for the report: its first line in backticks, never a secret."""
    return "(not shown: it has a secret)" if f.secret else _code(f.text)


def _code(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    if len(first) > 100:
        first = first[:99] + "…"
    more = text.strip().count("\n")
    first = first.replace("`", "'")
    return f"`{first}`" + (f" (+{more} lines)" if more else "")
