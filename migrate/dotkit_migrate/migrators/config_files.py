"""Your config files where dotkit links its own: layer, replace, the dotkit file, or keep yours.

Also ~/.gitconfig (adopted into config/gitconfig, merged with
~/.config/git/config when both are carried) and your mise config, which
becomes the base of config/mise.toml when it is carried as "layer". A file
with a secret never goes into the repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import base as base_mod
from .. import secrets
from .._compat import tomllib
from ..context import Context, is_managed, read_text, tilde
from ..docs import Txn, toml_value
from ..model import Choices, Facts, Item, Page
from . import Migrator, and_list

OPTION_TEXT = {
    "layer": "yours, read after the dotkit file",
    "replace": "yours, instead of the dotkit file",
    "dotkit": "the dotkit file; yours is renamed to <name>.before-dotkit",
    "keep": "leave yours in place, outside the repository",
    "adopt": "yours, in the repository",
}

MISE_CONFIG = ".config/mise/config.toml"


@dataclass
class ConfigFile:
    target: str  # relative to the home directory
    path: Path
    secrets: List[secrets.Secret] = field(default_factory=list)
    same_as_base: bool = False
    # Your layer file of the base link (~/.config/ghostty/config.local) exists already.
    layer_exists: bool = False


@dataclass
class ConfigFacts:
    files: List[ConfigFile] = field(default_factory=list)  # at the targets of base.LINKS
    gitconfig: Optional[ConfigFile] = None
    mise_config: Optional[ConfigFile] = None  # ~/.config/mise/config.toml
    mise_tools: Dict[str, object] = field(default_factory=dict)  # its [tools], parsed
    mise_settings: Dict[str, object] = field(default_factory=dict)  # its [settings], parsed
    ghostty_app_support: bool = False


def facts_of(f: Facts) -> Optional[ConfigFacts]:
    return f.get(ConfigFiles.key)


def _secrets_of(p: Path) -> List[secrets.Secret]:
    """The secrets of a file. A file that cannot be checked counts as one with a secret."""
    try:
        return secrets.scan_file(p)
    except secrets.Unscannable as e:
        return [secrets.Secret(0, f"a file that cannot be checked ({e})")]


def _mise_layered(cf: Optional[ConfigFacts], c: Choices) -> bool:
    """Your mise config goes into the repository: config/mise.toml is your file."""
    return bool(cf and cf.mise_config and c.option(f"file:{MISE_CONFIG}") == "layer" and not cf.mise_config.secrets)


# The intro of the "mise" block that lists the selected tools when your mise config is kept in place.
KEPT_INTRO = ("Your ~/.config/mise/config.toml stays in place, so mise does not read config/mise.toml of this "
              "repository. To get these tools from mise, add them to your config:")


def mise_target(f: Facts, c: Choices) -> Optional[str]:
    """Where the tools of the tool migrators go, or None without a mise config of yours.

    "repository": yours is carried as "layer", so it is config/mise.toml,
    which mise reads. "kept": your ~/.config/mise/config.toml stays
    in place, config/mise.toml is not linked, and mise never reads it, so the
    tools are to-dos. None: you have no mise config; the tools go to
    config/mise.toml.
    """
    cf = facts_of(f)
    if cf is None or cf.mise_config is None:
        return None
    return "repository" if _mise_layered(cf, c) else "kept"


def mise_dest(f: Facts, c: Choices) -> str:
    """The mise config that mise reads, for report text: yours when it is kept in place."""
    return "~/.config/mise/config.toml" if mise_target(f, c) == "kept" else "config/mise.toml"


def _read_mise(path: Path) -> Tuple[Dict[str, object], Dict[str, object]]:
    """The [tools] and [settings] of your mise config; empty when it cannot be read or parsed."""
    try:
        data = tomllib.loads(_text(path))
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    tools, settings = data.get("tools"), data.get("settings")
    return tools if isinstance(tools, dict) else {}, settings if isinstance(settings, dict) else {}


# Settings that are lists of mise: yours replaces dotkit's whole list, so it
# becomes dotkit's list plus yours.
IDIOMATIC = ("idiomatic_version_file_enable_tools", "idiomatic_version_file_disable_files")


def _toml(v: object) -> str:
    """A value for report text: backticked TOML, as MiseDoc writes it, ready to paste."""

    def plain(x: object) -> bool:
        return isinstance(x, (bool, int, str)) or (isinstance(x, list) and all(plain(y) for y in x))

    return "`" + (toml_value(v) if plain(v) else str(v)) + "`"


def _names(v: object) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _idiomatic(f: Facts, txn: Txn, settings: Dict[str, object], layered: bool) -> None:
    """dotkit's idiomatic settings plus yours, for each one that your mise config sets.

    A key that your config does not set is not written: a copy would freeze
    dotkit's list. Carried as "layer", the line of config/mise.toml is
    replaced (after set_base), and a to-do says so; kept in place, your file
    is never edited. What cannot be written is a to-do.
    """
    r = txn.report
    for key in IDIOMATIC:
        if key not in settings:
            continue
        yours = settings[key]
        base_val = f.base.mise_settings.get(key, [])
        if not _names(yours):
            r.todo("mise", "", [f"your mise config sets `{key}` to {_toml(yours)}, which is not a list of names. "
                                f"dotkit's value is {_toml(base_val)}. Check it."])
            continue
        base_names = list(base_val) if _names(base_val) else []
        # Lists of names that mise allows: the order does not matter.
        if set(base_names) <= set(yours):
            continue
        union: List[str] = []
        for v in base_names + yours:
            if v not in union:
                union.append(v)
        if not layered:
            r.todo("mise", "", [f"~/.config/mise/config.toml sets `{key}`, which replaces dotkit's "
                                f"{_toml(base_val)}. Set it to {_toml(union)}."])
        elif txn.mise().replace_setting(key, union, expect=yours):
            r.todo("mise", "", [f"config/mise.toml: `{key}` is now {_toml(union)}, dotkit's list and yours, "
                                f"because a list in your config replaces dotkit's (yours was {_toml(yours)}). "
                                "Check it."])
        else:
            r.todo("mise", "", [f"config/mise.toml sets `{key}`, which replaces dotkit's {_toml(base_val)}. "
                                f"Set it to {_toml(union)}."])


def _choice(x: ConfigFile, c: Choices) -> str:
    choice = c.option(f"file:{x.target}") or "dotkit"
    if x.secrets and choice not in ("keep", "dotkit"):
        choice = "keep"
    return choice


def _git_choice(cf: ConfigFacts, c: Choices) -> str:
    if cf.gitconfig and cf.gitconfig.secrets:
        return "keep"
    return c.option("file:.gitconfig") or ("adopt" if cf.gitconfig else "")


def _git_parts(f: Facts, cf: ConfigFacts, c: Choices) -> List[Tuple[str, Path]]:
    """The files that go to config/gitconfig, in order: ~/.config/git/config as "layer", and ~/.gitconfig."""
    home = f.identity.home
    git_choice = _git_choice(cf, c)
    parts = []
    for x in cf.files:
        if x.target == ".config/git/config" and _choice(x, c) == "layer" and not (cf.gitconfig and
                                                                                  git_choice != "adopt"):
            parts.append((tilde(x.path, home), x.path))
    if cf.gitconfig and git_choice == "adopt":
        parts.append((tilde(cf.gitconfig.path, home), cf.gitconfig.path))
    return parts


def merged_sources(f: Facts, c: Choices) -> Dict[str, str]:
    """Repository files that are merged from several of yours, and what they are from."""
    cf = facts_of(f)
    if cf is None:
        return {}
    parts = _git_parts(f, cf, c)
    return {"config/gitconfig": " and ".join(w for w, _ in parts)} if len(parts) > 1 else {}


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _file_item(target: str, options: List[str], default: str, note: str) -> Item:
    return Item(f"file:{target}", f"~/{target}", note, options=tuple(options), default_option=default,
                option_text={o: OPTION_TEXT[o] for o in options})


class ConfigFiles(Migrator):
    key = "config_files"
    title = "Config files"
    sections = ("Config files", "mise")

    def manual_steps(self, ctx: Context) -> str:
        """Section 7.5: what the template does without this migrator, from base.LINKS."""
        git = next(link for link in base_mod.LINKS if link.target == ".config/git/config")
        return ("None of your config files is carried or kept in place. dotkit links its own files at "
                f"{and_list([f'~/{link.target}' for link in base_mod.LINKS])}, and links config/mise.toml of "
                f"this repository as ~/{MISE_CONFIG}. The first apply renames your files at these paths to "
                "<name>.before-dotkit. They stay on disk, outside the repository, and the tools no longer read "
                "them. The tools of your old mise config are not in config/mise.toml. If a file with a login, "
                "such as ~/.npmrc with a token, was renamed, log in again or copy the token by hand; do not put "
                f"it into Git. ~/{git.layer} stays in place, and Git still reads it after the dotkit settings. "
                "Copy the settings you need from the .before-dotkit files.")

    def scan(self, ctx: Context) -> ConfigFacts:
        cf = ConfigFacts()
        for link in base_mod.LINKS:
            p = ctx.home / link.target
            if not p.is_file() or is_managed(p):
                continue
            base_file = ctx.base.file(link.source)
            same = read_text(p) == read_text(base_file) if base_file.is_file() else False
            layer_exists = bool(link.layer) and (ctx.home / link.layer).exists()
            cf.files.append(ConfigFile(link.target, p, _secrets_of(p), same, layer_exists))
        p = ctx.home / ".gitconfig"
        if p.is_file() and not is_managed(p):
            cf.gitconfig = ConfigFile(".gitconfig", p, _secrets_of(p))
        p = ctx.home / MISE_CONFIG
        if p.is_file() and not is_managed(p):
            cf.mise_config = ConfigFile(MISE_CONFIG, p, _secrets_of(p))
            cf.mise_tools, cf.mise_settings = _read_mise(p)
        cf.ghostty_app_support = (ctx.home / "Library/Application Support/com.mitchellh.ghostty/config").is_file()
        return cf

    def pages(self, f: Facts) -> List[Page]:
        cf = facts_of(f)
        if cf is None:
            return []
        items: List[Item] = []
        for x in cf.files:
            link = next(link for link in base_mod.LINKS if link.target == x.target)
            options = []
            if link.layer and not (x.layer_exists and link.layer != ".gitconfig"):
                options.append("layer")
            if x.target != ".config/git/config":
                options.append("replace")
            options += ["dotkit", "keep"]
            note = ""
            if x.secrets:
                # A file with a secret never goes into the repository.
                options, default = ["keep", "dotkit"], "keep"
                note = "has secrets: " + ", ".join(sorted({s.what for s in x.secrets})) + \
                    ". It stays out of the repository"
            elif x.same_as_base:
                default, note = "dotkit", "the same as the dotkit file"
            else:
                default = options[0]
            items.append(_file_item(x.target, options, default, note))
        if cf.gitconfig:
            g = cf.gitconfig
            if g.secrets:
                items.append(_file_item(".gitconfig", ["keep"], "keep",
                                        "has secrets: " + ", ".join(sorted({s.what for s in g.secrets})) +
                                        ". It stays out of the repository"))
            else:
                items.append(_file_item(".gitconfig", ["adopt", "keep"], "adopt",
                                        "Git reads it after the dotkit file"))
        if cf.mise_config:
            if cf.mise_config.secrets:
                items.append(_file_item(MISE_CONFIG, ["keep"], "keep",
                                        "has secrets. It stays out of the repository"))
            else:
                items.append(_file_item(MISE_CONFIG, ["layer", "keep"], "layer",
                                        "your global mise tools; goes to config/mise.toml"))
        return [Page("files", "Your config files", "config/", "dotkit has its own file for each of these. "
                     "Choose which one each tool reads.", "choices", items)]

    def plan(self, f: Facts, c: Choices, txn: Txn) -> None:
        cf, r = facts_of(f), txn.report
        if cf is None:
            return
        home = f.identity.home
        layered = _mise_layered(cf, c)
        if layered:
            # Your mise config is the base of config/mise.toml. The tool migrators skipped its tools.
            txn.mise().set_base(_text(cf.mise_config.path))
            # Settings come after set_base.
            _idiomatic(f, txn, cf.mise_settings, layered=True)
        links = {link.target: link for link in base_mod.LINKS}
        git_choice = _git_choice(cf, c)
        git_parts = _git_parts(f, cf, c)
        for x in cf.files:
            link = links[x.target]
            choice = _choice(x, c)
            where = tilde(x.path, home)
            if x.target == ".config/git/config" and choice == "layer":
                if cf.gitconfig and git_choice != "adopt":
                    choice = "dotkit"
                    r.todo("Config files", "", [f"{where}: dotkit links its own file here, and yours is renamed to "
                                                f"{where}.before-dotkit. Copy its settings to ~/.gitconfig."])
                else:
                    continue  # in git_parts
            if choice == "layer":
                txn.copies.add(link.repo_file, x.path)
                txn.links().link(link.layer, link.repo_file)
                r.carried(where, f"{link.repo_file} (~/{link.layer}, read after the dotkit file)")
                if x.target == ".config/ghostty/config":
                    r.todo("Config files", "", [f"{link.repo_file}: Ghostty reads it after the dotkit settings. "
                                                "Delete the lines that you do not need to set again."])
            elif choice == "replace":
                txn.copies.add(link.repo_file, x.path)
                txn.links().link(x.target, link.repo_file)
                r.carried(where, f"{link.repo_file} (instead of the dotkit file)")
                r.todo("Config files", "", [f"{link.repo_file} replaces the dotkit file "
                                            f"config/{Path(link.source).name}. Compare the two; the dotkit file "
                                            f"has settings that yours does not, for example "
                                            f"{_base_example(x.target)}."])
            elif choice == "keep":
                txn.links().link(x.target, None)
                why = "it has secrets (" + ", ".join(sorted({s.what for s in x.secrets})) + ")" if x.secrets else \
                    "you chose to keep it"
                r.todo("Config files", "", [f"{where} stays in place and outside the repository, because {why}. "
                                            f"dotkit does not link its own file there. To get the dotkit "
                                            f"settings, copy them from config/{Path(link.source).name} of dotkit."])
            else:
                if not x.same_as_base:
                    r.todo("Config files", "", [f"{where} is replaced by the dotkit file and renamed to "
                                                f"{where}.before-dotkit. Copy the settings you miss to "
                                                + (f"~/{link.layer}" if link.layer else "your own copy") + "."])
        if cf.gitconfig and git_choice != "adopt":
            why = "it has secrets" if cf.gitconfig.secrets else "you chose to keep it"
            r.todo("Config files", "", [f"{tilde(cf.gitconfig.path, home)} stays in place, because {why}. Git "
                                        "reads it after the dotkit file, as before."])
        if git_parts:
            if len(git_parts) == 1:
                txn.copies.add("config/gitconfig", git_parts[0][1])
            else:
                txn.file("config/gitconfig").set("".join(
                    f"# From {w}.\n" + _text(p).rstrip("\n") + "\n\n" for w, p in git_parts))
            txn.links().link(".gitconfig", "config/gitconfig")
            r.carried(" and ".join(w for w, _ in git_parts), "config/gitconfig (~/.gitconfig, read after the "
                                                              "dotkit file)")
            r.todo("Config files", "", ["config/gitconfig: Git reads it after the dotkit settings "
                                        "(config/gitconfig of dotkit). Delete the settings that are the same. "
                                        "Your name and email are in ~/.gitconfig.user."])
        if cf.ghostty_app_support:
            r.todo("Config files", "", ["~/Library/Application Support/com.mitchellh.ghostty/config: Ghostty reads "
                                        "it after ~/.config/ghostty/config, so it wins over the dotkit settings. "
                                        "Move its lines to config/ghostty.conf of this repository."])
        if layered:
            r.carried("~/.config/mise/config.toml", "config/mise.toml")
        elif cf.mise_config:
            txn.links().link(MISE_CONFIG, None)
            r.todo("mise", "", ["~/.config/mise/config.toml stays in place, outside the repository, and dotkit "
                                "does not link config/mise.toml. `dotkit add` writes mise tools to it."])
            _idiomatic(f, txn, cf.mise_settings, layered=False)


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
