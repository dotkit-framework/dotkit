"""The packages that the installed executables were built from (macho.py): the Go modules and the Go
version of Go binaries, and the crates of Rust binaries that cargo-auditable builds. The executables are
the ones on the PATH of dotkit: the Nix profile of the user and of the system, Homebrew, and the mise tools.

Go advisories name the functions that have the bug. An advisory whose functions no binary of the tool
has is left out, as govulncheck does for a binary: the tool cannot call them. It counts in the summary.

The executables exist only where the tools are installed: in CI the part is skipped.
"""

from __future__ import annotations

import os
import plistlib
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import macho
from ..model import Accept, Item, Part, package_query

NAME, LABEL, NOUNS = "binaries", "in a binary", ("package in binaries", "packages in binaries")

NOT_CALLED = "the binaries do not have the functions of the advisory"
_STORE = re.compile(r"/nix/store/[0-9a-z]{32}-([^/]+)/")
_CELLAR = re.compile(r"/(Cellar|Caskroom)/([^/]+)/([^/]+)/")
# The outermost app bundle of a path: /Applications/OrbStack.app/Contents/MacOS/scli.app/... -> OrbStack.
_APP = re.compile(r"^(.*?/([^/]+)\.app)/")


def app_version(bundle: Path) -> str:
    """CFBundleShortVersionString of the Info.plist of an app bundle; "" when it has none."""
    try:
        with open(bundle / "Contents/Info.plist", "rb") as f:
            info = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return ""
    v = info.get("CFBundleShortVersionString") if isinstance(info, dict) else None
    return v if isinstance(v, str) else ""

Owner = Tuple[str, str, str]  # (name, version, where it comes from: Nix, Homebrew, mise)


def owner(path: str, mise_installs: Dict[str, Tuple[str, str]]) -> Owner:
    """The tool of an executable, from its real path: a Nix store path (ripgrep-15.2.0), a Homebrew keg or
    cask, or the install directory of a mise tool."""
    m = _STORE.search(path)
    if m:
        # NAME-VERSION: the version starts at the first - with a digit after it.
        n = re.match(r"(.+?)-(\d.*)", m[1])
        return (n[1], n[2], "Nix") if n else (m[1], "", "Nix")
    m = _CELLAR.search(path)
    if m:
        return m[2], m[3], "Homebrew"
    m = _APP.search(path)
    if m:
        return m[2], app_version(Path(m[1])), "app"
    for install, (key, version) in mise_installs.items():
        if path.startswith(install.rstrip("/") + "/"):
            return key, version, "mise"
    return os.path.basename(path), "", ""


class GoFunctions:
    """The check of a Go advisory against the function names of the binaries that have the module. tables:
    path -> function name table, shared by the checks of a run, so each binary is read once, when an
    advisory needs it."""

    def __init__(self, paths: List[Path], tables: Optional[Dict[Path, bytes]] = None):
        self.paths = paths
        self.tables = {} if tables is None else tables

    def names(self) -> List[bytes]:
        out = []
        for p in self.paths:
            if p not in self.tables:
                where = macho.sections(p).get("__gopclntab")
                self.tables[p] = macho.read_section(p, where) if where else b""
            if self.tables[p]:
                out.append(self.tables[p])
        return out

    def __call__(self, record: dict, query: dict) -> Optional[str]:
        """Only the records of the Go vulnerability database (GO-*) name the functions. Another record, such
        as its GHSA alias, gives no verdict: match.findings applies the verdict of the GO record to it."""
        if not str(record.get("id") or "").startswith("GO-"):
            return None
        module = (query.get("package") or {}).get("name")
        imports = [i for a in record.get("affected") or [] if isinstance(a, dict)
                   and (a.get("package") or {}).get("name") == module
                   for i in (a.get("ecosystem_specific") or {}).get("imports") or [] if isinstance(i, dict)]
        # (package, function); a package without functions: any function of it.
        wanted = [(i["path"], s) for i in imports if isinstance(i.get("path"), str)
                  for s in (i.get("symbols") or [""]) if isinstance(s, str)]
        tables = self.names() if wanted else []
        if not tables:
            # No functions in the advisory, or no table to check: the advisory counts.
            return None
        if any(macho.go_has_function(t, path, sym) for t in tables for path, sym in wanted):
            return None
        return NOT_CALLED


