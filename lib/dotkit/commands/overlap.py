"""What the dotfiles repository has that the dotkit base has too (BEHAVIOR.md, rows Ov).

Three kinds of overlap:
- A tool: a name in a list of both, or a mise key of both. For mise, the
  key of the repository replaces the key of the base.
- A config file: a link of the repository that replaces a base link. The
  manifest of the system (etc/dotkit/links.json, from nix/links.nix) has the
  base links, the links of the configuration, and the layers: the file of
  yours that a tool reads after a base file.
- A file that an apply moved aside: TARGET.before-dotkit (the
  backupFileExtension of Home Manager), next to a link or a directory above it.

apply and upgrade show the overlaps that are new or changed since the last
report, and remember them in ~/.local/state/dotkit/overlaps.json; for the
others, one line. doctor shows all of them.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..core import lists, mise_toml, ui
from ..core.env import Env

MANIFEST = "etc/dotkit/links.json"
ACTIVE = Path("/run/current-system")
# A diff is shown when at least this part of the lines is the same and it changes at most MAX_CHANGED lines.
# Else the files are too different to read as a diff: the report gives the diff command.
MIN_SAME = 0.5
MAX_CHANGED = 40
LISTS = (("nix", "nix/packages.txt", "nix"), ("formula", "nix/brews.txt", "brew"), ("cask", "nix/casks.txt", "cask"))


@dataclass
class Overlap:
    key: str  # what it is about, unique in a report: mise:node, link:.npmrc
    where: str  # the file of the repository or the path in the home directory
    text: str
    detail: List[str] = field(default_factory=list)
    # The state of the two sides: a change makes the overlap new again.
    fingerprint: str = ""


def _hash(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else json.dumps(p, sort_keys=True, default=str).encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


def _tilde(path: str, home: Path) -> str:
    return "~" + path[len(str(home)):] if path == str(home) or path.startswith(f"{home}/") else path


def _short(path: str, env: Env, dotkit: Path) -> str:
    """A source path for a message: relative in the repository, "dotkit FILE" in the base, else as it is."""
    for root, label in ((env.repo, ""), (dotkit, "dotkit ")):
        if path.startswith(f"{root}/"):
            return label + path[len(str(root)) + 1:]
    return _tilde(path, env.home)


# --- tools


def tools(env: Env, dotkit: Path) -> List[Overlap]:
    out = []
    for kind, rel, prefix in LISTS:
        base = set(lists.names(dotkit / rel))
        for name in lists.names(env.repo / rel):
            if name in base:
                out.append(Overlap(f"{kind}:{name}", rel, f"{name} is in the dotkit base too. "
                                   f"Remove yours: dotkit remove {prefix}:{name}"))
    rel = "config/mise.toml"
    try:
        base_tools = mise_toml.tools(dotkit / rel)
        repo_tools = mise_toml.tools(env.repo / rel)
    except mise_toml.InvalidToml as e:
        ui.warn(f"{e} The mise tools are not compared with the base.")
        return out
    for key, value in repo_tools.items():
        if key not in base_tools:
            continue
        base_value = base_tools[key]
        if value == base_value:
            text = f"{key} is the same in the dotkit base. Remove yours: dotkit remove {key}"
        else:
            text = (f"{key} = {mise_toml.inline(value)} replaces {mise_toml.inline(base_value)} of the dotkit base.")
        out.append(Overlap(f"mise:{key}", rel, text, fingerprint=_hash(value, base_value)))
    return out


# --- config files


def load_manifest(system: Path) -> Optional[dict]:
    """The manifest of a system; None when it has none (a release of dotkit before the manifest)."""
    path = system / MANIFEST
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        ui.warn(f"cannot read {path}: {e}. The config files are not compared with the base.")
        return None
    if not valid_manifest(data):
        ui.warn(f"{path} has a format that this dotkit does not know. The config files are not compared.")
        return None
    return data


def valid_manifest(data: object) -> bool:
    return isinstance(data, dict) and data.get("version") == 1 and all(
        isinstance(data.get(k), dict) for k in ("base", "layers", "links"))


def _read(path: str) -> Optional[bytes]:
    try:
        return Path(path).read_bytes() if os.path.isfile(path) else None
    except OSError:
        return None


def _lines(data: bytes) -> Optional[List[str]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.split("\n")
    # The header about the base file, and the one of dotkit own instead of it, do not count.
    if lines[0].startswith(("# dotkit base", "# From the dotkit base")):
        lines = lines[next((i for i, line in enumerate(lines) if not line.startswith("#")), len(lines)):]
    text = "\n".join(lines)
    # The spaces at the ends of lines and the blank lines at both ends do not count.
    return [line.rstrip() for line in text.strip("\n").rstrip().split("\n")] if text.strip() else []


@dataclass
class Comparison:
    kind: str  # "same", "diff" (similar: detail is the diff), "different", or "unknown" (not text, or missing)
    detail: List[str]
    changed: int  # the lines of the diff that start with + or -
    fingerprint: str


def compare(base_path: str, yours_path: str, base_label: str, yours_label: str) -> Comparison:
    a, b = _read(base_path), _read(yours_path)
    fingerprint = _hash(a or b"", b or b"", yours_label)
    if a is None or b is None:
        return Comparison("unknown", [], 0, fingerprint)
    la, lb = _lines(a), _lines(b)
    if la is None or lb is None:
        return Comparison("same" if a == b else "unknown", [], 0, fingerprint)
    if la == lb:
        return Comparison("same", [], 0, fingerprint)
    same = difflib.SequenceMatcher(None, la, lb, autojunk=False).ratio()
    diff = list(difflib.unified_diff(la, lb, base_label, yours_label, n=1, lineterm=""))
    changed = sum(1 for line in diff[2:] if line[:1] in "+-")
    if same >= MIN_SAME and changed <= MAX_CHANGED:
        return Comparison("diff", diff, changed, fingerprint)
    return Comparison("different", [f"{round(same * 100)}% of the lines are the same. "
                                    f"Compare them: diff -u {base_path} {yours_path}"], changed, fingerprint)


def links(env: Env, manifest: dict, dotkit: Path) -> List[Overlap]:
    base, effective, layers = manifest.get("base") or {}, manifest.get("links") or {}, manifest.get("layers") or {}
    out = []
    for target in sorted(base):
        source = effective.get(target, base[target])
        if source == base[target]:
            continue
        where = f"~/{target}"
        layer = layers.get(target)
        if source is None:
            text = "has no link: your configuration sets it to null, so the base file is off."
            if layer:
                text += f" Its tool reads ~/{layer} after the base file: put your settings there instead."
            out.append(Overlap(f"link:{target}", where, text, fingerprint=_hash(None)))
            continue
        yours = _short(source, env, dotkit)
        c = compare(base[target], source, _short(base[target], env, dotkit), yours)
        if c.kind == "same":
            out.append(Overlap(f"link:{target}", where, f"your {yours} is the same as the base file. "
                               f"Give it back: dotkit own --release {where}", fingerprint=c.fingerprint))
            continue
        text = f"your {yours} replaces the base file" + {
            "diff": f"; {c.changed} lines differ.", "different": ", and differs from it a lot.", "unknown": "."}[c.kind]
        if layer:
            text += (f" Its tool reads ~/{layer} after the base file: move your changes there, "
                     f"and remove your link of {where}.")
        out.append(Overlap(f"link:{target}", where, text, c.detail, c.fingerprint))
    return out


def _content(path: Path) -> str:
    """The fingerprint of a file, a link, or a directory (its names)."""
    try:
        if path.is_symlink():
            return _hash("link", os.readlink(path))
        if path.is_dir():
            return _hash("dir", sorted(str(p.relative_to(path)) for p in path.rglob("*")))
        return _hash(path.read_bytes())
    except OSError:
        return ""


def _managed(path: Path) -> bool:
    """A link of Home Manager: it points into the Nix store."""
    try:
        return path.is_symlink() and os.readlink(path).startswith("/nix/store/")
    except OSError:
        return False


def moved(env: Env, manifest: dict, dry_run: bool) -> List[Overlap]:
    """The files that an apply moved aside, and with dry_run the ones that the apply will move."""
    ext = manifest.get("backupFileExtension")
    if not ext:
        return []
    layers = manifest.get("layers") or {}
    targets = [t for t, s in (manifest.get("links") or {}).items() if s is not None]
    out, seen = [], set()
    for target in sorted(targets):
        parts = target.split("/")
        for i in range(len(parts), 0, -1):
            rel = "/".join(parts[:i])
            backup = env.home / f"{rel}.{ext}"
            if rel in seen or not os.path.lexists(backup):
                continue
            seen.add(rel)
            text = f"an apply moved your file aside for the link ~/{rel}."
            if i == len(parts) and target in layers:
                text += f" Move the settings that you need to ~/{layers[target]}, then delete it."
            else:
                text += f" Compare it with ~/{rel}, take what you need, then delete it."
            out.append(Overlap(f"moved:{rel}", f"~/{rel}.{ext}", text, fingerprint=_content(backup)))
        path = env.home / target
        if dry_run and os.path.lexists(path) and not _managed(path):
            text = f"apply moves your file to ~/{target}.{ext} and links the file of your configuration."
            if target in layers:
                text += f" Move your settings to ~/{layers[target]} first."
            out.append(Overlap(f"replaced:{target}", f"~/{target}", text))
    return out


# --- the report


def find(env: Env, manifest: Optional[dict], dry_run: bool = False) -> List[Overlap]:
    """The overlaps; only the tools without a manifest."""
    dotkit = Path(manifest["dotkit"]) if manifest and isinstance(manifest.get("dotkit"), str) else env.dotkit
    out = tools(env, dotkit)
    if manifest is not None:
        out += links(env, manifest, dotkit) + moved(env, manifest, dry_run)
    return out


def _state_file(env: Env) -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(env.home / ".local/state")
    return Path(state) / "dotkit/overlaps.json"


def _seen(env: Env) -> Dict[str, str]:
    try:
        data = json.loads(_state_file(env).read_bytes())
    except (OSError, ValueError):
        return {}
    seen = data.get("seen") if isinstance(data, dict) else None
    return seen if isinstance(seen, dict) else {}


def _remember(env: Env, found: List[Overlap]) -> None:
    path = _state_file(env)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "seen": {o.key: o.fingerprint for o in found}}, indent=1) + "\n",
                       encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        ui.warn(f"cannot write {path}: {e}.")


def _color(line: str, stream) -> str:
    """A line of a diff in colors, when stream is a terminal and NO_COLOR is not set."""
    if not stream.isatty() or os.environ.get("NO_COLOR") or line.startswith(("+++", "---")):
        return line
    code = {"+": "32", "-": "31", "@": "36"}.get(line[:1])
    return f"\033[{code}m{line}\033[0m" if code else line


def show(found: List[Overlap], stdout: bool = False) -> None:
    """The overlaps on stderr, or on stdout for doctor."""
    write, stream = (ui.out, sys.stdout) if stdout else (ui.note, sys.stderr)
    width = max(ui.width(o.where) for o in found)
    for o in found:
        write(f"  {ui.pad(o.where, width)}  {o.text}")
        for line in o.detail:
            write("      " + _color(line, stream))


def report(env: Env, system: Path, dry_run: bool) -> None:
    """After apply and upgrade: the new and changed overlaps, then one line for the others."""
    found = find(env, load_manifest(system), dry_run)
    seen = _seen(env)
    new = [o for o in found if seen.get(o.key) != o.fingerprint]
    if new:
        ui.warn("your dotfiles repeat or replace parts of the dotkit base:")
        show(new)
    known = len(found) - len(new)
    if known:
        ui.note(f"{'And' if new else 'Your dotfiles have'} {known} known overlap{'s' if known > 1 else ''} with "
                f"the dotkit base. dotkit doctor lists {'them' if known > 1 else 'it'}.")
    if not dry_run and ({o.key: o.fingerprint for o in found} != seen):
        _remember(env, found)


def doctor(env: Env, system: Path = ACTIVE) -> int:
    """All overlaps of the active system."""
    manifest = load_manifest(system)
    found = find(env, manifest)
    if manifest is None:
        ui.note(f"{system} has no link manifest, so the config files are not compared. Run dotkit apply.")
    if not found:
        ui.out("Your dotfiles do not repeat or replace any part of the dotkit base.")
        return 0
    ui.out("Your dotfiles repeat or replace these parts of the dotkit base:")
    show(found, stdout=True)
    return 0
