"""A fake Mac for the tests: a home directory in a temporary directory and canned command output."""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from dotkit_migrate import base, context, registry

ROOT = Path(__file__).resolve().parents[2]

# Fake tokens in the formats of real ones. They are built at run time, so
# that no secret scanner flags this file. fill() puts them into a text.
TOKENS = {
    "@GH@": "gh" + "p_" + "A1b2C3d4" * 4 + "Zz9y",
    "@GHO@": "gh" + "o_" + "A1b2C3d4" * 4,
    "@NPM@": "np" + "m_" + "Q7w8E9r0" * 4 + "Tt5u",
    "@OPENAI@": "sk" + "-proj-" + "M3n4B5v6" * 3,
    "@SLACK@": "xo" + "xb-123456789012-" + "abcdefghij",
    "@AWS@": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "@STRIPE@": "sk" + "_live_" + "4eC39HqLyjWDarjtT1zdp7dc",
    "@HEX40@": "0f9e8d7c6b5a" + "4b3c2d1e0f" + "9a8b7c6d5e4f3a2b1c",
    "@RANDOM@": "q8Zr2" + "Lm9vXk4Tn7Wb" + "3Jy6Hd1Pf",
}


def fill(text: str) -> str:
    for k, v in TOKENS.items():
        text = text.replace(k, v)
    return text


# Every temporary directory of the tests is made by tempdir(), named
# dotkit-migrate-<kind>-<pid>-..., and removed by remove(). check_temp() (run
# last by tests/__init__.py) finds the ones a test left behind.
TMP_PREFIX = "dotkit-migrate-"
_made: list[Path] = []


def tempdir(kind: str = "test") -> Path:
    """A new temporary directory. The caller removes it with remove(), usually with addCleanup."""
    p = Path(tempfile.mkdtemp(prefix=f"{TMP_PREFIX}{kind}-{os.getpid()}-"))
    _made.append(p)
    return p


def remove(path: Path) -> None:
    """Removes a temporary directory, also when parts of it are read-only or unreadable. Raises when it stays."""
    if not os.path.lexists(path):
        return
    if os.path.isdir(path) and not os.path.islink(path):
        os.chmod(path, 0o700)
        for root, dirs, _ in os.walk(path):
            for d in dirs:
                p = os.path.join(root, d)
                if not os.path.islink(p):  # never change the mode of what a link points to
                    os.chmod(p, 0o700)
        shutil.rmtree(path)
    else:
        os.unlink(path)


def temp_entries(tmp: str | None = None) -> set[str]:
    """The dotkit-migrate-* names in the temporary directory."""
    return {n for n in os.listdir(tmp or tempfile.gettempdir()) if n.startswith(TMP_PREFIX)}


def _pid_of(name: str) -> int | None:
    """The pid in a name of tempdir(), dotkit-migrate-<kind>-<pid>-...; None when it has none."""
    m = re.match(re.escape(TMP_PREFIX) + r"[a-z]+-(\d+)-", name)
    return int(m.group(1)) if m else None


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def check_temp(before: set[str], made: list[Path] | None = None,
               tmp: str | None = None) -> tuple[list[str], list[str]]:
    """What the tests left in the temporary directory since before: (removed, kept).

    A leak is a directory of tempdir() that still exists, or an entry that is new since before and
    has this process's pid or a dead one (this suite or one of its children): it is removed. A new
    entry whose pid is alive belongs to a run at the same time and is ignored. A new entry without
    a pid in its name is a leak too, but it is never removed, only counted (kept). An entry that was
    there before with a dead pid gets a note on stderr.
    """
    tmp = tmp or tempfile.gettempdir()
    made = _made if made is None else made
    left = {p for p in made if os.path.lexists(p)}
    kept = set()
    now = temp_entries(tmp)
    for name in now - before:
        pid = _pid_of(name)
        if pid is None:
            kept.add(name)
        elif pid == os.getpid() or not _alive(pid):
            left.add(Path(tmp, name))
    for path in left:
        remove(path)
    for name in sorted(now & before):
        pid = _pid_of(name)
        if pid is not None and pid != os.getpid() and not _alive(pid):
            print(f"note: {Path(tmp, name)} is from an earlier run (pid {pid}); remove it by hand.",
                  file=sys.stderr)
    return sorted({p.name for p in left}), sorted(kept)


