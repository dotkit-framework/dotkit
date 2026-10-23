"""Turn a scan and the choices into a plan: the files of the new repository and the report.

Safe defaults: Homebrew packages stay in Homebrew and go to the lists, tools
of other package managers go to mise, safe shell lines are carried, and
config files stay yours. Nothing is installed or uninstalled. Secrets never
go into a file that Git tracks.
"""

from __future__ import annotations

from pathlib import Path

from . import base as base_mod
from . import secrets
from . import docs as docs_mod
from .model import Choices, Item, Page
from .report import plural
from .context import tilde
from .scan import Scan

# --- The choices

OPTION_TEXT = {
    "layer": "yours, read after the dotkit file",
    "replace": "yours, instead of the dotkit file",
    "dotkit": "the dotkit file; yours is renamed to <name>.before-dotkit",
    "keep": "leave yours in place, outside the repository",
    "adopt": "yours, in the repository",
}

def _file_item(target: str, options: list[str], default: str, note: str) -> Item:
    return Item(f"file:{target}", f"~/{target}", note, options=tuple(options), default_option=default,
                option_text={o: OPTION_TEXT[o] for o in options})


class Planner:
    def __init__(self, scan: Scan, base: base_mod.Base | None = None):
        self.scan = scan
        self.base = base or base_mod.Base()

    # --- What the wizard shows

    def pages(self) -> list[Page]:
        """The questions, one page per source, in the order of the plan. Pages without items are left out."""
        s = self.scan
        out: list[Page] = []

        def checklist(key: str, title: str, dest: str, intro: str, items: list[Item], note: str = "") -> None:
            unique: dict[str, Item] = {}
            for i in items:  # a package in two places is one item
                unique.setdefault(i.id, i)
            out.append(Page(key, title, dest, intro, "checklist", list(unique.values()), note))

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

    def plan(self, c: Choices, txn: docs_mod.Txn) -> None:
        _Build(self, c, txn).run()


class _Build:
    def __init__(self, planner: Planner, c: Choices, txn: docs_mod.Txn):
        self.p = planner
        self.s = planner.scan
        self.base = planner.base
        self.c = c
        self.home = self.s.home
        # The planning steps write only through this transaction.
        self.txn = txn
        self.r = self.txn.report
        # Repository files that are merged from several of yours, and what they are from.
        self.merged: dict[str, str] = {}

    def t(self, path: str | Path) -> str:
        return tilde(path, self.home)

    def picked(self, page: str, item_id: str) -> bool:
        return self.c.selected(page, item_id)

    def run(self) -> None:
        # Your mise config first: the tools that it has are not added again.
        if self.mise_layered():
            self.txn.mise().set_base(self.s.mise_config.read_text(encoding="utf-8", errors="replace"))
        self.config_files()
        self.other_config()
        self.secret_files()

    # --- Tools of other package managers

    def mise_layered(self) -> bool:
        """Your mise config goes into the repository: config/mise.toml is your file."""
        s = self.s
        return bool(s.mise_config and self.c.option("file:.config/mise/config.toml") == "layer"
                    and not secrets.has_secret(s.mise_config))

    # --- Shell files

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