def components(paths: List[Path]) -> Dict[Tuple[str, str, str], List[Path]]:
    """(ecosystem, name, version) -> the executables that have it: Go stdlib and modules, Rust crates."""
    out: Dict[Tuple[str, str, str], List[Path]] = {}
    for p in paths:
        sects = macho.sections(p)
        found: List[Tuple[str, str, str]] = []
        if "__go_buildinfo" in sects:
            build = macho.go_build(macho.read_section(p, sects["__go_buildinfo"]))
            if build is not None:
                found.append(("Go", "stdlib", build.version))
                found += [("Go", path, v) for path, v in build.modules]
        if ".dep-v0" in sects:
            found += [("crates.io", n, v) for n, v in macho.rust_crates(macho.read_section(p, sects[".dep-v0"])) or []]
        for c in found:
            out.setdefault(c, [])
            if p not in out[c]:
                out[c].append(p)
    return out


def items(tool: Owner, found: Dict[Tuple[str, str, str], List[Path]],
          tables: Optional[Dict[Path, bytes]] = None) -> List[Item]:
    name, version, origin = tool
    of = f"{name} {version}".strip()
    out = []
    for (ecosystem, pkg, v), paths in sorted(found.items()):
        if ecosystem == "Go":
            label, shown = ("Go standard library", "Go") if pkg == "stdlib" else ("Go module", pkg)
            accept: Optional[Accept] = GoFunctions(paths, tables)
        else:
            label, shown, accept = "Rust crate", pkg, None
        out.append(Item(NAME, f"{name} > {shown}", v.lstrip("v") if pkg == "stdlib" else v,
                        [package_query(ecosystem, pkg, v)], where=f"{label} in {of} ({origin})" if origin else
                        f"{label} in {of}", parent=name, accept=accept))
    return out


def executables(dirs: List[Path]) -> List[Path]:
    """The real paths of the executable files in the directories, each once. A program that Nix wraps in a
    script (makeWrapper) is next to it as .NAME-wrapped: that one too."""
    out: List[Path] = []
    seen = set()

    def add(path: str) -> None:
        real = os.path.realpath(path)
        if real not in seen and os.path.isfile(real) and os.access(real, os.X_OK):
            seen.add(real)
            out.append(Path(real))
            wrapped = os.path.join(os.path.dirname(real), f".{os.path.basename(real)}-wrapped")
            if os.path.lexists(wrapped):
                add(wrapped)

    for d in dirs:
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            continue
        for e in entries:
            add(os.path.join(d, e))
    return out


def _children(root: Path) -> List[Path]:
    try:
        return list(root.iterdir())
    except OSError:
        return []


def collect(ctx) -> Part:
    if ctx.config_only:
        from . import CONFIG_ONLY
        return Part(skipped=CONFIG_ONLY)
    user = os.environ.get("USER") or ctx.env.home.name
    installs = {e["install_path"]: (key, e["version"]) for key, entries in ctx.mise_installed().items()
                for e in entries if isinstance(e.get("install_path"), str)}
    dirs = [ctx.system(f"/etc/profiles/per-user/{user}/bin"), ctx.system("/run/current-system/sw/bin"),
            ctx.system("/opt/homebrew/bin")]
    # A mise tool from GitHub or aqua has its executable in the install directory or a directory in it,
    # a runtime in bin/.
    for p in installs:
        root = Path(p)
        dirs += [root] + sorted(c for c in _children(root) if c.is_dir() and not c.is_symlink())
    # The executables of the apps: Homebrew casks, the App Store, and apps from other places.
    for apps in (ctx.system("/Applications"), ctx.env.home / "Applications"):
        try:
            bundles = sorted(apps.glob("*.app"))
        except OSError:
            bundles = []
        dirs += [b / "Contents/MacOS" for b in bundles]
    by_owner: Dict[Owner, List[Path]] = {}
    for p in executables(dirs):
        by_owner.setdefault(owner(str(p), installs), []).append(p)
    part = Part()
    tables: Dict[Path, bytes] = {}
    for tool, paths in sorted(by_owner.items()):
        part.items += items(tool, components(paths), tables)
    if not by_owner:
        part.skipped = "no tool is installed here"
    return part
