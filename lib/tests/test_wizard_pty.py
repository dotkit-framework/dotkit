"""Drive the wizard through a pseudo terminal, as a person would."""

import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
import unittest
from pathlib import Path

from tests import fake
from tests.test_session import used_mac

CHILD = Path(__file__).resolve().parent / "wizard_child.py"
UP, DOWN, LEFT, RIGHT = "\x1b[A", "\x1b[B", "\x1b[D", "\x1b[C"
ENTER, SPACE, CTRL_U = "\r", " ", "\x15"


def _size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def drive(keys, term="xterm-256color", rows=30, cols=100, timeout=30.0, env=None):
    """Runs the child in a pty and sends the keys: text, or ("resize", rows, cols). Returns its result and output.

    The child's fake Mac and its result are in a temporary directory of this process, which removes it
    also when the child is killed or the test fails.
    """
    tmp = fake.tempdir("pty")
    try:
        return _drive(tmp, keys, term, rows, cols, timeout, env)
    finally:
        fake.remove(tmp)


def _drive(tmp, keys, term, rows, cols, timeout, env):
    out = tmp / "result.json"
    extra = env or {}
    env = {k: v for k, v in os.environ.items() if k not in ("TERMINFO", "TERMINFO_DIRS", "DOTKIT_TEST_CRASH")}
    env.update(extra)
    env.update({"TERM": term, "ESCDELAY": "25", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        pid, fd = pty.fork()
    except OSError as e:
        raise unittest.SkipTest(f"no pseudo terminal: {e}") from e
    if pid == 0:
        try:
            _size(0, rows, cols)
            os.execve(sys.executable, [sys.executable, str(CHILD), str(out), str(tmp / "mac")], env)
        finally:
            os._exit(127)
    screen = bytearray()

    def drain(seconds: float, quiet: float = 0.1) -> None:
        """Reads the output for at most seconds; returns earlier when the output stopped for quiet seconds.
        A key that comes early waits in the terminal, so the wizard reads the keys in order."""
        end = time.time() + seconds
        last = None
        while time.time() < end:
            if last is not None and time.time() - last > quiet:
                return
            r, _, _ = select.select([fd], [], [], 0.01)
            if r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                screen.extend(data)
                last = time.time()

    done = 0
    try:
        drain(5.0, quiet=0.3)  # the scan and curses start
        for key in keys:
            if isinstance(key, tuple):
                _size(fd, key[1], key[2])
                os.kill(pid, signal.SIGWINCH)
            else:
                try:
                    os.write(fd, key.encode())
                except OSError:
                    break  # the child has quit
            drain(1.0)
        deadline = time.time() + timeout
        status = None
        while time.time() < deadline:
            drain(0.1)
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
        else:
            raise AssertionError("the wizard did not finish. Output:\n" + screen.decode(errors="replace")[-3000:])
        drain(0.2)
        text = screen.decode(errors="replace")
        if not out.exists():
            raise AssertionError(f"the child wrote no result (status {status}). Output:\n{text[-3000:]}")
        return json.loads(out.read_text()), text
    finally:
        if not done:  # a failure or the timeout: the child must not outlive its directory
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(fd)


def pages():
    """The pages of the fake Mac of wizard_child.py, from the Session: the key sequences follow them."""
    h = fake.Home()
    try:
        p = fake.session(h, used_mac(h))
        return p.pages(), p.defaults("~/.dotfiles").to_json()
    finally:
        h.cleanup()


class WizardPtyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages, cls.defaults = pages()
        # The fixture must have a page of each kind for these tests to mean something.
        cls.keys = [page.key for page in cls.pages]
        for key in ("formulae", "casks", "taps", "mas", "runtimes", "shell", "files", "other"):
            assert key in cls.keys, key
        cls.total = len(cls.pages) + 3  # the path, the keymap, the pages, the plan

    def run_wizard(self, keys, **kw):
        return drive(keys, **kw)

    def page_keys(self, per_page):
        """Enter on each page, after the keys of per_page for that page."""
        out = []
        for key in self.keys:
            out += [*per_page.get(key, []), ENTER]
        return out

    def test_defaults(self):
        result, screen = self.run_wizard([ENTER] * self.total)
        self.assertEqual(result["mode"], "curses", screen[-2000:])
        self.assertTrue(result["ok"], screen[-2000:])
        self.assertEqual(result["choices"], self.defaults)
        # curses redraws only what changed, so only whole titles are in the output.
        for title in ("Where the dotfiles repository goes", "The zsh keymap", self.pages[0].title, "The plan"):
            self.assertIn(title, screen)
        self.assertIn(f"1/{self.total}", screen)
        self.assertNotIn("Not carried, by your choice", screen)

    def test_changes(self):
        keys = [
            CTRL_U, *"/tmp/elsewhere", ENTER,  # outside the home directory: refused
            CTRL_U, *"~/dots", ENTER,
            DOWN, ENTER,  # vi
            *self.page_keys({
                "formulae": [DOWN, SPACE, "x"],  # the second formula off; x does nothing
                "casks": ["a"],  # all selected: a selects none
                "taps": ["a", "a"],  # none, then all
                "mas": ["j", "k", SPACE],  # the first app off
                "runtimes": [SPACE],  # off by default: on
                "shell": [DOWN, SPACE],  # ~/.zshrc off
                "files": [SPACE, DOWN, RIGHT],  # ghostty: layer -> replace; starship: replace -> dotkit
                "other": [SPACE],  # ~/.config/nvim
            }),
            "b", ENTER,  # back from the plan to other config, then on
            ENTER,  # the plan
        ]
        # A tall window: the plan screen shows the lines of what you left out.
        result, screen = self.run_wizard(keys, rows=50)
        self.assertTrue(result["ok"], screen[-2000:])
        c = result["choices"]
        self.assertEqual((c["repo"], c["keymap"]), ("~/dots", "vi"))
        picked = c["picked"]
        self.assertEqual(picked["formulae"], ["brew:wget"])
        self.assertEqual(picked["casks"], [])
        self.assertEqual(picked["taps"], self.defaults["picked"]["taps"])
        self.assertEqual(picked["mas"], ["mas:1289583905"])
        self.assertEqual(picked["runtimes"], ["runtime:node"])
        self.assertEqual(picked["shell"], ["shell:.bash_profile", "shell:.zprofile"])
        self.assertEqual(picked["other"], ["other:.config/nvim"])
        self.assertEqual(c["options"]["file:.config/ghostty/config"], "replace")
        self.assertEqual(c["options"]["file:.config/starship.toml"], "dotkit")
        self.assertIn("Use a directory in", screen)
        self.assertIn("Not carried, by your choice:", screen)

    def test_keypad_keys(self):
        """Terminals in keypad mode, as curses asks, send ESC O B for the down arrow."""
        result, screen = self.run_wizard([ENTER, "\x1bOB", ENTER, "\x1bOB", SPACE, "q"])
        self.assertFalse(result["ok"], screen[-2000:])
        self.assertEqual(result["choices"]["keymap"], "vi", screen[-2000:])
        self.assertEqual(result["choices"]["picked"]["formulae"], ["brew:wget"])

    def test_quit(self):
        result, screen = self.run_wizard([ENTER, "q"])
        self.assertFalse(result["ok"], screen[-2000:])

    def test_small_window(self):
        result, screen = self.run_wizard([ENTER, ("resize", 30, 100), ENTER, "q"], rows=10, cols=40)
        self.assertFalse(result["ok"])
        self.assertIn("Make the window larger", screen)

    def test_resize_on_a_page(self):
        """A smaller window redraws the page, and its keys still work."""
        keys = [ENTER, ENTER, ("resize", 18, 64), DOWN, SPACE, ("resize", 30, 100), ENTER]
        keys += [ENTER] * (self.total - 3)
        result, screen = self.run_wizard(keys)
        self.assertTrue(result["ok"], screen[-2000:])
        self.assertEqual(result["choices"]["picked"]["formulae"], ["brew:wget"])

    def plain_answers(self, per_page):
        """One answer per checklist page, one per item of a choices page: the counts come from the Session."""
        out = []
        for page in self.pages:
            if page.kind == "checklist":
                out.append(per_page.get(page.key, ""))
            else:
                answers = per_page.get(page.key, {})
                out += [answers.get(i.id, "") for i in page.items]
        return out

    def test_plain_prompts(self):
        lines = ["", "vi", *self.plain_answers({
            "formulae": "2",  # toggle the second
            "casks": "n",
            "taps": "1-2",  # both off
            "mas": "a",
            "runtimes": "1",
            "shell": "1 3",  # ~/.zprofile and ~/.bash_profile off
            "files": {"file:.config/ghostty/config": "replace"},
            "other": "1",
        }), "y"]
        result, screen = self.run_wizard([line + "\r" for line in lines], term="dumb")
        self.assertEqual(result["mode"], "plain")
        self.assertTrue(result["ok"], screen[-3000:])
        c = result["choices"]
        self.assertEqual(c["keymap"], "vi")
        picked = c["picked"]
        self.assertEqual(picked["formulae"], ["brew:wget"])
        self.assertEqual(picked["casks"], [])
        self.assertEqual(picked["taps"], [])
        self.assertEqual(picked["mas"], self.defaults["picked"]["mas"])
        self.assertEqual(picked["runtimes"], ["runtime:node"])
        self.assertEqual(picked["shell"], ["shell:.zshrc"])
        self.assertEqual(picked["other"], ["other:.config/nvim"])
        self.assertEqual(c["options"]["file:.config/ghostty/config"], "replace")
        self.assertEqual(c["options"]["file:.npmrc"], "tokens")
        self.assertIn("The plan:", screen)
        self.assertIn("Not carried, by your choice:", screen)

    def test_a_crash_and_the_wizard_finishes(self):
        """A scan and a plan that raise: the warning line, the Problems block, and the defaults are written."""
        crash = {"DOTKIT_TEST_CRASH": "1"}
        result, screen = self.run_wizard([ENTER] * self.total, rows=40, env=crash)
        self.assertEqual(result["mode"], "curses", screen[-2000:])
        self.assertTrue(result["ok"], screen[-2000:])
        self.assertEqual(result["choices"], self.defaults)
        self.assertIn("1 part of the scan failed: Logins.", screen)
        self.assertIn("Problems:", screen)
        lines = ["", "", *self.plain_answers({}), "y"]
        result, screen = self.run_wizard([line + "\r" for line in lines], term="dumb", env=crash)
        self.assertEqual(result["mode"], "plain")
        self.assertTrue(result["ok"], screen[-3000:])
        self.assertLess(screen.index("1 part of the scan failed: Logins."), screen.index("Where the dotfiles"))
        self.assertIn("  Shell lines: failed while planning: RuntimeError: boom.", screen)

    def test_plain_prompts_no_at_the_end(self):
        lines = ["", "", *self.plain_answers({}), "n"]
        result, screen = self.run_wizard([line + "\r" for line in lines], term="dumb")
        self.assertFalse(result["ok"], screen[-3000:])
        self.assertEqual(result["choices"], self.defaults)


if __name__ == "__main__":
    unittest.main()
