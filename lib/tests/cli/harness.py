"""The sandbox of the parity tests: a dotkit, a dotfiles repository, a home, and fake tools.

run(impl, scenario) copies an implementation of the dotkit command into a new
sandbox, runs one scenario in it, and returns the Result: the exit code,
stdout, stderr (or the terminal text), the changed files, and the calls of
the fake tools. Paths in the result start with @SB, the sandbox. The Bash
scripts and the Python code must give the same Result for each scenario
(CLI-PLAN.md, section 4.3); the goldens in golden/ are the reference.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import sys
import termios
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from tests import fake

ROOT = fake.ROOT
HERE = Path(__file__).resolve().parent
TOOLS = ("nix", "brew", "mise", "mas", "curl", "sudo", "darwin-rebuild")
JQ = shutil.which("jq")

# The dotkit base of the sandbox: small lists, so that the goldens do not
# change when the real base lists change.
BASE = {
    "dotkit/nix/packages.txt": "# The base CLIs.\nripgrep\nfd\n",
    "dotkit/nix/brews.txt": "wget  # a comment\n",
    "dotkit/nix/casks.txt": "ghostty\n",
    "dotkit/nix/taps.txt": "",
    "dotkit/config/mise.toml": '[tools]\nnode = "lts"\n"npm:@dkt-ai/dkt-code" = "latest"\n',
    "dotkit/flake.lock": json.dumps({"nodes": {
        "root": {"inputs": {"nixpkgs": "nixpkgs", "nix-darwin": "nix-darwin"}},
        "nixpkgs": {"locked": {"lastModified": 1760000000, "rev": "aaaaaaa1111111"}},
        "nix-darwin": {"locked": {"lastModified": 1760000000, "rev": "bbbbbbb2222222"}},
    }, "root": "root", "version": 7}, indent=2) + "\n",
}
REPO = {
    "repo/nix/packages.txt": "jq\n",
    "repo/nix/brews.txt": "",
    "repo/nix/casks.txt": "zed",  # no final newline
    "repo/nix/taps.txt": "",
    "repo/nix/mas.txt": "# apps\n409183694 Keynote\n",
    "repo/nix/flake.lock": json.dumps({"nodes": {
        "root": {"inputs": {"dotkit": "dotkit", "nixpkgs": ["dotkit", "nixpkgs"]}},
        "dotkit": {"locked": {"lastModified": 1760000000, "rev": "ccccccc3333333"}},
    }, "root": "root", "version": 7}, indent=2) + "\n",
    "repo/config/mise.toml": ('# My tools.\n[tools]\n"github:modem-dev/dkt-hunk" = "latest"\n'
                              '"npm:cowsay" = { version = "1.6.0" }\n\n[settings]\nexperimental = true\n'),
}


@dataclass
class Scenario:
    name: str
    argv: List[str]
    rules: List[dict] = field(default_factory=list)
    # Files to write before the run, by path in the sandbox; None removes the file.
    files: Dict[str, Optional[str]] = field(default_factory=dict)
    env: Dict[str, str] = field(default_factory=dict)
    # Text to type: stdin and stderr are a terminal, without echo. None: stdin is /dev/null. A list: each
    # part after the first goes to the terminal when new text shows on it (Ctrl-C after the list shows).
    tty: Union[str, List[str], None] = None
    # The calls run at the same time (the lookups of a bare name): compare them sorted.
    unordered: bool = False
    # Set DOTKIT_DIR to a checkout with its own flake.lock.
    dotkit_dir: bool = False
    # Run without DOTFILES.
    no_dotfiles: bool = False
    # The reason to skip the scenario on this Mac, or None.
    skip: Callable[[], Optional[str]] = lambda: None
    # What the scenario checks: the rows of PARITY.md.
    rows: List[str] = field(default_factory=list)
    # A deviation (DV...) or a Bash bug (B...) of PARITY.md. test_parity.COMPARE says what the other
    # implementations must match; a Bash bug runs with the Bash scripts only.
    deviation: Optional[str] = None


@dataclass
class Impl:
    """An implementation of the dotkit command: the files to copy into the sandbox's dotkit. In the copy of
    dotkit, /usr/bin/python3 becomes the Python of the tests, so that the tests cover each Python."""
    name: str
    files: List[str]
    needs_jq: bool

    def missing(self) -> Optional[str]:
        if not os.access("/bin/bash", os.X_OK):
            return "no /bin/bash"
        if self.needs_jq and not JQ:
            return "no jq"
        gone = [f for f in self.files if not (ROOT / f).exists()]
        return f"no {', '.join(gone)}" if gone else None


# Until step 7 of CLI-PLAN.md, the Bash dotkit runs the commands that are not in Python yet, with jq.
PYTHON = Impl("python", ["dotkit", "scripts/nix.sh", "lib/run.py", "lib/dotkit"], needs_jq=True)


def _tool_script(tool: str) -> str:
    return f'#!/bin/sh\nexec "{sys.executable}" -I -B "{HERE / "fakebin.py"}" {tool} "$@"\n'


def _write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def install_fakes(root: Path, rules: List[dict]) -> Dict[str, str]:
    """The fake tools in root/bin with the rules in root/fake; returns PATH and FAKE_DIR for them."""
    for tool in TOOLS:
        _write(root / "bin" / tool, _tool_script(tool), 0o755)
    _write(root / "fake/rules.json", json.dumps(rules, indent=1))
    return {"PATH": f"{root / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin", "FAKE_DIR": str(root / "fake")}


def fake_calls(root: Path) -> List[dict]:
    log = root / "fake/log.jsonl"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def setup(sb: Path, impl: Impl, s: Scenario) -> None:
    for rel in impl.files:
        src = ROOT / rel
        dst = sb / "dotkit" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "tests"))
        else:
            shutil.copy2(src, dst)
    launcher = sb / "dotkit/dotkit"
    launcher.write_text(launcher.read_text(encoding="utf-8").replace("/usr/bin/python3", sys.executable),
                        encoding="utf-8")
    for rel, text in {**BASE, **REPO}.items():
        _write(sb / rel, text)
    _write(sb / "dotkit/migrate/dotkit-migrate", _tool_script("dotkit-migrate"), 0o755)
    install_fakes(sb, s.rules)
    if impl.needs_jq and JQ:
        os.symlink(JQ, sb / "bin/jq")
    if s.dotkit_dir:
        _write(sb / "checkout/flake.lock", BASE["dotkit/flake.lock"])
    for rel, text in s.files.items():
        p = sb / rel
        if text is None:
            if p.is_symlink() or p.exists():
                p.unlink()
        else:
            _write(p, text.replace("@SB", str(sb)), 0o755 if rel.startswith("bin/") else 0o644)
    (sb / "home").mkdir(exist_ok=True)
    (sb / "tmp").mkdir()


def _snapshot(sb: Path) -> Dict[str, str]:
    out = {}
    for top in ("repo", "home", "checkout", "dotkit/nix", "dotkit/config"):
        base = sb / top
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            rel = str(p.relative_to(sb))
            if p.is_symlink():
                out[rel] = "-> " + os.readlink(p)
            elif p.is_file():
                out[rel] = p.read_text(encoding="utf-8", errors="replace")
    return out


def _controlling_terminal() -> None:
    """In the child, after setsid: stdin becomes the controlling terminal, for /dev/tty and Ctrl-C."""
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def run_tty(argv: List[str], env: Dict[str, str], cwd: Path, text: Union[str, List[str]]) -> tuple[int, str, str]:
    """stdin and stderr on a terminal of 120 columns without echo, stdout on a pipe. The terminal is the
    controlling terminal of the command, so \x03 in text is Ctrl-C. Returns the exit code, stdout, and the
    text of the terminal."""
    master, slave = pty.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    p = subprocess.Popen(argv, env=env, cwd=cwd, stdin=slave, stdout=subprocess.PIPE, stderr=slave,
                         start_new_session=True, preexec_fn=_controlling_terminal)
    os.close(slave)
    parts = [text] if isinstance(text, str) else list(text)
    os.write(master, parts.pop(0).encode())
    out, term = b"", b""
    while True:
        ready, _, _ = select.select([master, p.stdout], [], [], 120)
        if not ready:
            p.kill()
            raise TimeoutError(argv)
        if p.stdout in ready:
            chunk = os.read(p.stdout.fileno(), 65536)
            if chunk:
                out += chunk
            elif p.poll() is not None:
                break
        if master in ready:
            try:
                chunk = os.read(master, 65536)
            except OSError:  # EIO: the terminal has no process left
                chunk = b""
            term += chunk
            if chunk and parts:
                os.write(master, parts.pop(0).encode())
            if not chunk and p.poll() is not None:
                break
    p.wait()
    p.stdout.close()
    os.close(master)
    return p.returncode, out.decode(), term.decode().replace("\r\n", "\n")


def _normalize(text: str, sb: Path) -> str:
    text = text.replace(str(sb), "@SB")
    # The names of temporary files differ between mktemp and Python.
    return re.sub(r"@SB/tmp/[^\s'\"/]+", "@SB/tmp/*", text)


def run(impl: Impl, s: Scenario) -> dict:
    # Resolved: /var is a link to /private/var, and realpath in the scripts gives the second form.
    sb = fake.tempdir("cli").resolve()
    try:
        setup(sb, impl, s)
        env = {"HOME": str(sb / "home"), "TMPDIR": str(sb / "tmp"), "LC_ALL": "en_US.UTF-8",
               "PATH": f"{sb / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin", "FAKE_DIR": str(sb / "fake"),
               "USER": os.environ.get("USER", "")}
        if not s.no_dotfiles:
            env["DOTFILES"] = str(sb / "repo")
        if s.dotkit_dir:
            env["DOTKIT_DIR"] = str(sb / "checkout")
        env.update({k: v.replace("@SB", str(sb)) for k, v in s.env.items()})
        before = _snapshot(sb)
        argv = [str(sb / "dotkit/dotkit"), *s.argv]
        if s.tty is None:
            r = subprocess.run(argv, env=env, cwd=sb / "home", stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=300)
            code, out, err = r.returncode, r.stdout, r.stderr
        else:
            code, out, err = run_tty(argv, env, sb / "home", s.tty)
        after = _snapshot(sb)
        files = {k: after.get(k) for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)}
        calls = fake_calls(sb)
        if s.unordered:
            calls.sort(key=lambda c: json.dumps(c, sort_keys=True))
        result = {
            "argv": s.argv,
            "exit": code,
            "stdout": out,
            ("terminal" if s.tty is not None else "stderr"): err,
            "files": files,
            "calls": calls,
            "tmp_left": len(list((sb / "tmp").iterdir())),
        }
        return json.loads(_normalize(json.dumps(result), sb))
    finally:
        fake.remove(sb)
