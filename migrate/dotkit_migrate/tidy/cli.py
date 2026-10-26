"""The tidy mode of dotkit-migrate: scan, edit the plan, confirm, apply, and add to the lists (TIDY.md, section 2)."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import List, Optional, Tuple

from .. import base as base_mod
from .. import writer
from ..catalog import BREW_ENV
from ..context import Context, tilde
from ..declared import Declared
from ..docs import Docs
from ..migrators.appstore import add_app
from ..model import AdoptResult, Facts, Identity
from . import apps, plan
from .actions import Actions
from .clis import Tool
from .plan import Line

MakeContext = Callable[[Path, base_mod.Base], Context]
# edit(text, env) -> the edited text, or None when the editor fails. Tests give a fake.
Edit = Callable[[str, Mapping[str, str]], Optional[str]]


def info(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def edit_text(text: str, env: Mapping[str, str]) -> Optional[str]:
    """Open the text in $VISUAL or $EDITOR (default vi), through sh as Git does, so that `code --wait` works."""
    editor = env.get("VISUAL") or env.get("EDITOR") or "vi"
    fd, path = tempfile.mkstemp(prefix="dotkit-tidy-", suffix=".txt")  # mode 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        r = subprocess.run(["/bin/sh", "-c", editor + ' "$@"', editor, path], check=False)
        if r.returncode != 0:
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def declared_of(ctx: Context, repo: Path, nix_declared: dict) -> Declared:
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = ctx.env.get("USER", "")
    return Declared.for_repo(ctx.base, repo, nix_declared, Facts(Identity(str(ctx.home), user), ctx.base))


def changed(app: apps.App) -> bool:
    """The app is gone, or its bundle ID or version is not what the scan read."""
    if not os.path.isdir(app.path):
        return True
    return apps.read_info(Path(app.path))[:2] != (app.bundle_id, app.version)


def apply_tool(x: Line, tool: Tool, actions: Actions) -> Tuple[bool, str]:
    """Run one line of a command-line tool. (True, "") or (False, why, with the next step)."""
    if not os.path.lexists(tool.path):
        return False, "it is gone since the scan. Run dotkit tidy again."
    if x.verb == "uninstall":
        if actions.uninstall(tool.uninstall, tool.path_first):
            return True, ""
        return False, f"`{' '.join(tool.uninstall)}` failed; its output is above."
    ok, why = actions.trash(tool.path)
    return (True, "") if ok else (False, f"it could not move it to the Trash: {why}")


def apply_line(x: Line, app: apps.App, actions: Actions) -> Tuple[bool, str]:
    """Run one line of an app. (True, "") or (False, why, with the next step)."""
    if x.verb == "add":
        return True, ""
    if changed(app):
        return False, "it changed since the scan. Run dotkit tidy again."
    if x.verb in ("replace", "trash") and not actions.quit_app(app.bundle_id, app.path):
        return False, "it does not quit. Quit it, then run dotkit tidy again."
    if x.verb == "adopt":
        if actions.brew_install(x.token, "adopt"):
            return True, ""
        return False, (f"brew could not adopt it, often because the app differs from the cask {x.token}. To use the "
                       "cask, change the line to replace.")
    if x.verb == "replace":
        if actions.brew_install(x.token, "force"):
            return True, ""
        return False, f"brew could not install the cask {x.token}; its output is above."
    ok, why = actions.trash(app.path)
    return (True, "") if ok else (False, f"it could not move it to the Trash: {why}")


def write_lists(done: List[Line], t: apps.TidyFacts, ctx: Context, repo: Path, declared: Declared) -> List[str]:
    """Add the casks and App Store apps of the lines that worked to the lists. The lines for the output."""
    docs = Docs("adopt", ctx.base.template, repo, home=t.home, host=socket.gethostname().split(".")[0],
                by="dotkit tidy")
    txn = docs.begin("tidy")
    casks, mas = txn.list("nix/casks.txt"), txn.list("nix/mas.txt")
    by_path = {a.path: a for a in t.apps}
    out = []
    for x in done:
        if x.path not in by_path:  # a command-line tool: phase 2 writes nothing to the lists
            continue
        if x.token and x.verb != "trash":
            if declared.brew(x.token, "cask"):
                continue
            if casks.flagged(x.token):
                out.append(f"`{x.token}`: dotkit's secret check flags its line in nix/casks.txt, so it is not "
                           f"added. Add it by hand: `dotkit add cask:{x.token}`.")
            elif casks.add(x.token):
                out.append(f"nix/casks.txt: {x.token}")
        elif x.mas_id and not declared.mas_id(x.mas_id):
            app = by_path[x.path]
            name = t.mas_names.get(x.mas_id) or app.name[:-len(".app")]
            note, todo = add_app(x.mas_id, name, declared, mas)
            out += [s for s in (note, todo) if s]
            if not todo:
                out.append(f"nix/mas.txt: {x.mas_id} {name}")
    txn.commit()
    files, secret_lines = docs.render()
    assert not secret_lines, "tidy writes nothing to ~/.secrets"
    if files:
        writer.apply_edits(AdoptResult(repo, files, {rel: docs.read.get(rel) for rel in files}), repo)
    return out


def all_listed(ctx: Context, t: apps.TidyFacts, repo: Path, a: argparse.Namespace) -> bool:
    """Every installed cask is in the lists, as the repository has them now."""
    if not t.brew:
        return False
    out = ctx.runner.run([t.brew, "list", "--cask", "--full-name", "-1"], timeout=300, env=BREW_ENV)
    if out is None:
        return False
    declared = declared_of(ctx, repo, json.loads(a.declared) if a.declared else {})
    return all(declared.brew(n, "cask") for n in out.split())


def cmd_tidy(a: argparse.Namespace, make_context: MakeContext, make_actions: Callable[[str], Actions] = Actions,
             edit: Edit = edit_text) -> int:
    b = base_mod.Base(Path(a.base)) if a.base else base_mod.Base()
    ctx = make_context(Path(os.environ.get("HOME") or str(Path.home())), b)
    repo = Path(a.repo)
    declared = declared_of(ctx, repo, json.loads(a.declared) if a.declared else {})
    if not a.dry_run and not a.plan and not a.yes and not sys.stdin.isatty():
        info("dotkit tidy: the plan opens in an editor, which needs a terminal. Use -n to print it, or --plan FILE.")
        return 2
    info("Scanning the apps and command-line tools of this Mac. It changes nothing.")
    t = apps.scan(ctx)
    for w in t.warnings:
        info(w)
    lines, listed = plan.defaults(t, declared)
    if not lines:
        print("The lists have every app of this Mac that tidy knows, and tidy found no old copies of tools.")
        return 0
    text = plan.render(lines, t, listed)
    if a.dry_run:
        print(text, end="")
        todo = [x for x in lines if x.verb != "keep"]
        if todo:
            print("\nWith these verbs, tidy runs:")
            print("\n".join("  " + c for c in plan.commands(todo, t)))
        sys.stdout.flush()
        info("No change. Run dotkit tidy without -n to edit the plan.")
        return 0
    if a.plan:
        try:
            edited: Optional[str] = Path(a.plan).read_text(encoding="utf-8")
        except OSError as e:
            info(f"dotkit tidy: cannot read {a.plan} ({e.strerror or e}).")
            return 2
    elif a.yes:
        edited = text
    else:
        edited = edit(text, ctx.env)
        if edited is None:
            info("dotkit tidy: the editor failed. Nothing changed.")
            return 1
    if not any(s.strip() and not s.strip().startswith("#") for s in edited.splitlines()):
        info("Stopped: the plan is empty. Nothing changed.")
        return 1
    parsed, errors = plan.parse(edited, t)
    t.casks.update(apps.cask_infos(ctx, t, sorted({x.token for x in parsed if x.token and x.token not in t.casks})))
    errs, notes = plan.check(parsed, t)
    if errors + errs:
        for e in errors + errs:
            info(f"dotkit tidy: {e}")
        info("Nothing changed. Fix the plan, and run dotkit tidy again.")
        return 2
    for n in notes:
        info(n)
    todo = [x for x in parsed if x.verb != "keep"]
    if not todo:
        print("Every line is keep. Nothing changed.")
        return 0
    print("Tidy runs:")
    print("\n".join("  " + c for c in plan.commands(todo, t)))
    if any(x.verb in ("adopt", "replace", "add") for x in todo):
        print("Then it adds the casks and App Store apps to nix/casks.txt and nix/mas.txt.")
    sys.stdout.flush()
    if not a.yes:
        sys.stderr.write("Tidy now? [y/N] ")
        sys.stderr.flush()
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            info("dotkit tidy: nothing changed.")
            return 1
    actions = make_actions(t.brew)
    by_path = {x.path: x for x in t.apps}
    tools = {x.path: x for x in t.tools}
    done: List[Line] = []
    failed: List[str] = []
    stopped = False
    try:
        for x in todo:
            if x.path in tools:
                ok, why = apply_tool(x, tools[x.path], actions)
            else:
                ok, why = apply_line(x, by_path[x.path], actions)
            if ok:
                done.append(x)
            else:
                failed.append(f"{tilde(x.path, t.home)}: {why}")
                info(f"dotkit tidy: {failed[-1]}")
    except KeyboardInterrupt:
        stopped = True
        info("dotkit tidy: stopped. The lists get the lines that worked until now.")
    try:
        added = write_lists(done, t, ctx, repo, declared)
    except writer.AdoptStopped:
        info("dotkit tidy: a list changed while tidy ran, so tidy did not change the lists. Add the casks and App "
             "Store apps of the lines above with dotkit add.")
        return 1
    except (writer.SecretInRepository, OSError) as e:
        info(f"dotkit tidy: could not change the lists ({e}). Add the casks and App Store apps of the lines above "
             "with dotkit add.")
        return 1
    trashed = [tilde(x.path, t.home) for x in done if x.verb == "trash"]
    removed = [f"{x.thing} ({tilde(x.path, t.home)})" for x in done if x.verb == "uninstall"]
    if added:
        print("Added to the lists:")
        print("\n".join("  " + s for s in added))
    if removed:
        print("Uninstalled:")
        print("\n".join("  " + s for s in removed))
    if trashed:
        print("In the Trash:")
        print("\n".join("  " + s for s in trashed))
    if failed:
        print("Not done:")
        print("\n".join("  " + s for s in failed))
    if any(x.verb in ("adopt", "replace") for x in done):
        info("The casks are installed already; the next dotkit apply records them.")
    if done and all_listed(ctx, t, repo, a):
        info("The lists have every installed cask now. With `homebrew.onActivation.cleanup = \"uninstall\"` in "
             "nix/darwin.nix, dotkit apply uninstalls a cask that the lists do not have; `dotkit apply -n` shows "
             "what it would uninstall.")
    return 130 if stopped else 1 if failed else 0
