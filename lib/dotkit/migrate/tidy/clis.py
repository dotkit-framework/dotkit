"""The scan of dotkit tidy for command-line tools: packages, files, and version managers (TIDY.md, section 8.1).

Read-only, with the rules of migrators/: no writes, and commands only
through ctx.runner.
"""

from __future__ import annotations

import os
import pwd
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from .. import catalog
from .._compat import tomllib
from ..catalog import BREW_ENV
from ..context import Context, is_managed, read_json
from ..migrators.node_tools import node_module_roots, bin_names
from ..migrators.tools import env_path

# /usr/local/bin of this Mac. The tests use another directory.
USR_LOCAL_BIN = Path("/usr/local/bin")

# The kinds whose lines are packages, with `uninstall`; the others have `trash`.
PACKAGES = ("brew", "npm", "pipx", "uv", "cargo")

# A link into one of these is not a file of its own: another row of the scan has it, or an app or Nix owns it.
_OWNED_PARTS = (("Cellar",), ("Caskroom",), ("pipx", "venvs"), ("uv", "tools"), ("lib", "node_modules"))


@dataclass
class Tool:
    path: str  # absolute: the package directory, the file, or the directory of a version manager
    kind: str  # brew, npm, pipx, uv, cargo, go, file, or manager
    name: str  # the formula, package, crate, file, or version manager
    where: str = ""  # for the comment: "nvm v20.11.0", "Homebrew node"
    commands: List[str] = field(default_factory=list)
    uninstall: List[str] = field(default_factory=list)  # the command; [] for none
    path_first: str = ""  # a directory that goes first in PATH for the uninstall: the bin of that node
    target: str = ""  # file: the target of the link; "" for a file that is not a link
    writable: bool = True  # you can move it to the Trash without sudo
    owner: str = ""

    @property
    def thing(self) -> str:
        return f"{self.kind}:{self.name}" if self.kind in PACKAGES or self.kind == "go" else ""


def owner(p: Path) -> str:
    """The user that owns a path (the link itself, for a link)."""
    try:
        uid = p.lstat().st_uid
    except OSError:
        return ""
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _movable(p: Path) -> bool:
    """You can move it: its directory is writable, and a directory itself too."""
    return os.access(p.parent, os.W_OK) and (not p.is_dir() or p.is_symlink() or os.access(p, os.W_OK))


def _owned(target: str) -> bool:
    """The link target belongs to Nix, an app, or a package that another row has."""
    if target.startswith("/nix/store/") or ".app/" in target + "/":
        return True
    parts = Path(target).parts
    return any(tuple(parts[i:i + len(o)]) == o for o in _OWNED_PARTS for i in range(len(parts)))


def _brew(ctx: Context, out: List[Tool]) -> None:
    brew = ctx.runner.which("brew", ("/opt/homebrew/bin",))
    if not brew:
        return
    for name in (ctx.runner.run([brew, "leaves", "--installed-on-request"], timeout=300, env=BREW_ENV) or "").split():
        short = name.rsplit("/", 1)[-1]
        out.append(Tool(str(ctx.brew_prefix / "Cellar" / short), "brew", name, "Homebrew",
                        uninstall=[brew, "uninstall", name]))


def _npm(ctx: Context, out: List[Tool]) -> None:
    for root, label in node_module_roots(ctx):
        prefix = root.parent.parent
        npm = prefix / "bin/npm"
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        found: List[Path] = []
        for p in entries:
            if p.name.startswith("@") and p.is_dir() and not p.is_symlink():
                found += sorted(p.iterdir())
            elif not p.name.startswith("."):
                found.append(p)
        for p in found:
            if p.is_symlink():  # npm link: a local package, not an install
                continue
            meta = read_json(p / "package.json")
            if not isinstance(meta, dict) or not meta.get("bin"):
                continue
            name = str(meta.get("name") or p.name)
            if name in ("npm", "corepack"):  # they come with node
                continue
            has_npm = os.access(npm, os.X_OK)
            out.append(Tool(str(p), "npm", name, label, bin_names(name, meta["bin"]),
                            [str(npm), "uninstall", "-g", name] if has_npm else [],
                            str(prefix / "bin") if has_npm else ""))


