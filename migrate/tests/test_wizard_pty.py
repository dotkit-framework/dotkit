"""Drive the wizard through a pseudo terminal, as a person would."""

import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path

CHILD = Path(__file__).resolve().parent / "wizard_child.py"
UP, DOWN, LEFT, RIGHT = "\x1b[A", "\x1b[B", "\x1b[D", "\x1b[C"
ENTER, SPACE, CTRL_U = "\r", " ", "\x15"


def _size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def drive(keys, term="xterm-256color", rows=30, cols=100, timeout=30.0):
    """Runs the child in a pty and sends the keys: text, or ("resize", rows, cols). Returns its result and output."""
    out = Path(tempfile.mkdtemp()) / "result.json"
    env = {k: v for k, v in os.environ.items() if k not in ("TERMINFO", "TERMINFO_DIRS")}
    env.update({"TERM": term, "ESCDELAY": "25", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        pid, fd = pty.fork()
    except OSError as e:
        raise unittest.SkipTest(f"no pseudo terminal: {e}") from e
    if pid == 0:
        try:
            _size(0, rows, cols)
            os.execve(sys.executable, [sys.executable, str(CHILD), str(out)], env)
        finally:
            os._exit(127)
    screen = bytearray()

    def drain(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.02)
            if r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                screen.extend(data)

    drain(1.5)  # the scan and curses start
    for key in keys:
        if isinstance(key, tuple):
            _size(fd, key[1], key[2])
            os.kill(pid, signal.SIGWINCH)
        else:
            try:
                os.write(fd, key.encode())
            except OSError:
                break  # the child has quit
        drain(0.12)
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        drain(0.1)
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            break
    else:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        raise AssertionError("the wizard did not finish. Output:\n" + screen.decode(errors="replace")[-3000:])
    drain(0.2)
    os.close(fd)
    text = screen.decode(errors="replace")
    if not out.exists():
        raise AssertionError(f"the child wrote no result (status {status}). Output:\n{text[-3000:]}")
    return json.loads(out.read_text()), text


class WizardPtyTests(unittest.TestCase):
    def run_wizard(self, keys, **kw):
        return drive(keys, **kw)

    def test_defaults(self):
        # repo, keymap, what to carry, config files, other config, plan
        result, screen = self.run_wizard([ENTER] * 6)
        self.assertEqual(result["mode"], "curses", screen[-2000:])
        self.assertTrue(result["ok"], screen[-2000:])
        c = result["choices"]
        self.assertEqual((c["repo"], c["keymap"]), ("~/.dotfiles", "emacs"))
        self.assertTrue(c["groups"]["formulae"] and c["groups"]["shell"])
        self.assertFalse(c["groups"]["cargo"])
        self.assertEqual(c["files"][".npmrc"], "keep")
        self.assertEqual(c["adopt"], [])
        for title in ("Where the dotfiles repository goes", "The zsh keymap", "What to carry over",
                      "Your config files", "Other config to keep in the repository", "The plan"):
            self.assertIn(title, screen)

    def test_changes(self):
        keys = [
            CTRL_U, *"/tmp/elsewhere", ENTER,  # outside the home directory: refused
            CTRL_U, *"~/dots", ENTER,
            DOWN, ENTER,  # vi
            SPACE, DOWN, SPACE, "x", ENTER,  # formulae and casks off; x does nothing
            SPACE, DOWN, RIGHT, ENTER,  # ghostty: layer -> replace; starship: replace -> dotkit
            SPACE, ENTER,  # adopt ~/.config/nvim
            "b", ENTER,  # back from the plan to other config, then on
            ENTER,  # the plan
        ]
        result, screen = self.run_wizard(keys)
        self.assertTrue(result["ok"], screen[-2000:])
        c = result["choices"]
        self.assertEqual((c["repo"], c["keymap"]), ("~/dots", "vi"))
        self.assertFalse(c["groups"]["formulae"])
        self.assertFalse(c["groups"]["casks"])
        self.assertTrue(c["groups"]["taps"])
        self.assertEqual(c["files"][".config/ghostty/config"], "replace")
        self.assertEqual(c["files"][".config/starship.toml"], "dotkit")
        self.assertEqual(c["adopt"], [".config/nvim"])
        self.assertIn("Use a directory in", screen)

    def test_keypad_keys(self):
        """Terminals in keypad mode, as curses asks, send ESC O B for the down arrow."""
        result, screen = self.run_wizard([ENTER, "\x1bOB", ENTER, "\x1bOB", SPACE, "q"])
        self.assertEqual(result["choices"]["keymap"], "vi", screen[-2000:])
        self.assertFalse(result["choices"]["groups"]["casks"])

    def test_quit(self):
        result, screen = self.run_wizard([ENTER, "q"])
        self.assertFalse(result["ok"], screen[-2000:])

    def test_small_window(self):
        result, screen = self.run_wizard([ENTER, ("resize", 30, 100), ENTER, "q"], rows=10, cols=40)
        self.assertFalse(result["ok"])
        self.assertIn("Make the window larger", screen)

    def test_plain_prompts(self):
        # repo, keymap, 9 groups (casks off), 6 config files, other config, the plan.
        lines = ["", "vi", "", "n", *[""] * 7, *[""] * 6, "nvim", "y"]
        result, screen = self.run_wizard([line + "\r" for line in lines], term="dumb")
        self.assertEqual(result["mode"], "plain")
        self.assertTrue(result["ok"], screen[-3000:])
        c = result["choices"]
        self.assertEqual(c["keymap"], "vi")
        self.assertTrue(c["groups"]["formulae"])
        self.assertFalse(c["groups"]["casks"])
        self.assertEqual(c["adopt"], [".config/nvim"])
        self.assertIn("The plan:", screen)


if __name__ == "__main__":
    unittest.main()
