"""A fake Mac for the tests: a home directory in a temporary directory and canned command output."""

from __future__ import annotations

import json
import shutil
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
        self.root = Path(tempfile.mkdtemp(prefix="dotkit-migrate-test-"))
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
        shutil.rmtree(self.root, ignore_errors=True)


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
