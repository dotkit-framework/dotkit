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
from dataclasses import dataclass, field
from pathlib import Path

from . import base as base_mod
from ._compat import tomllib
from . import rcparse, secrets
from .report import Report, plural
from . import scan as scan_mod
from .scan import Scan, ShellFile

# --- The choices


@dataclass
class Group:
    key: str
    label: str
    dest: str  # where the items go
    items: list[str]
    default: bool
    note: str = ""  # one line for the wizard, for example why it is off
    count: int = -1  # what the wizard shows; -1: the number of items

    @property
    def size(self) -> int:
        return len(self.items) if self.count < 0 else self.count


@dataclass
class FileItem:
    target: str  # relative to the home directory
    options: list[str]
    default: str
    note: str = ""


OPTION_TEXT = {
    "layer": "yours, read after the dotkit file",
    "replace": "yours, instead of the dotkit file",
    "dotkit": "the dotkit file; yours is renamed to <name>.before-dotkit",
    "keep": "leave yours in place, outside the repository",
    "adopt": "yours, in the repository",
}


@dataclass
class Choices:
    repo: str
    keymap: str = "emacs"
    groups: dict[str, bool] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    adopt: set[str] = field(default_factory=set)


def tilde(path: str | Path, home: str) -> str:
    p = str(path)
    if p == home:
        return "~"
    return "~/" + p[len(home) + 1:] if p.startswith(home + "/") else p


def expand(path: str, home: str) -> str:
    """An absolute path, with ~ for the home directory of the scan."""
    path = path.strip()
    if path == "~" or path.startswith("~/"):
        path = home + path[1:]
    return os.path.abspath(path)


def repo_error(repo: str, home: str) -> str | None:
    """Why a path cannot be the new repository, or None."""
    p = expand(repo, home)
    if not p.startswith(home + "/"):
        return f"Use a directory in {home}."
    # The path goes into nix/user.nix and into zsh lines in double quotes.
    if re.search(r'["$`\\\n]', p):
        return "Use a path without \", $, `, \\, or a line break."
    if os.path.lexists(p):
        return f"{tilde(p, home)} exists. Choose a new directory."
    if not os.path.isdir(os.path.dirname(p)):
        return f"{tilde(os.path.dirname(p), home)} does not exist."
    return None


class Planner:
    def __init__(self, scan: Scan, base: base_mod.Base | None = None,
                 check_syntax: Callable[[str, str], bool] | None = None):
        self.scan = scan
        self.base = base or base_mod.Base()
        # check_syntax(shell, text): True when the text parses. None: no check.
        self.check_syntax = check_syntax
        self._rc: dict[str, rcparse.Result] | None = None

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

    def npm_tools(self) -> list[str]:
        return [p.name for p in self.scan.npm if p.ok and p.name != "pnpm"]

    def runtime_lines(self) -> dict[str, list[str]]:
        """mise tool -> versions, default first, for the version managers mise can replace."""
        out: dict[str, list[str]] = {}
        for vm in self.scan.version_managers:
            if vm.name in ("sdkman", "jenv"):
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

    def rc_results(self) -> dict[str, rcparse.Result]:
        """The parsed shell files, by name. Layer files that were not read before are not parsed."""
        if self._rc is None:
            self._rc = {}
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
                    known = r.variables
                    self._rc[name] = r
        return self._rc

    def groups(self) -> list[Group]:
        s = self.scan
        formulae, _ = self.brew_split(s.formulae, "formula")
        casks, _ = self.brew_split(s.casks, "cask")
        rc = self.rc_results()
        carried = sum(r.statements for r in rc.values())
        out = [
            Group("formulae", "Homebrew formulae", "nix/brews.txt", formulae, True),
            Group("casks", "Homebrew casks", "nix/casks.txt", casks, True),
            Group("taps", "Homebrew taps", "nix/darwin.nix", [t.name for t in s.taps], True),
            Group("mas", "App Store apps", "nix/darwin.nix", [n for _, n in s.mas_apps], True),
            Group("npm", "npm, pnpm, and bun tools", "config/mise.toml", self.npm_tools(), True),
            Group("pipx", "pipx and uv tools", "config/mise.toml", [p.name for p in s.pipx if p.ok], True),
            Group("cargo", "cargo install tools", "config/mise.toml", [p.name for p in s.cargo if p.ok], False,
                  "off: mise builds them from source with cargo"),
            Group("go", "Go tools", "config/mise.toml", [p.name for p in s.go if p.ok], False,
                  "off: mise builds them from source with go"),
            Group("runtimes", "Runtime versions of version managers", "config/mise.toml",
                  [f"{t} {', '.join(v)}" for t, v in self.runtime_lines().items()], False,
                  "off: mise installs them; the base has Node, Python, and Ruby"),
            Group("shell", "Shell lines", "config/zsh, config/bash",
                  [f"~/{n}: {r.statements}" for n, r in rc.items() if r.statements], True, count=carried),
        ]
        if carried == 0:
            out[-1].items = []
        return [g for g in out if g.items]

    def file_items(self) -> list[FileItem]:
        s = self.scan
        items: list[FileItem] = []
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
            items.append(FileItem(c.target, options, default, note))
        if s.gitconfig:
            g = s.gitconfig
            if g.secrets:
                items.append(FileItem(".gitconfig", ["keep"], "keep",
                                      "has secrets: " + ", ".join(sorted({x.what for x in g.secrets})) +
                                      ". It stays out of the repository"))
            else:
                items.append(FileItem(".gitconfig", ["adopt", "keep"], "adopt", "Git reads it after the dotkit file"))
        if s.mise_config:
            if secrets.has_secret(s.mise_config):
                items.append(FileItem(".config/mise/config.toml", ["keep"], "keep",
                                      "has secrets. It stays out of the repository"))
            else:
                items.append(FileItem(".config/mise/config.toml", ["layer", "keep"], "layer",
                                      "your global mise tools; goes to config/mise.toml"))
        return items

    def defaults(self, repo: str) -> Choices:
        return Choices(
            repo=repo,
            groups={g.key: g.default for g in self.groups()},
            files={f.target: f.default for f in self.file_items()},
        )

    def adoptable(self) -> list[str]:
        return [o.rel for o in self.scan.other_config if o.adoptable]

    # --- The plan

    def build(self, c: Choices) -> Plan:
        return _Build(self, c).run()


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
    # before the first activation. write() lists them in .build/links-in-way.
    move_links: list[str] = field(default_factory=list)
    # Why the plan cannot be applied, for example ~/.config is a link. The
    # wizard shows them, and nothing is written.
    blockers: list[str] = field(default_factory=list)
    home: str = ""
    summary: list[str] = field(default_factory=list)
    report: str = ""


