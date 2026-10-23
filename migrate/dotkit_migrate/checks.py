"""The checks of a plan against this Mac: links in the way, files moved aside, blockers, and the summary.

They run whatever the planning steps did, on the links of the plan and on
the file system. They read and never write.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import base as base_mod
from .context import is_managed, tilde
from .docs import Docs, ReportDoc
from .model import Plan

# Directories that hold much more than dotkit's files. A link there is not
# moved aside: links_in_the_way stops instead.
SHARED_DIRS = frozenset({".config", ".local", ".local/share", "Library", "Library/Preferences"})


def check(docs: Docs, plan: Plan) -> None:
    """The links in the way, as to-dos and plan.move_links; the blockers of the report as plan.blockers."""
    links_in_the_way(docs, plan)
    plan.blockers = list(docs.report.blockers)


def link_targets(plan: Plan) -> list[str]:
    """The targets of the links of the new configuration, relative to the home directory."""
    targets = set(base_mod.BASE_TARGETS) | {k for k, v in plan.links.items() if v is not None}
    targets |= {".zshrc.local", ".config/mise/config.toml"}
    targets -= {k for k, v in plan.links.items() if v is None}
    return sorted(targets)


def moved_aside(plan: Plan) -> list[str]:
    """The files of yours that a link of the new configuration replaces."""
    out = []
    moved = plan.move_links
    for t in link_targets(plan):
        path = Path(plan.home, t)
        if any(t == m or t.startswith(m + "/") for m in moved):
            continue
        if (path.exists() or path.is_symlink()) and not is_managed(path):
            out.append(f"~/{t}")
    return out


def links_in_the_way(docs: Docs, plan: Plan) -> None:
    """Links of yours at a target of the new configuration, or at a directory above one.

    A directory that another dotfiles manager folded into one link (GNU
    Stow links ~/.config/ghostty to ~/dotfiles/ghostty) would make Home
    Manager put its links into that other repository. Setup moves such a
    link aside, as it does with a link at the target itself. ~/.config and
    the other shared directories hold too much else to move: setup stops.
    """
    items = []
    txn = docs.begin("checks")
    targets = link_targets(plan)
    for t in targets:
        parts = Path(t).parts
        for i in range(1, len(parts) + 1):
            rel = "/".join(parts[:i])
            path = Path(plan.home, rel)
            if not path.is_symlink():
                if not path.is_dir():
                    break
                continue
            if is_managed(path):
                break
            if i < len(parts) and rel in SHARED_DIRS:
                msg = (f"~/{rel} is a link to {tilde(os.readlink(path), plan.home)}. dotkit links files inside "
                       f"it, which would put them into that directory. Replace the link with a directory "
                       f"(for example: mv ~/{rel} ~/{rel}.old && mkdir ~/{rel}, then move back what you "
                       f"need), then run setup again.")
                txn.report.blocker(msg)
                break
            if rel in plan.move_links:
                break
            plan.move_links.append(rel)
            what = "the file it points to stays"
            if i < len(parts):
                others = _others(path, rel, targets)
                what = "the directory it points to stays"
                if others:
                    what += ", and these files of it are no longer at ~/" + rel + ": " + ", ".join(others) + \
                        ". Copy the ones you need into the repository"
            items.append(f"~/{rel} is a link to {tilde(os.readlink(path), plan.home)}. Setup renames the link "
                         f"to ~/{rel}.before-dotkit before the first apply; {what}.")
            break
    if items:
        txn.report.todo("Config files", "", items)
    txn.commit()


def _others(link: Path, rel: str, targets: list[str]) -> list[str]:
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


def summary(plan: Plan, report: ReportDoc, git_name: str = "", git_email: str = "",
            mise_dest: str = "config/mise.toml") -> list[str]:
    """What the review screen, --dry-run, and the plain prompts show. writer.mask_lines masks it.

    mise_dest is the mise config that mise reads (config_files.mise_dest).
    """
    p = plan
    repo_rel = tilde(p.repo, p.home)
    out = []
    if p.blockers:
        out += ["Setup cannot continue:"] + [f"  {b}" for b in p.blockers] + [""]
    out += [f"Repository: {repo_rel} (new)", f"zsh keymap: {p.keymap}", ""]
    out += [f"{what} -> {where}" for what, where in report.carried_rows]
    if p.local:
        out.append("Lines with secrets -> " + ", ".join(sorted(p.local)) + " (Git ignores local/)")
    nulls = [t for t, v in p.links.items() if v is None]
    if nulls:
        out.append("Stay in place, not linked: " + ", ".join(f"~/{t}" for t in nulls))
    moved = moved_aside(p)
    if moved:
        out.append("Renamed to <name>.before-dotkit by the first apply: " + ", ".join(moved))
    if p.move_links:
        out.append("Links of yours, renamed to <name>.before-dotkit before the first apply: " +
                   ", ".join(f"~/{t}" for t in p.move_links))
    out += ["", f"MIGRATION.md: {report.count} items to check by hand.", "",
            "Nothing is installed or uninstalled now: Homebrew packages stay as they are.",
            "Setup then builds and applies the configuration. Files in the way of a dotkit"]
    if mise_dest == "config/mise.toml":
        out.append("link are renamed to <name>.before-dotkit. mise installs the tools of config/mise.toml.")
    else:
        out += [f"link are renamed to <name>.before-dotkit. mise reads your {mise_dest},",
                "which stays in place; config/mise.toml of this repository is not read."]
    if git_name or git_email:
        out.append(f"Git identity: {git_name} <{git_email}>, in ~/.gitconfig.user.")
    return out