def _python(ctx: Context, out: List[Tool]) -> None:
    pipx = ctx.runner.which("pipx", ("/opt/homebrew/bin", str(ctx.home / ".local/bin")))
    for home in (env_path(ctx, "PIPX_HOME", ".local/pipx"), ctx.home / "Library/Application Support/pipx"):
        for meta_path in sorted(home.glob("venvs/*/pipx_metadata.json")):
            meta = read_json(meta_path) or {}
            main = meta.get("main_package") or {}
            name = str(main.get("package") or meta_path.parent.name)
            apps = [str(a) for a in main.get("apps") or [] if isinstance(a, str)]
            out.append(Tool(str(meta_path.parent), "pipx", name, "pipx", apps,
                            [pipx, "uninstall", name] if pipx else []))
    uv = ctx.runner.which("uv", ("/opt/homebrew/bin", str(ctx.home / ".local/bin")))
    for receipt in sorted(env_path(ctx, "UV_TOOL_DIR", ".local/share/uv/tools").glob("*/uv-receipt.toml")):
        try:
            data = tomllib.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tool = data.get("tool") or {}
        reqs = tool.get("requirements") or []
        first = reqs[0] if reqs and isinstance(reqs[0], dict) else {}
        name = str(first.get("name") or receipt.parent.name)
        commands = [str(e["name"]) for e in tool.get("entrypoints") or [] if isinstance(e, dict) and e.get("name")]
        out.append(Tool(str(receipt.parent), "uv", name, "uv tool", commands,
                        [uv, "tool", "uninstall", name] if uv else []))


def _cargo(ctx: Context, out: List[Tool]) -> Set[str]:
    """The crates of cargo install; returns the names of their commands in $CARGO_HOME/bin."""
    home = env_path(ctx, "CARGO_HOME", ".cargo")
    data = read_json(home / ".crates2.json")
    installs = data.get("installs") if isinstance(data, dict) else None
    if not isinstance(installs, dict):
        return set()
    cargo = home / "bin/cargo"
    argv0 = str(cargo) if os.access(cargo, os.X_OK) else ctx.runner.which("cargo") or ""
    bins: Set[str] = set()
    for key, meta in sorted(installs.items()):
        name = key.split(" ", 1)[0]
        commands = [str(b) for b in (meta or {}).get("bins") or [] if isinstance(b, str)] if isinstance(meta, dict) \
            else []
        bins |= set(commands)
        first = home / "bin" / (commands[0] if commands else name)
        out.append(Tool(str(first), "cargo", name, "cargo install", commands,
                        [argv0, "uninstall", name] if argv0 else []))
    return bins


def _go(ctx: Context, out: List[Tool]) -> None:
    gobin = ctx.env.get("GOBIN")
    d = Path(gobin) if gobin else env_path(ctx, "GOPATH", "go") / "bin"
    for p in _files(d):
        out.append(Tool(str(p), "go", p.name, "go install", [p.name], writable=_movable(p), owner=owner(p)))


def _files(d: Path) -> List[Path]:
    try:
        return [p for p in sorted(d.iterdir()) if (p.is_file() or p.is_symlink()) and not p.name.startswith(".")]
    except OSError:
        return []


def _plain(ctx: Context, out: List[Tool], cargo_bins: Set[str]) -> None:
    dirs = [ctx.home / ".local/bin", ctx.home / "bin", USR_LOCAL_BIN, env_path(ctx, "CARGO_HOME", ".cargo") / "bin"]
    seen: Set[str] = set()
    for d in dirs:
        real = os.path.realpath(d)
        if real in seen:
            continue
        seen.add(real)
        for p in _files(d):
            target = ""
            if p.is_symlink():
                try:
                    target = os.path.normpath(os.path.join(str(d), os.readlink(p)))
                except OSError:
                    continue
                if _owned(target) or os.path.dirname(target) == str(d) or is_managed(p):
                    continue
            elif not os.access(p, os.X_OK):
                continue
            if d.name == "bin" and d.parent == env_path(ctx, "CARGO_HOME", ".cargo") and p.name in cargo_bins:
                continue
            out.append(Tool(str(p), "file", p.name, str(d), [p.name], target=target, writable=_movable(p),
                            owner=owner(p)))


def _managers(ctx: Context, out: List[Tool]) -> None:
    for spec in catalog.VERSION_MANAGERS:
        if not spec.detect:
            continue
        d: Optional[Path] = catalog.manager_dir(spec, ctx.home, ctx.env)
        if d is not None and d.is_dir() and not is_managed(d):
            out.append(Tool(str(d), "manager", spec.name, "version manager", writable=_movable(d),
                            owner=owner(d)))
    for fw in catalog.FRAMEWORKS:
        for rel in fw.paths:
            p = ctx.home / rel
            if p.is_dir() and not is_managed(p):
                out.append(Tool(str(p), "manager", fw.name, "zsh framework", writable=_movable(p), owner=owner(p)))
                break


def scan(ctx: Context) -> List[Tool]:
    out: List[Tool] = []
    _brew(ctx, out)
    _npm(ctx, out)
    _python(ctx, out)
    bins = _cargo(ctx, out)
    _go(ctx, out)
    _plain(ctx, out, bins)
    _managers(ctx, out)
    # One line per path: the first row wins (a Go file in ~/bin is go, not file).
    unique: Dict[str, Tool] = {}
    for t in out:
        unique.setdefault(t.path, t)
    return list(unique.values())
