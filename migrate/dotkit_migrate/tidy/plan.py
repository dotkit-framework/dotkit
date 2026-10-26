"""The plan of dotkit tidy: the default verbs, the text, and the check of an edited plan (TIDY.md, sections 4, 5,
and 8.1).

Pure: it reads the facts of the scan and the lists, and runs nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..context import expand, shell_path, tilde
from ..declared import Declared
from .apps import App, TidyFacts
from .clis import PACKAGES, Tool

VERBS = ("adopt", "replace", "add", "uninstall", "keep", "trash")
# The prefixes of a thing: cask:<token>, mas:<id>, and the kinds of tools.
THINGS = ("cask", "mas") + PACKAGES + ("go",)
PATH_WIDTH = 40

HEADER = """\
# dotkit tidy: {count}. Change a verb, save, and close the editor.
# An empty file stops tidy, and nothing changes. A line that you delete is keep.
#
# adopt      the cask takes over the app in place.
# replace    quit the app, then install the cask over it. The app data stays.
# add        the app is from a cask or the App Store already: add it to the list.
# uninstall  remove an old copy of a tool with its package manager.
# keep       change nothing.
# trash      move the app, the file, or the directory to the Trash. App data stays in ~/Library.
#
# adopt and replace take a cask, and you can change it: adopt cask:<token> <path>.
"""
TOOLS = ("# Command-line tools: old copies of tools that dotkit has, and files that no package manager knows. "
         "dotkit migrate\n# adds the tools that the lists do not have.")
CHECK_REFERENCES = ("Before trash, check that nothing points into it: LaunchAgents, IDE interpreter settings, "
                    "git hooks")


@dataclass
class Line:
    verb: str
    path: str  # absolute
    thing: str = ""  # cask:<token>, mas:<id>, or a tool: npm:<package>
    note: str = ""  # the comment
    number: int = 0  # the line number in the edited plan; 0 for a default line

    @property
    def token(self) -> str:
        return self.thing[len("cask:"):] if self.thing.startswith("cask:") else ""

    @property
    def mas_id(self) -> int:
        rest = self.thing[len("mas:"):] if self.thing.startswith("mas:") else ""
        return int(rest) if rest.isdigit() else 0


def numbers(version: str) -> List[int]:
    """The numbers of a version, without zeros at the end: "7.2.2 (88465)" -> [7, 2, 2, 88465]."""
    out = [int(x) for x in re.findall(r"\d+", version)]
    while out and out[-1] == 0:
        out.pop()
    return out


def compare(app: str, cask: str) -> Optional[int]:
    """-1, 0, or 1 when the version of the app is older, the same, or newer than the cask's; None: unknown.

    The cask version is the part before the first comma ("2.2.1,430"). Homebrew's own check at
    `brew install --adopt` compares the Info.plist of the download, which the scan cannot see.
    """
    a, c = numbers(app), numbers(cask.split(",")[0])
    if not a or not c:
        return None
    return (a > c) - (a < c)


def source_text(app: App) -> str:
    if app.source == "pkg":
        return f"from the package {app.pkg_id}"
    return {"hand": "by hand", "mas": "from the App Store"}.get(app.source, app.source)


def _cask_note(app: App, t: TidyFacts) -> Tuple[str, str]:
    """The default verb and comment of an app with a matching cask that you can change."""
    token = app.match
    info = t.casks.get(token)
    if info is None:
        return "keep", f"cask {token}: `brew info` has no data for it"
    if info.pkg:
        return "replace", f"the cask {token} runs the vendor installer, which can ask for your password"
    if info.auto_updates:
        return "adopt", "the cask auto-updates"
    v, cv = app.version or "no version", info.version.split(",")[0]
    order = compare(app.version, info.version)
    if order == 0:
        return "adopt", f"{v}, the same as the cask"
    if order is not None and order < 0:
        return "replace", f"{v}, the cask has {cv}"
    if order is not None:
        return "keep", f"{v}, the cask {token} has {cv}, so replace would make it older"
    return "keep", f"{v}: dotkit cannot compare it with {cv} of the cask {token}; adopt stops when they differ"


def default_line(app: App, t: TidyFacts, declared: Declared) -> Optional[Line]:
    """The line of an app with its default verb (TIDY.md, section 4.2); None when the lists have the app."""
    p = app.path
    if app.source == "cask":
        if declared.brew(app.cask, "cask"):
            return None
        return Line("add", p, f"cask:{app.cask}", "from a cask, not in nix/casks.txt")
    if app.source == "mas":
        if app.mas_id and declared.mas_id(app.mas_id):
            return None
        if not app.mas_id:
            return Line("keep", p, note="from the App Store; Spotlight has no App Store ID for it")
        if not t.mas:
            return Line("keep", p, note="from the App Store; run `dotkit add brew:mas`, then dotkit tidy again")
        cask = (f"; cask {app.match} has it too, but the App Store data and license do not carry over"
                if app.match else "")
        return Line("add", p, f"mas:{app.mas_id}", f"from the App Store, not in nix/mas.txt{cask}")
    if app.source in ("setapp", "toolbox", "system"):
        return Line("keep", p, note={"setapp": "from Setapp, which manages it",
                                     "toolbox": "from JetBrains Toolbox, which manages it",
                                     "system": "from Apple"}[app.source])
    src = source_text(app)
    if app.system_parts:
        src += (f", with system parts ({app.system_parts[0]}) that only the vendor's uninstaller removes")
    if not app.writable:
        alt = f"; or: replace cask:{app.match}" if app.match else ""
        return Line("keep", p, note=f"{src}; owned by {app.owner or 'another user'}, so tidy does not change it{alt}")
    if app.editions:
        return Line("keep", p, note=f"{src}; {', '.join(app.editions)} has another bundle ID than "
                                    f"{app.bundle_id}, so the app data does not follow")
    if app.candidates:
        return Line("keep", p, note=f"{src}; several casks match: {', '.join(app.candidates)}")
    if not app.match:
        return Line("keep", p, note=f"{src}; no cask")
    verb, note = _cask_note(app, t)
    if not app.confirmed and app.bundle_id and app.cask_ids:
        # The cask copy may have another bundle ID, and then it does not find the app data. adopt keeps the app.
        note += (f"; the cask names {', '.join(app.cask_ids[:2])}, not {app.bundle_id}, so check that the app "
                 "data follows")
        if verb == "replace":
            verb, note = "keep", f"{note}; or: replace cask:{app.match}"
    if verb == "replace" and app.running:
        note += "; it runs, and tidy quits it first"
    return Line(verb, p, f"cask:{app.match}" if verb != "keep" else "", f"{src}; {note}")


def provided(tool: Tool, declared: Declared) -> str:
    """What dotkit has instead of this copy of a tool, such as "jq (from Nix)"; "" for nothing."""
    if tool.kind == "brew":
        src = declared.brew(tool.name, "formula") or ""
        # The lists or the base have the formula itself: this is not a copy.
        if src.endswith(("(from Homebrew)", "(dotkit formula)")):
            return ""
        return src.replace("(dotkit, from ", "(from ")  # after "dotkit has"
    if tool.kind in ("npm", "pipx", "uv", "cargo"):
        key = ("pipx" if tool.kind == "uv" else tool.kind) + ":" + tool.name
        if declared.mise_key(key):
            return f"{key} (from mise)"
    for name in dict.fromkeys([tool.name] + tool.commands):
        src = declared.tool_source(name)
        if src:
            return src
    return ""


def default_tool_line(tool: Tool, t: TidyFacts, declared: Declared) -> Optional[Line]:
    """The line of a command-line tool with its default verb (TIDY.md, section 8.1); None when it is not a line."""
    p = tool.path
    if tool.kind == "manager":
        return Line("keep", p, note=f"{tool.name}, a {tool.where}; dotkit does not load it. {CHECK_REFERENCES}")
    src = provided(tool, declared)
    if tool.kind in PACKAGES:
        if not src:
            return None
        where = f"{tool.where}; " if tool.kind == "npm" else ""
        if not tool.uninstall:
            what = {"npm": "npm next to it", "uv": "uv"}.get(tool.kind, tool.kind)
            return Line("keep", p, note=f"{where}dotkit has {src}; no {what} to uninstall it")
        return Line("uninstall", p, tool.thing, f"{where}dotkit has {src}")
    owner = f"; owned by {tool.owner or 'another user'}" if not tool.writable else ""
    if tool.kind == "go":
        if not src:
            return None
        return Line("trash" if tool.writable else "keep", p, note=f"from go install; dotkit has {src}{owner}")
    what = f"a link to {tilde(tool.target, t.home)}" if tool.target else "a file from an installer"
    if src and tool.writable:
        return Line("trash", p, note=f"{what}; dotkit has {src}")
    return Line("keep", p, note=what + (f"; dotkit has {src}" if src else "") + owner)


def _order(x: Line) -> Tuple[int, str]:
    return VERBS.index(x.verb), x.path.rsplit("/", 1)[-1].lower()


def defaults(t: TidyFacts, declared: Declared) -> Tuple[List[Line], int]:
    """The lines with their default verbs: the apps, then the tools, each in the order of VERBS. Also the number of
    apps that the lists have."""
    lines, listed = [], 0
    for app in t.apps:
        line = default_line(app, t, declared)
        if line is None:
            listed += 1
        else:
            lines.append(line)
    tools = [x for x in (default_tool_line(tool, t, declared) for tool in t.tools) if x is not None]
    return sorted(lines, key=_order) + sorted(tools, key=_order), listed


def render(lines: List[Line], t: TidyFacts, listed: int) -> str:
    """The plan text for the editor."""
    tools = {x.path for x in t.tools}
    n_tools = sum(1 for x in lines if x.path in tools)
    parts = [(len(lines) - n_tools, "app", "apps"), (n_tools, "command-line tool", "command-line tools")]
    count = " and ".join(f"{n} {one if n == 1 else many}" for n, one, many in parts if n) or "nothing"
    out = HEADER.format(count=count).splitlines()
    if listed:
        out.append(f"# Not shown: {listed} app{'s' if listed != 1 else ''} that the lists have already.")
    for p in t.skipped:
        out.append(f"# Not shown: {p}, because its path has ` #` or a line break.")
    out.append("")
    width = max([len(x.thing) for x in lines] + [0])
    verb_width = max(len(x.verb) for x in lines)
    # The comments line up after the paths, up to a path of PATH_WIDTH characters.
    path_width = min(max(len(tilde(x.path, t.home)) for x in lines), PATH_WIDTH)
    first_tool = next((x for x in lines if x.path in tools), None)
    for x in lines:
        if x is first_tool:
            out += ["", *TOOLS.splitlines()]
        head = f"{x.verb:<{max(verb_width + 1, 8)}} " + (f"{x.thing:<{width}}  " if width else "")
        path = tilde(x.path, t.home)
        out.append(f"{head}{path:<{path_width}}  # {x.note}" if x.note else f"{head}{path}")
    return "\n".join(out) + "\n"


def parse(text: str, t: TidyFacts) -> Tuple[List[Line], List[str]]:
    """The lines of an edited plan, and the errors of their form: "line 3: ..."."""
    lines, errors = [], []
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        s = s.split(" #", 1)[0].strip()
        verb, _, rest = s.partition(" ")
        rest = rest.strip()
        if verb not in VERBS:
            errors.append(f"line {n}: `{verb}` is not a verb. Use {', '.join(VERBS)}.")
            continue
        thing = ""
        if re.match(r"(?:" + "|".join(THINGS) + r"):\S", rest):
            thing, _, rest = rest.partition(" ")
            rest = rest.strip()
        if not rest:
            errors.append(f"line {n}: the line has no path.")
            continue
        lines.append(Line(verb, expand(rest, t.home), thing, number=n))
    return lines, errors


def check_tool(x: Line, tool: Tool, t: TidyFacts) -> List[str]:
    """The errors of a line of a command-line tool (TIDY.md, section 8.1)."""
    where, name = f"line {x.number}", tilde(x.path, t.home)
    if x.verb in ("adopt", "replace", "add"):
        return [f"{where}: {x.verb} is for apps. Use uninstall, trash, or keep."]
    if x.verb == "uninstall":
        if tool.kind not in PACKAGES:
            return [f"{where}: {name} is not a package. Use trash or keep."]
        if x.thing != tool.thing:
            return [f"{where}: {name} is {tool.thing}: uninstall {tool.thing} <path>."]
        if not tool.uninstall:
            return [f"{where}: the scan found no command that uninstalls {tool.thing}."]
    if x.verb == "trash":
        if tool.kind in PACKAGES:
            return [f"{where}: {tool.thing} is a package. Use uninstall, so that its package manager forgets it too."]
        if not tool.writable:
            return [f"{where}: {name} is owned by {tool.owner or 'another user'}, so tidy cannot move it to the Trash."]
    return []


def check(lines: List[Line], t: TidyFacts) -> Tuple[List[str], List[str]]:
    """The errors and the notes of the parsed lines against the scan (TIDY.md, sections 5 and 8.1)."""
    apps: Dict[str, App] = {a.path: a for a in t.apps}
    tools: Dict[str, Tool] = {x.path: x for x in t.tools}
    errors, notes, seen = [], [], set()
    for x in lines:
        where = f"line {x.number}"
        app = apps.get(x.path)
        tool = tools.get(x.path)
        if app is None and tool is None:
            errors.append(f"{where}: {tilde(x.path, t.home)} is not an app or a tool of the scan.")
            continue
        if x.path in seen:
            errors.append(f"{where}: {tilde(x.path, t.home)} has another line already.")
            continue
        seen.add(x.path)
        if tool is not None:
            errors += check_tool(x, tool, t)
            continue
        assert app is not None
        if x.verb == "uninstall":
            errors.append(f"{where}: uninstall is for command-line tools. Use trash or keep for an app.")
        elif x.verb in ("adopt", "replace"):
            token = x.token
            if not token or not re.fullmatch(r"[a-z0-9][a-z0-9@._+-]*(?:/[a-z0-9._-]+){0,2}", token):
                errors.append(f"{where}: {x.verb} needs a cask: {x.verb} cask:<token> <path>.")
            elif t.names and token not in t.names:
                errors.append(f"{where}: Homebrew has no cask `{token}`.")
            elif app.source == "cask" and token == app.cask:
                errors.append(f"{where}: the cask {token} has the app already. Use add or keep.")
            elif x.verb == "adopt" and token in t.casks and t.casks[token].pkg:
                errors.append(f"{where}: the cask {token} runs an installer, so it has nothing to adopt. "
                              "Use replace.")
            elif token != app.match:
                notes.append(f"{where}: you chose the cask {token}; the scan proposed "
                             f"{app.match or 'none'}.")
            if app.source == "mas":
                notes.append(f"{where}: {app.name} is from the App Store. The cask copy does not get its App Store "
                             "data and license.")
        elif x.verb == "add":
            if x.token:
                if app.source != "cask" or x.token != app.cask:
                    errors.append(f"{where}: add is for an app from a cask or the App Store. Use adopt or replace.")
            elif x.mas_id:
                if app.source != "mas" or x.mas_id != app.mas_id:
                    errors.append(f"{where}: {app.name} is not the App Store app {x.mas_id}.")
                elif not t.mas:
                    errors.append(f"{where}: mas is not on this Mac. Run `dotkit add brew:mas` first.")
            else:
                errors.append(f"{where}: add needs cask:<token> or mas:<id>.")
        elif x.verb == "trash":
            if app.source == "cask":
                errors.append(f"{where}: the cask {app.cask} has the app. Use `dotkit remove cask:{app.cask}` to "
                              "uninstall it.")
            elif not app.writable:
                errors.append(f"{where}: {app.name} is owned by {app.owner or 'another user'}, so tidy cannot move "
                              "it to the Trash.")
            elif app.system_parts:
                notes.append(f"{where}: trash moves only the app. Its package has system parts that only the "
                             "vendor's uninstaller removes.")
    return errors, notes


def commands(lines: List[Line], t: TidyFacts) -> List[str]:
    """What apply does, one line per plan line, for the confirmation and for -n."""
    apps = {a.path: a for a in t.apps}
    tools = {x.path: x for x in t.tools}
    out = []
    for x in lines:
        app = apps.get(x.path)
        tool = tools.get(x.path)
        name = tilde(x.path, t.home)
        if x.verb == "uninstall" and tool is not None:
            out.append(" ".join(shell_path(tilde(a, t.home)) for a in tool.uninstall))
        elif x.verb == "adopt":
            out.append(f"brew install --cask --adopt {x.token}    ({name})")
        elif x.verb == "replace":
            quit_first = f"quit {app.name}, then " if app is not None and app.running else ""
            out.append(f"{quit_first}brew install --cask --force {x.token}    ({name})")
        elif x.verb == "trash":
            quit_first = f"quit {app.name}, then " if app is not None and app.running else ""
            out.append(f"{quit_first}trash {shell_path(name)}")
        elif x.verb == "add":
            dest = "nix/casks.txt" if x.token else "nix/mas.txt"
            out.append(f"add {x.thing} to {dest}    ({name})")
    return out
