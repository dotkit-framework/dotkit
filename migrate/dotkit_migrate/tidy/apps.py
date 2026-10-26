"""The scan of dotkit tidy: the apps, their sources, and the casks that match them (TIDY.md, section 3).

Read-only, with the rules of migrators/: no writes, and commands only
through ctx.runner.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Iterator, List, Optional, Set, Tuple

from ..catalog import BREW_ENV
from ..context import Context, tilde
from . import clis

# Directories with apps that are not apps of their own: the web apps of Chrome and Brave (*.localized), and
# the apps of Nix and Home Manager.
SKIP_DIRS = ("Nix Apps", "Home Manager Apps")

# The files of a package that only the vendor's uninstaller removes, by the start of their path in
# `pkgutil --files`.
SYSTEM_PARTS = ("Library/LaunchDaemons/", "Library/LaunchAgents/", "Library/Extensions/",
                "Library/PrivilegedHelperTools/", "Library/SystemExtensions/")

# The bundle ID in a zap or uninstall path of a cask: ~/Library/Preferences/com.tinyspeck.slackmacgap.plist.
_ID_PATH = re.compile(r"/(?:Preferences|Containers|Group Containers|Application Scripts|HTTPStorages|Caches|WebKit|"
                      r"Saved Application State|Application Support)/(?:ByHost/)?"
                      r"([A-Za-z0-9][\w-]*(?:\.[\w-]+)+?)(?:\.plist|\.savedState|\.binarycookies|\*)?$")
_APP_PATH = re.compile(r"/Applications/[^/]+\.app")
_MAS_LINE = re.compile(r"^\s*(\d+)\s+(.+?)\s+\(([^)]*)\)\s*$")


@dataclass
class Cask:
    """What the bulk cask file of Homebrew says about one cask. It only finds candidates (TIDY.md, section 3.3)."""

    token: str
    apps: Tuple[str, ...] = ()  # the names of its apps in /Applications: "Slack.app"
    quits: FrozenSet[str] = frozenset()  # the bundle IDs of uninstall: quit, launchctl, signal
    zap_ids: FrozenSet[str] = frozenset()  # the bundle IDs in the paths of zap and uninstall
    pkg_ids: FrozenSet[str] = frozenset()  # the pkgutil IDs of uninstall, as Homebrew patterns


@dataclass
class CaskInfo:
    """What `brew info --json=v2` says about a cask: the stable format that the plan reads."""

    token: str
    version: str = ""
    auto_updates: bool = False
    apps: Tuple[str, ...] = ()  # the names of its apps in /Applications
    pkg: bool = False  # it runs an installer and has no app artifact
    pkg_ids: Tuple[str, ...] = ()
    installed: str = ""  # the installed version; "" when it is not installed


@dataclass
class App:
    path: str  # absolute
    name: str  # "Slack.app"
    bundle_id: str = ""
    version: str = ""  # CFBundleShortVersionString
    build: str = ""  # CFBundleVersion
    source: str = "hand"  # cask, mas, setapp, toolbox, pkg, system, or hand
    cask: str = ""  # source cask: the installed cask that has the app
    mas_id: int = 0  # source mas: the App Store ID from Spotlight; 0 when it is unknown
    pkg_id: str = ""  # the package of `pkgutil --file-info`
    system_parts: List[str] = field(default_factory=list)  # source pkg: files of SYSTEM_PARTS
    writable: bool = True  # you can change the app without sudo
    owner: str = ""
    running: bool = False
    match: str = ""  # the cask that the scan proposes
    candidates: List[str] = field(default_factory=list)  # several casks match, and none wins
    editions: List[str] = field(default_factory=list)  # casks with the name of the app and another bundle ID
    confirmed: bool = False  # the matched cask names the bundle ID of the app
    cask_ids: List[str] = field(default_factory=list)  # the bundle IDs that the matched cask names


@dataclass
class TidyFacts:
    home: str = ""
    brew: str = ""  # the brew command; "" without Homebrew
    mas: str = ""  # the mas command; "" without mas
    apps: List[App] = field(default_factory=list)
    casks: Dict[str, CaskInfo] = field(default_factory=dict)  # the installed casks and the matched ones
    names: Set[str] = field(default_factory=set)  # the tokens of cask_names.txt; empty when it is missing
    mas_names: Dict[int, str] = field(default_factory=dict)  # from `mas list`
    skipped: List[str] = field(default_factory=list)  # apps and tools that cannot be a line of the plan
    tools: List[clis.Tool] = field(default_factory=list)  # the command-line tools (TIDY.md, section 8.1)
    warnings: List[str] = field(default_factory=list)


def _strings(x) -> Iterator[str]:
    """Every string in a JSON value."""
    if isinstance(x, str):
        yield x
    elif isinstance(x, list):
        for y in x:
            yield from _strings(y)
    elif isinstance(x, dict):
        for y in x.values():
            yield from _strings(y)


def _base(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def cask_from_raw(token: str, c: dict) -> Cask:
    """One cask of the bulk file, from its raw_artifacts."""
    apps: List[str] = []
    quits: Set[str] = set()
    zap: Set[str] = set()
    pkgs: Set[str] = set()
    for art in c.get("raw_artifacts") or []:
        if not isinstance(art, list) or not art:
            continue
        kind, args = art[0], art[1:]
        if kind == ":app":
            # [":app", ["Telegram.app"], {":target": "Telegram Desktop.app"}]: the app gets the name of the target.
            target = [a[":target"] for a in args if isinstance(a, dict) and isinstance(a.get(":target"), str)]
            if target:
                apps.append(_base(target[0]))
            else:
                apps += [_base(s) for s in _strings([a for a in args if not isinstance(a, dict)])]
        elif kind in (":uninstall", ":zap"):
            for a in args:
                if not isinstance(a, dict):
                    continue
                if kind == ":uninstall":
                    for k in (":quit", ":launchctl", ":signal"):
                        quits |= {s for s in _strings(a.get(k)) if "." in s}
                    pkgs |= set(_strings(a.get(":pkgutil")))
                    apps += [_base(s) for s in _strings(a.get(":delete")) if _APP_PATH.fullmatch(s)]
                for s in _strings(a):
                    m = _ID_PATH.search(s)
                    if m:
                        zap.add(m.group(1))
    return Cask(token, tuple(dict.fromkeys(apps)), frozenset(quits), frozenset(zap), frozenset(pkgs))


def cask_from_info(c: dict) -> CaskInfo:
    """One cask of `brew info --json=v2 --cask`."""
    apps: List[str] = []
    pkg_ids: List[str] = []
    has_app = has_pkg = False
    for art in c.get("artifacts") or []:
        if not isinstance(art, dict):
            continue
        if "app" in art:
            has_app = True
            # {"app": ["Slack.app"], "target": "/Applications/Slack.app"}, or a target inside the list.
            targets = [a["target"] for a in art["app"] if isinstance(a, dict) and isinstance(a.get("target"), str)]
            if isinstance(art.get("target"), str):
                targets = [art["target"]]
            apps += [_base(t) for t in targets] or [_base(a) for a in art["app"] if isinstance(a, str)]
        if "pkg" in art:
            has_pkg = True
        for u in art.get("uninstall") or []:
            if isinstance(u, dict):
                pkg_ids += list(_strings(u.get("pkgutil")))
                apps += [_base(s) for s in _strings(u.get("delete")) if _APP_PATH.fullmatch(s)]
    return CaskInfo(str(c.get("token") or ""), str(c.get("version") or ""), c.get("auto_updates") is True,
                    tuple(dict.fromkeys(apps)), has_pkg and not has_app, tuple(pkg_ids),
                    str(c.get("installed") or ""))


def pkg_matches(pattern: str, pkg_id: str) -> bool:
    """Homebrew reads a pkgutil ID of a cask as a pattern: com.google.drivefs.*."""
    try:
        return re.fullmatch(pattern, pkg_id) is not None
    except re.error:
        return pattern == pkg_id


def token_form(name: str) -> str:
    """The name of an app in the form of a cask token: "Google Chrome.app" -> google-chrome."""
    stem = name[:-4] if name.endswith(".app") else name
    stem = stem.lower().replace("+", "-plus").replace("@", "-at-")
    return re.sub(r"[^a-z0-9]+", "-", stem).strip("-")


class Index:
    """The casks of the bulk file, by app name, bundle ID, and pkgutil ID."""

    def __init__(self, casks: Dict[str, Cask]):
        self.casks = casks
        self.by_name: Dict[str, List[str]] = {}
        self.by_id: Dict[str, List[str]] = {}
        for c in casks.values():
            for a in c.apps:
                self.by_name.setdefault(a, []).append(c.token)
            for i in c.quits | c.zap_ids:
                self.by_id.setdefault(i, []).append(c.token)

    def candidates(self, app: App) -> List[str]:
        """The casks with the name of the app or its package; without them, the casks that name its bundle ID."""
        found = set(self.by_name.get(app.name, ()))
        if app.pkg_id:
            found |= {c.token for c in self.casks.values() if any(pkg_matches(p, app.pkg_id) for p in c.pkg_ids)}
        if not found and app.bundle_id:
            found = set(self.by_id.get(app.bundle_id, ()))
        return sorted(found)

    def choose(self, app: App) -> None:
        """Set app.match, or app.candidates and app.editions (TIDY.md, section 3.4)."""
        tokens = self.candidates(app)
        bid = app.bundle_id
        confirmed = [t for t in tokens if bid and bid in self.casks[t].quits | self.casks[t].zap_ids]
        # Only the IDs of uninstall name the app itself; zap paths often keep the IDs of old versions.
        editions = [t for t in tokens if bid and self.casks[t].quits and t not in confirmed]
        pool = confirmed or [t for t in tokens if t not in editions]
        plain = [t for t in pool if "@" not in t] or pool
        if len(plain) > 1:
            want = token_form(app.name)
            first = [t for t in plain if all(o == t or o.startswith(t + "-") for o in plain)]
            plain = [want] if want in plain else first[:1] or plain
        if len(plain) == 1:
            app.match, app.confirmed = plain[0], plain[0] in confirmed
            app.cask_ids = sorted(self.casks[app.match].quits | self.casks[app.match].zap_ids)
        elif plain:
            app.candidates = plain
        else:
            app.editions = editions


def _children(d: Path) -> List[Path]:
    try:
        return sorted(d.iterdir())
    except OSError:
        return []


def _bundle(p: Path) -> bool:
    return p.name.endswith(".app") and p.is_dir() and not p.is_symlink()


def app_paths(ctx: Context) -> List[Tuple[Path, str]]:
    """The apps of TIDY.md section 3.1, with the source that their directory gives ("" for none)."""
    out: List[Tuple[Path, str]] = []
    for root in (ctx.applications, ctx.home / "Applications"):
        for p in _children(root):
            if _bundle(p):
                out.append((p, ""))
            elif (p.is_dir() and not p.is_symlink() and not p.name.endswith(".localized")
                  and not p.name.endswith(".app") and p.name not in SKIP_DIRS):
                fixed = ("setapp" if root == ctx.applications and p.name == "Setapp" else
                         "toolbox" if root != ctx.applications and p.name == "JetBrains Toolbox" else "")
                out += [(q, fixed) for q in _children(p) if _bundle(q)]
    return out


def read_info(path: Path) -> Tuple[str, str, str]:
    """(bundle ID, CFBundleShortVersionString, CFBundleVersion) of an app; "" for what it does not have."""
    try:
        p = path / "Contents/Info.plist"
        if p.stat().st_size > 4_000_000:
            return "", "", ""
        with open(p, "rb") as f:
            info = plistlib.load(f)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return "", "", ""
    if not isinstance(info, dict):
        return "", "", ""

    def text(key: str) -> str:
        v = info.get(key)
        return str(v).strip() if isinstance(v, (str, int, float)) else ""

    return text("CFBundleIdentifier"), text("CFBundleShortVersionString"), text("CFBundleVersion")


def bulk_file(ctx: Context) -> Optional[Path]:
    """The newest bulk file of the Homebrew API cache, or None."""
    api = Path(ctx.env.get("HOMEBREW_CACHE") or ctx.home / "Library/Caches/Homebrew") / "api"
    found = []
    for p in _children(api / "internal"):
        if p.name.startswith("packages.") and p.name.endswith(".jws.json.payload"):
            try:
                found.append((p.stat().st_mtime, p))
            except OSError:
                pass
    return max(found)[1] if found else None


def load_index(ctx: Context, t: TidyFacts) -> Optional[Index]:
    """The index of the bulk file. Its first line is the signature header; the second line is the JSON."""
    p = bulk_file(ctx)
    try:
        if p is None:
            raise ValueError("no file")
        with open(p, "rb") as f:
            f.readline()
            data = json.loads(f.read())
        casks = data["casks"]
        if not isinstance(casks, dict):
            raise ValueError("no casks")
        index = Index({k: cask_from_raw(k, v) for k, v in casks.items() if isinstance(v, dict)})
    except (OSError, ValueError, KeyError, TypeError):
        t.warnings.append("The cask data of Homebrew is missing or has a format that dotkit does not know, so tidy "
                          "proposes no cask. Run `brew update`, then dotkit tidy again.")
        return None
    names = ctx.env.get("HOMEBREW_CACHE") or str(ctx.home / "Library/Caches/Homebrew")
    try:
        text = (Path(names) / "api/cask_names.txt").read_text(encoding="utf-8")
        t.names = {line.strip() for line in text.splitlines() if line.strip()}
    except OSError:
        t.names = set(index.casks)
    return index


def brew_info(ctx: Context, t: TidyFacts, args: List[str]) -> List[CaskInfo]:
    out = ctx.runner.run([t.brew, "info", "--json=v2", *args], timeout=300, env=BREW_ENV)
    try:
        return [cask_from_info(c) for c in json.loads(out or "")["casks"] if isinstance(c, dict)]
    except (ValueError, KeyError, TypeError):
        return []


def cask_infos(ctx: Context, t: TidyFacts, tokens: List[str]) -> Dict[str, CaskInfo]:
    """brew info of these casks: one command, or one per cask when it fails (one bad token fails it all)."""
    tokens = [x for x in tokens if not t.names or x in t.names]
    if not tokens:
        return {}
    found = brew_info(ctx, t, ["--cask", *tokens])
    if not found:
        for x in tokens:
            found += brew_info(ctx, t, ["--cask", x])
    return {c.token: c for c in found}


def scan(ctx: Context) -> TidyFacts:
    t = TidyFacts(home=str(ctx.home))
    run = ctx.runner
    t.brew = run.which("brew", ("/opt/homebrew/bin",)) or ""
    t.mas = run.which("mas", ("/opt/homebrew/bin",)) or ""
    if t.brew:
        installed = brew_info(ctx, t, ["--installed", "--cask"])
        t.casks = {c.token: c for c in installed}
    else:
        t.warnings.append("Homebrew is not on this Mac, so tidy proposes no cask.")
    if t.mas:
        for line in (run.run([t.mas, "list"], timeout=60) or "").splitlines():
            m = _MAS_LINE.match(line)
            if m and m.group(2).strip():
                t.mas_names[int(m.group(1))] = m.group(2).strip()
    procs = (run.run(["/bin/ps", "-axo", "comm="], timeout=30) or "").splitlines()
    owned = {a: c.token for c in t.casks.values() for a in c.apps}
    for path, fixed in app_paths(ctx):
        app = App(str(path), path.name)
        app.bundle_id, app.version, app.build = read_info(path)
        app.writable = os.access(path, os.W_OK)
        app.owner = clis.owner(path)
        app.running = any(line.startswith(app.path + "/") for line in procs)
        if path.name in owned:
            app.source, app.cask = "cask", owned[path.name]
        elif (path / "Contents/_MASReceipt/receipt").is_file():
            app.source = "mas"
            out = (run.run(["/usr/bin/mdls", "-raw", "-name", "kMDItemAppStoreAdamID", app.path], timeout=30)
                   or "").strip()
            app.mas_id = int(out) if out.isdigit() else 0
        elif fixed:
            app.source = fixed
        else:
            out = run.run(["/usr/sbin/pkgutil", "--file-info", app.path], timeout=30) or ""
            m = re.search(r"^pkgid: (\S+)", out, re.M)
            app.pkg_id = m.group(1) if m else ""
            by_pkg = [c.token for c in t.casks.values() if app.pkg_id and
                      any(pkg_matches(p, app.pkg_id) for p in c.pkg_ids)]
            if by_pkg:
                app.source, app.cask = "cask", by_pkg[0]
            elif app.pkg_id and not app.pkg_id.startswith("com.apple."):
                app.source = "pkg"
                files = run.run(["/usr/sbin/pkgutil", "--files", app.pkg_id], timeout=60) or ""
                app.system_parts = [f for f in files.splitlines()
                                    if f.startswith(SYSTEM_PARTS) or "/Contents/Library/SystemExtensions/" in f][:3]
            elif app.bundle_id.startswith("com.apple."):
                app.source = "system"
        if " #" in tilde(app.path, t.home) or "\n" in app.path:
            t.skipped.append(tilde(app.path, t.home))
            continue
        t.apps.append(app)
    index = load_index(ctx, t) if t.brew else None
    if index is not None:
        for app in t.apps:
            if app.source == "cask":
                continue
            index.choose(app)
            # A pkg cask often names no app, and its package ID can differ from the app's (SF Symbols), so
            # only the match shows that an installed cask has the app.
            info = t.casks.get(app.match)
            if info is not None and info.installed and app.source in ("hand", "pkg", "system"):
                app.source, app.cask = "cask", app.match
        wanted = sorted({a.match for a in t.apps if a.match and a.source != "mas" and a.match not in t.casks})
        t.casks.update(cask_infos(ctx, t, wanted))
    for tool in clis.scan(ctx):
        if " #" in tilde(tool.path, t.home) or "\n" in tool.path:
            t.skipped.append(tilde(tool.path, t.home))
        else:
            t.tools.append(tool)
    return t