def _remove_made() -> None:
    """At exit, for a run that stopped before check_temp: remove what tempdir() made."""
    for p in _made:
        try:
            remove(p)
        except OSError:
            pass


atexit.register(_remove_made)


class FakeRunner:
    """Answers commands by "name args", for example "brew leaves --installed-on-request"."""

    def __init__(self, commands: dict[str, str] | None = None, paths: dict[str, str] | None = None):
        self.commands = commands or {}
        self.paths = paths or {}
        self.calls: list[str] = []

    def which(self, name: str, extra: tuple[str, ...] = ()) -> str | None:
        del extra
        return self.paths.get(name)

    def run(self, argv: list[str], timeout: float = 120, env: dict[str, str] | None = None) -> str | None:
        del timeout, env
        key = " ".join([Path(argv[0]).name] + list(argv[1:]))
        self.calls.append(key)
        return self.commands.get(key)


class Home:
    """A temporary home directory. write() creates files in it."""

    def __init__(self) -> None:
        self.root = tempdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.apps = self.root / "Applications"
        self.apps.mkdir()
        self.prefix = self.root / "homebrew"
        self.prefix.mkdir()

    @classmethod
    def at(cls, root: Path) -> "Home":
        """A fake Mac in an existing directory, for a child process."""
        h = cls.__new__(cls)
        h.root = root
        h.home, h.apps, h.prefix = root / "home", root / "Applications", root / "homebrew"
        for d in (h.home, h.apps, h.prefix):
            d.mkdir(parents=True, exist_ok=True)
        return h

    def write(self, rel: str, text: str = "", mode: int | None = None) -> Path:
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        if mode is not None:
            p.chmod(mode)
        return p

    def json(self, rel: str, data) -> Path:
        return self.write(rel, json.dumps(data))

    def mkdir(self, rel: str) -> Path:
        p = self.home / rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    def cleanup(self) -> None:
        remove(self.root)


BREW = "/opt/homebrew/bin/brew"


def brew_runner(formulae=(), casks=(), taps=None, mas=None, extra=None) -> FakeRunner:
    commands = {
        "brew leaves --installed-on-request": "".join(f + "\n" for f in formulae),
        "brew list --cask --full-name -1": "".join(c + "\n" for c in casks),
        "brew tap-info --json --installed": json.dumps(taps or [
            {"name": "homebrew/core", "remote": "https://github.com/Homebrew/homebrew-core"},
        ]),
        "git config --global --includes --get user.name": "Jane Appleseed\n",
        "git config --global --includes --get user.email": "jane@example.com\n",
    }
    paths = {"brew": BREW, "git": "/usr/bin/git"}
    if mas is not None:
        paths["mas"] = "/opt/homebrew/bin/mas"
        commands["mas list"] = mas
    commands.update(extra or {})
    return FakeRunner(commands, paths)


def context_of(h: Home, runner: FakeRunner, env: dict[str, str] | None = None) -> context.Context:
    return context.Context(h.home, env or {}, runner, base.Base(ROOT), h.apps, h.prefix)


def session(h: Home, runner: FakeRunner, env: dict[str, str] | None = None, check=None,
            strict: bool = True) -> registry.Session:
    """The Session of a fake Mac. check is the check_syntax of the shell lines; None: no check.

    Strict by default: a crash or a conflict raises where it happens. The isolation tests turn it off.
    """
    return registry.Session(context_of(h, runner, env), strict=strict, check_syntax=check)