# Directories that hold much more than dotkit's files. A link there is not
# moved aside: links_in_the_way stops instead.
SHARED_DIRS = frozenset({".config", ".local", ".local/share", "Library", "Library/Preferences"})


def nix_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${") + '"'


def nix_name(s: str) -> str:
    return s if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_'-]*", s) else nix_string(s)


def toml_key(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def insert_tools(text: str, lines: list[str]) -> str:
    """Text of a mise config with the lines added at the end of its [tools] table."""
    if not lines:
        return text
    rows = text.splitlines()
    start = next((i for i, r in enumerate(rows) if re.match(r"^\s*\[tools\]\s*(#.*)?$", r)), None)
    if start is None:
        body = text.rstrip("\n")
        return (body + "\n\n" if body else "") + "[tools]\n" + "\n".join(lines) + "\n"
    end = next((i for i in range(start + 1, len(rows)) if re.match(r"^\s*\[", rows[i])), len(rows))
    while end > start + 1 and not rows[end - 1].strip():
        end -= 1
    rows[end:end] = lines
    return "\n".join(rows) + "\n"


class _Build:
    def __init__(self, planner: Planner, c: Choices):
        self.p = planner
        self.s = planner.scan
        self.base = planner.base
        self.c = c
        self.home = self.s.home
        repo = expand(c.repo, self.home)
        self.plan = Plan(Path(repo), c.keymap, home=self.home)
        self.r = Report()
        self.on = lambda key: c.groups.get(key, False)
        self.template = self.base.template
        self.mise_lines: list[str] = []
        self.brews: list[str] = []
        self.casks: list[str] = []

    def t(self, path: str | Path) -> str:
        return tilde(path, self.home)

    def tmpl(self, rel: str) -> str:
        return (self.template / rel).read_text(encoding="utf-8")

    def run(self) -> Plan:
        self.homebrew()
        self.app_store()
        self.tools()
        self.version_managers()
        self.shell()
        self.config_files()
        self.other_config()
        self.secret_files()
        self.misc()
        self.render()
        return self.plan

    # --- Homebrew and the App Store

    def homebrew(self) -> None:
        s, r = self.s, self.r
        if not s.brew:
            return
        for kind, names, key, dest in (("formula", s.formulae, "formulae", "nix/brews.txt"),
                                       ("cask", s.casks, "casks", "nix/casks.txt")):
            keep, redundant = self.p.brew_split(names, kind)
            flag = "--cask " if kind == "cask" else ""
            if keep and self.on(key):
                (self.brews if kind == "formula" else self.casks).extend(keep)
                r.carried(plural(len(keep), "Homebrew formula" if kind == "formula" else "Homebrew cask",
                                  "Homebrew formulae" if kind == "formula" else "Homebrew casks"), dest)
            elif keep:
                r.todo("Homebrew packages that are not in the lists",
                       "They stay installed. dotkit does not manage them.",
                       [f"`{n}`: `dotkit add {'cask' if kind == 'cask' else 'brew'}:{n}`" for n in keep])
            if redundant:
                r.todo("Homebrew copies of dotkit tools",
                       "The dotkit base has these tools, so they are not in your lists. The Homebrew copy "
                       "stays installed. Remove it when nothing else needs it; brew says when another "
                       "formula depends on it.",
                       [f"`{n}`: dotkit has {what}. `brew uninstall {flag}{n}`" for n, what in redundant.items()])
        if s.taps and self.on("taps"):
            r.carried(plural(len(s.taps), "Homebrew tap"), "homebrew.taps in nix/darwin.nix")
            custom = [t for t in s.taps if t.custom]
            if custom:
                r.todo("Homebrew taps", "These taps are not on GitHub, so the list has their URL. Check it.",
                       [f"`{t.name}`: {t.remote}" for t in custom])
        elif s.taps:
            r.todo("Homebrew taps", "They stay tapped. Add them to homebrew.taps in nix/darwin.nix for a new Mac.",
                   [f"`{t.name}`" for t in s.taps])
        if s.old_taps:
            r.todo("Homebrew taps", "Homebrew merged these taps into its own. Remove them.",
                   [f"`{t}`: `brew untap {t}`" for t in s.old_taps])

    def app_store(self) -> None:
        s, r = self.s, self.r
        if s.mas_apps and self.on("mas"):
            r.carried(plural(len(s.mas_apps), "App Store app"), "homebrew.masApps in nix/darwin.nix")
        elif s.mas_apps:
            r.todo("App Store apps", "Add the ones you want to homebrew.masApps in nix/darwin.nix.",
                   [f"`{n}`: `{nix_name(n)} = {i};`" for i, n in s.mas_apps])
        if s.store_apps:
            r.todo("App Store apps",
                   "These apps came from the App Store. dotkit installs App Store apps with mas, which was not "
                   "on this Mac, so the scan has no IDs. Run `dotkit add brew:mas`, then `mas list`, and add "
                   "each app to homebrew.masApps in nix/darwin.nix as `Name = ID;`.",
                   [f"{n}" for n in s.store_apps])

    # --- Tools of other package managers

    def user_mise_keys(self) -> set[str]:
        """The tools of the mise config of yours that goes into the repository."""
        if self.s.mise_config and self.c.files.get(".config/mise/config.toml") == "layer":
            return set(base_mod.mise_tools(self.s.mise_config))
        return set()

    def tools(self) -> None:
        s, r = self.s, self.r
        have = self.user_mise_keys()

        def add(key: str, comment: str) -> bool:
            if key in have or any(line.startswith(toml_key(key) + " ") for line in self.mise_lines):
                return False
            if comment and comment not in self.mise_lines:
                self.mise_lines.append(comment)
            self.mise_lines.append(f'{toml_key(key)} = "latest"')
            return True

        groups = (
            ("npm", s.npm, "npm", "# From npm, pnpm, and bun (global packages), see MIGRATION.md.",
             "npm uninstall -g {name}"),
            ("pipx", s.pipx, "pipx", "# From pipx and uv tool, see MIGRATION.md.", "{remove}"),
            ("cargo", s.cargo, "cargo", "# From cargo install, see MIGRATION.md.", "cargo uninstall {name}"),
            ("go", s.go, "go", "# From ~/go/bin, see MIGRATION.md.", "rm {where}"),
        )
        for key, pkgs, backend, comment, remove in groups:
            carried, manual, old = [], [], []
            for p in pkgs:
                if key == "npm" and p.name == "pnpm":
                    manual.append(f"`pnpm` ({p.where}): dotkit has no global pnpm; each project supplies its "
                                  "own, with mise or corepack. `npm uninstall -g pnpm` when you no longer need it.")
                    continue
                if not p.ok:
                    manual.append(f"`{p.name}` ({p.where}): {p.note}. Add it to config/mise.toml by hand.")
                    continue
                spec = f"{backend}:{p.name}"
                if self.on(key):
                    if add(spec, comment):
                        carried.append(p)
                        rm = remove.format(name=p.name, where=p.where,
                                           remove=("pipx uninstall " if p.where == "pipx" else "uv tool uninstall ")
                                           + p.name)
                        old.append(f"`{spec}`, was {p.version or 'installed'} in {p.where}. "
                                   f"Old copy: `{rm}`" + (f". Note: {p.note}" if p.note else ""))
                else:
                    manual.append(f"`{p.name}` ({p.where}): `mise use -g {spec}`")
            if carried:
                r.carried(plural(len(carried), f"{key} tool"), "config/mise.toml")
                r.todo("Tools that moved to mise",
                       "mise installs these at the end of setup (`mise install`). The old copies stay, and "
                       "they can come first in PATH. Remove each one when the mise copy works.",
                       old)
            if manual:
                why = {
                    "cargo": "mise builds cargo tools from source, so they are off by default.",
                    "go": "mise builds Go tools from source, so they are off by default.",
                }.get(key, "")
                r.todo("Tools that are not in mise", (why + " " if why else "") +
                       "They stay where they are. To add one to config/mise.toml:", manual)
            if key == "cargo" and carried and not s.has_cargo:
                r.todo("Tools that moved to mise", "", ["mise needs cargo for these: `mise use -g rust`."])
            if key == "go" and carried and not s.has_go:
                r.todo("Tools that moved to mise", "", ["mise needs go for these: `mise use -g go`."])

    def version_managers(self) -> None:
        s, r = self.s, self.r
        lines = self.p.runtime_lines()
        if lines and self.on("runtimes"):
            have = self.user_mise_keys()
            for tool, versions in lines.items():
                if tool in have:
                    continue
                if len(versions) == 1:
                    self.mise_lines.append(f'{toml_key(tool)} = "{versions[0]}"')
                else:
                    self.mise_lines.append(f"{toml_key(tool)} = [" + ", ".join(f'"{v}"' for v in versions) + "]")
            r.carried("runtime versions of " + ", ".join(vm.name for vm in s.version_managers if vm.versions),
                      "config/mise.toml")
        items = []
        for vm in s.version_managers:
            parts = []
            for tool, versions in vm.versions.items():
                d = vm.default.get(tool)
                parts.append(f"{tool} {', '.join(versions)}" + (f" (default {d})" if d else ""))
            what = "; ".join(parts) or "no versions"
            mise = ""
            if vm.name in ("sdkman", "jenv"):
                mise = ("dotkit has the Zulu 17 JDK (JAVA_HOME). For another JDK: `mise ls-remote java`, then "
                        "`mise use -g java@<version>`")
            else:
                tools = [t for t in vm.versions if t]
                mise = ", ".join(f"`mise use -g {t}@{(lines.get(t) or ['<version>'])[0]}`" for t in tools)
                if self.on("runtimes") and mise:
                    mise = "config/mise.toml has these versions now"
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
        # A copy: a failed syntax check below changes the results.
        results = copy.deepcopy(self.p.rc_results())
        on = self.on("shell")
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
            parts: list[str] = []
            secret_lines: list[str] = []
            for f, res in entries:
                if res is None:
                    continue
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
                    parts.append(f"# From ~/{f.name}. MIGRATION.md lists the lines that are not here.\n" + carried)
                    r.carried(plural(res.statements, "line") + f" of ~/{f.name}", f"{repo_file} (~/{layer})")
                if res.secrets and not on:
                    r.todo("Secrets", "", [f"~/{f.name}:{x.lines[0]}: `{x.name}` has a secret. You chose not to "
                                           "carry shell lines, so it is not carried." for x in res.secrets])
                elif res.secrets:
                    secret_lines.append(f"# From ~/{f.name}.")
                    secret_lines += [x.text for x in res.secrets]
                    r.todo("Secrets", "These lines have a secret, so they are not in Git. They are in a file in "
                                      "local/ of this repository, which Git ignores, and the repository file reads "
                                      "it. On a new Mac, copy the file by hand or use a password manager.",
                           [f"~/{f.name}:{x.lines[0]}: `{x.name}` -> local/{Path(repo_file).name}.{shell}"
                            for x in res.secrets])
                if not on:
                    lines = sum(1 for c in res.carried if c.kind != "comment")
                    if lines:
                        r.todo("Shell lines", "You chose not to carry shell lines. Copy the ones you need.",
                               [f"~/{f.name}: {lines} lines that could be carried"])
                self.findings(f.name, res)
            local_name = f"local/{Path(repo_file).name}.{shell}"
            if secret_lines:
                self.plan.local[local_name] = "\n".join(secret_lines) + "\n"
                rel = tilde(self.plan.repo / local_name, self.home).replace("~", "$HOME", 1)
                parts.insert(0, "# Lines with secrets, not in Git. See MIGRATION.md.\n"
                                f'if [[ -r "{rel}" ]]; then\n  source "{rel}"\nfi\n')
            header = ""
            if repo_file == "config/zsh/zshrc":
                header = self.tmpl("config/zsh/zshrc")
            else:
                header = {
                    ".zshenv": "# Your zsh lines for every zsh, linked to ~/.zshenv.local. The dotkit zshenv\n"
                               "# sources this file at the end. Keep it fast and quiet.\n",
                    ".zprofile": "# Your lines for zsh login shells, linked to ~/.zprofile.local. The dotkit\n"
                                 "# zprofile sources it, then puts the global tool directories first in PATH.\n",
                    ".bash_profile": "# shellcheck shell=bash\n# Your lines for bash login shells, linked to\n"
                                     "# ~/.bash_profile.local. The dotkit bash_profile sources it, then puts the\n"
                                     "# global tool directories first in PATH.\n",
                }[main]
            # A file for each layer that exists, also when it carries nothing: the
            # link moves an old layer file out of the way, so that dotkit does not
            # read a file that was not read before.
            if not (parts or main == ".zshrc" or layer in files):
                continue
            body = header + ("\n" + "\n".join(parts) if parts else "")
            self.plan.files[repo_file] = body
            if main != ".zshrc":
                self.plan.links[layer] = repo_file
        for f in unread:
            r.todo("Shell lines", "", [f"~/{f.name} was not read by ~/{f.name.removesuffix('.local')}, so nothing "
                                       f"of it is carried. dotkit links its own ~/{f.name} and renames yours to "
                                       f"~/{f.name}.before-dotkit. Copy what you need."])
        for name in s.other_shell_files:
            if name in self.c.adopt:
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
        shell = os.path.basename(s.login_shell or "")
        if s.login_shell and not s.login_shell.endswith("/sw/bin/zsh") and shell != "zsh":
            r.todo("Login shell",
                   f"Your login shell is {s.login_shell}. dotkit makes the zsh of Nix the login shell, so the "
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
        git_choice = self.c.files.get(".gitconfig", "adopt" if s.gitconfig else "")
        if s.gitconfig and s.gitconfig.secrets:
            git_choice = "keep"
        git_parts: list[tuple[str, Path]] = []
        for c in s.config_files:
            link = links[c.target]
            choice = self.c.files.get(c.target, "dotkit")
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
                self.plan.copies[link.repo_file] = c.path
                self.plan.links[link.layer] = link.repo_file
                r.carried(where, f"{link.repo_file} (~/{link.layer}, read after the dotkit file)")
                if c.target == ".config/ghostty/config":
                    r.todo("Config files", "", [f"{link.repo_file}: Ghostty reads it after the dotkit settings. "
                                                "Delete the lines that you do not need to set again."])
            elif choice == "replace":
                self.plan.copies[link.repo_file] = c.path
                self.plan.links[c.target] = link.repo_file
                r.carried(where, f"{link.repo_file} (instead of the dotkit file)")
                r.todo("Config files", "", [f"{link.repo_file} replaces the dotkit file "
                                            f"config/{Path(link.source).name}. Compare the two; the dotkit file "
                                            f"has settings that yours does not, for example "
                                            f"{_base_example(c.target)}."])
            elif choice == "keep":
                self.plan.links[c.target] = None
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
                self.plan.copies["config/gitconfig"] = git_parts[0][1]
            else:
                self.plan.files["config/gitconfig"] = "".join(
                    f"# From {w}.\n" + p.read_text(encoding="utf-8", errors="replace").rstrip("\n") + "\n\n" for w, p in git_parts)
            self.plan.links[".gitconfig"] = "config/gitconfig"
            r.carried(" and ".join(w for w, _ in git_parts), "config/gitconfig (~/.gitconfig, read after the "
                                                              "dotkit file)")
            r.todo("Config files", "", ["config/gitconfig: Git reads it after the dotkit settings "
                                        "(config/gitconfig of dotkit). Delete the settings that are the same. "
                                        "Your name and email are in ~/.gitconfig.user."])
        if s.ghostty_app_support:
            r.todo("Config files", "", ["~/Library/Application Support/com.mitchellh.ghostty/config: Ghostty reads "
                                        "it after ~/.config/ghostty/config, so it wins over the dotkit settings. "
                                        "Move its lines to config/ghostty.conf of this repository."])
        # The mise config.
        mise_choice = self.c.files.get(".config/mise/config.toml")
        if s.mise_config and secrets.has_secret(s.mise_config):
            mise_choice = "keep"
        text = self.tmpl("config/mise.toml")
        if s.mise_config and mise_choice == "layer":
            text = _merge_mise(text, s.mise_config.read_text(encoding="utf-8", errors="replace"))
            r.carried("~/.config/mise/config.toml", "config/mise.toml")
        elif s.mise_config:
            self.plan.links[".config/mise/config.toml"] = None
            r.todo("mise", "", ["~/.config/mise/config.toml stays in place, outside the repository, and dotkit "
                                "does not link config/mise.toml. `dotkit add` writes mise tools to it."])
        self.mise_text = text

    def other_config(self) -> None:
        r = self.r
        adopted, left, skipped = [], [], []
        for o in self.s.other_config:
            if o.rel in self.c.adopt and o.adoptable:
                name = o.rel.removeprefix(".config/").lstrip(".")
                dest = f"config/{name}" if o.rel.startswith(".config/") else f"config/home/{name}"
                self.plan.copies[dest] = Path(self.home, o.rel)
                self.plan.links[o.rel] = dest
                adopted.append(f"~/{o.rel} -> {dest}")
            elif o.adoptable:
                left.append(f"~/{o.rel}")
            elif o.reason not in ("empty", "a Home Manager link"):
                skipped.append(f"~/{o.rel}: {o.reason}")
        if adopted:
            r.carried(plural(len(adopted), "other config file or directory", "other config files and directories"),
                      "config/, linked with dotkit.links")
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

    def misc(self) -> None:
        for w in self.s.warnings:
            self.r.todo("Scan errors", "Part of the scan failed. Check these by hand.", [w])

    # --- Rendering the files

    def render(self) -> None:
        plan, s = self.plan, self.s
        self.guard_copies()
        repo_rel = tilde(plan.repo, self.home)
        user = (
            "# Your macOS account and this repository. dotkit's scripts/setup writes it.\n"
            "rec {\n"
            f"  name = {nix_string(s.user)};\n"
            '  home = "/Users/${name}";\n'
            f'  dotfiles = "${{home}}/{nix_string(repo_rel[2:])[1:-1]}";\n'
            "}\n"
        )
        plan.files["nix/user.nix"] = user

        def list_file(rel: str, names: list[str], label: str) -> None:
            if not names:
                return
            body = self.tmpl(rel).rstrip("\n") + "\n\n" + f"# {label}, see MIGRATION.md.\n" + \
                "".join(n + "\n" for n in names)
            plan.files[rel] = body

        list_file("nix/brews.txt", self.brews, f"From Homebrew on {s.hostname or 'this Mac'}")
        list_file("nix/casks.txt", self.casks, f"From Homebrew on {s.hostname or 'this Mac'}")

        darwin = self.tmpl("nix/darwin.nix")
        if s.taps and self.on("taps"):
            items = []
            for t in s.taps:
                if t.custom:
                    items.append(f"    {{\n      name = {nix_string(t.name)};\n"
                                 f"      clone_target = {nix_string(t.remote)};\n    }}\n")
                else:
                    items.append(f"    {nix_string(t.name)}\n")
            darwin = darwin.replace("  homebrew.taps = [ ];\n", "  homebrew.taps = [\n" + "".join(items) + "  ];\n")
        if s.mas_apps and self.on("mas"):
            apps: dict[str, int] = {}
            for i, n in s.mas_apps:
                apps.setdefault(n, i)
            body = "".join(f"    {nix_name(n)} = {i};\n" for n, i in sorted(apps.items()))
            darwin = darwin.replace("  homebrew.masApps = { };\n", "  homebrew.masApps = {\n" + body + "  };\n")
        if darwin != self.tmpl("nix/darwin.nix"):
            plan.files["nix/darwin.nix"] = darwin

        if plan.keymap == "vi":
            plan.files["nix/shell.nix"] = self.tmpl("nix/shell.nix").replace('# keymap = "vi";', 'keymap = "vi";')

        mise = insert_tools(self.mise_text, self.mise_lines)
        try:
            tomllib.loads(mise)
        except tomllib.TOMLDecodeError as e:
            self.r.todo("mise", f"config/mise.toml would not parse ({e}), so it has only the template. Add "
                                "these tools by hand:", [f"`{line}`" for line in self.mise_lines
                                                         if not line.startswith("#")])
            mise = self.tmpl("config/mise.toml")
        if mise != self.tmpl("config/mise.toml"):
            plan.files["config/mise.toml"] = mise

        links = {k: v for k, v in plan.links.items()}
        if links:
            home_nix = self.tmpl("nix/home.nix")
            rows = []
            for target, source in sorted(links.items(), key=lambda kv: (kv[1] is None, kv[0])):
                if source is None:
                    rows.append(f"    # Yours stays in place, see MIGRATION.md.\n    {nix_string(target)} = null;\n")
                else:
                    rows.append(f"    {nix_string(target)} = {nix_string(source)};\n")
            anchor = '    ".zshrc.local" = "config/zsh/zshrc";\n'
            home_nix = home_nix.replace(anchor, anchor + "".join(rows))
            plan.files["nix/home.nix"] = home_nix

        self.guard_files()
        if plan.local:
            plan.files[".gitignore"] = self.tmpl(".gitignore").rstrip("\n") + \
                "\n# Files with secrets, from the migration. See MIGRATION.md.\n/local/\n"

        self.links_in_the_way()
        plan.report = "".join(
            line if not secrets.line_secret(line) else "(a line with a secret was here)\n"
            for line in self.r.render(s, repo_rel).splitlines(keepends=True))
        plan.files["MIGRATION.md"] = plan.report
        plan.summary = self.summary(repo_rel)

    # The last checks that no secret goes into a file that Git tracks. The
    # rules above keep secrets out already. These drop a copy with a secret,
    # and a line with a secret in a generated file, and report it.

    def guard_copies(self) -> None:
        plan = self.plan
        for rel, src in list(plan.copies.items()):
            paths = [src] if not src.is_dir() else [p for p in src.rglob("*") if not p.is_dir()]
            # A link inside a directory is copied as a link. Anything else that
            # cannot be checked counts as a secret.
            if any(secrets.has_secret(p) for p in paths if ".DS_Store" not in p.relative_to(src).parts
                   and not (p.is_symlink() and p != src)):
                del plan.copies[rel]
                plan.files.pop(rel, None)
                for target, source in list(plan.links.items()):
                    if source == rel:
                        del plan.links[target]
                self.r.todo("Secrets", "", [f"{self.t(src)} has a secret, so it is not in the repository."])

    def guard_files(self) -> None:
        plan = self.plan
        for rel, text in list(plan.files.items()):
            if not secrets.scan_text(text):
                continue
            shell = "bash" if rel == "config/bash/bash_profile" else "zsh" if rel in {
                "config/zsh/zshrc", "config/zsh/zprofile", "config/zsh/zshenv"} else None
            if shell:
                # Never cut a shell statement in half. This guard is a fallback;
                # the parser normally routes the original statement to local/.
                kept, local = [], []
                for st in rcparse._statements(text):
                    if not secrets.scan_text(st.text):
                        kept.append(st.text)
                    elif not st.text.lstrip().startswith("#"):
                        local.append(st.text)
                if local:
                    name = f"local/{Path(rel).name}.{shell}"
                    plan.local[name] = plan.local.get(name, "") + "\n".join(local) + "\n"
                    source = tilde(plan.repo / name, self.home).replace("~", "$HOME", 1)
                    if not any(f'source "{source}"' in st for st in kept):
                        kept.append(f'if [[ -r "{source}" ]]; then\n  source "{source}"\nfi')
                plan.files[rel] = "\n".join(kept) + "\n"
            else:
                rows = text.splitlines()
                for start, end, logical in secrets.logical_lines(text):
                    if secrets.line_secret(logical):
                        rows[start - 1:end] = ["# (a line with a secret was here, see MIGRATION.md)"] * (end - start + 1)
                plan.files[rel] = "\n".join(rows) + "\n"
            self.r.todo("Secrets", "", [f"{rel}: a statement with a secret is not in Git."])

    def link_targets(self) -> list[str]:
        """The targets of the links of the new configuration, relative to the home directory."""
        p = self.plan
        targets = set(base_mod.BASE_TARGETS) | {k for k, v in p.links.items() if v is not None}
        targets |= {".zshrc.local", ".config/mise/config.toml"}
        targets -= {k for k, v in p.links.items() if v is None}
        return sorted(targets)

    def moved_aside(self) -> list[str]:
        """The files of yours that a link of the new configuration replaces."""
        out = []
        moved = self.plan.move_links
        for t in self.link_targets():
            path = Path(self.home, t)
            if any(t == m or t.startswith(m + "/") for m in moved):
                continue
            if (path.exists() or path.is_symlink()) and not scan_mod.is_managed(path):
                out.append(f"~/{t}")
        return out

    def links_in_the_way(self) -> None:
        """Links of yours at a target of the new configuration, or at a directory above one.

        A directory that another dotfiles manager folded into one link (GNU
        Stow links ~/.config/ghostty to ~/dotfiles/ghostty) would make Home
        Manager put its links into that other repository. Setup moves such a
        link aside, as it does with a link at the target itself. ~/.config and
        the other shared directories hold too much else to move: setup stops.
        """
        items = []
        targets = self.link_targets()
        for t in targets:
            parts = Path(t).parts
            for i in range(1, len(parts) + 1):
                rel = "/".join(parts[:i])
                path = Path(self.home, rel)
                if not path.is_symlink():
                    if not path.is_dir():
                        break
                    continue
                if scan_mod.is_managed(path):
                    break
                if i < len(parts) and rel in SHARED_DIRS:
                    msg = (f"~/{rel} is a link to {self.t(os.readlink(path))}. dotkit links files inside it, "
                           f"which would put them into that directory. Replace the link with a directory "
                           f"(for example: mv ~/{rel} ~/{rel}.old && mkdir ~/{rel}, then move back what you "
                           f"need), then run setup again.")
                    if msg not in self.plan.blockers:
                        self.plan.blockers.append(msg)
                    break
                if rel in self.plan.move_links:
                    break
                self.plan.move_links.append(rel)
                what = "the file it points to stays"
                if i < len(parts):
                    others = self._others(path, rel, targets)
                    what = "the directory it points to stays"
                    if others:
                        what += ", and these files of it are no longer at ~/" + rel + ": " + ", ".join(others) + \
                            ". Copy the ones you need into the repository"
                items.append(f"~/{rel} is a link to {self.t(os.readlink(path))}. Setup renames the link to "
                             f"~/{rel}.before-dotkit before the first apply; {what}.")
                break
        if items:
            self.r.todo("Config files", "", items)

    def _others(self, link: Path, rel: str, targets: list[str]) -> list[str]:
        """The files under a linked directory that no link of the new configuration replaces."""
        out = []
        for root, _dirs, files in os.walk(link, followlinks=False):
            for f in files:
                sub = os.path.relpath(os.path.join(root, f), link)
                if f"{rel}/{sub}" not in targets:
                    out.append(sub)
                if len(out) >= 10:
                    return sorted(out) + ["…"]
        return sorted(out)

    def summary(self, repo_rel: str) -> list[str]:
        p, s = self.plan, self.s
        out = []
        if p.blockers:
            out += ["Setup cannot continue:"] + [f"  {b}" for b in p.blockers] + [""]
        out += [f"Repository: {repo_rel} (new)", f"zsh keymap: {p.keymap}", ""]
        out += [f"{what} -> {where}" for what, where in self.r.carried_rows]
        if p.local:
            out.append("Lines with secrets -> " + ", ".join(sorted(p.local)) + " (Git ignores local/)")
        nulls = [t for t, v in p.links.items() if v is None]
        if nulls:
            out.append("Stay in place, not linked: " + ", ".join(f"~/{t}" for t in nulls))
        moved = self.moved_aside()
        if moved:
            out.append("Renamed to <name>.before-dotkit by the first apply: " + ", ".join(moved))
        if p.move_links:
            out.append("Links of yours, renamed to <name>.before-dotkit before the first apply: " +
                       ", ".join(f"~/{t}" for t in p.move_links))
        out += ["", f"MIGRATION.md: {self.r.count} items to check by hand.", "",
                "Nothing is installed or uninstalled now: Homebrew packages stay as they are.",
                "Setup then builds and applies the configuration. Files in the way of a dotkit",
                "link are renamed to <name>.before-dotkit. mise installs the tools of config/mise.toml."]
        if s.git_name or s.git_email:
            out.append(f"Git identity: {s.git_name} <{s.git_email}>, in ~/.gitconfig.user.")
        return out


def _code(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    if len(first) > 100:
        first = first[:99] + "…"
    more = text.strip().count("\n")
    first = first.replace("`", "'")
    return f"`{first}`" + (f" (+{more} lines)" if more else "")


def _merge_mise(template: str, own: str) -> str:
    """The template header, then the user's mise config as it was."""
    header = "".join(line + "\n" for line in template.splitlines() if line.startswith("#"))
    return header + "\n# From ~/.config/mise/config.toml.\n" + own.rstrip("\n") + "\n"


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
