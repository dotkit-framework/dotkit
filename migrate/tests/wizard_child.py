"""Runs the wizard on a fake Mac, for test_wizard_pty.

Usage: python3 wizard_child.py <result.json>
Writes {"ok": bool, "mode": "curses" or "plain", "choices": Choices.to_json()},
with the home directory as ~ in the repository path.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotkit_migrate.ui import curses_ui, plain  # noqa: E402
from tests import fake  # noqa: E402
from tests.test_session import used_mac  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1])
    h = fake.Home()
    try:
        p = fake.session(h, used_mac(h))
        home = str(h.home)
        c = p.defaults(str(h.home / ".dotfiles"))
        if curses_ui.available():
            mode, ok = "curses", curses_ui.run(p, c, home)
        else:
            mode, ok = "plain", plain.run(p, c, home)
        choices = c.to_json()
        choices["repo"] = choices["repo"].replace(home, "~")
        out.write_text(json.dumps({"ok": ok, "mode": mode, "choices": choices}))
    finally:
        h.cleanup()


if __name__ == "__main__":
    main()
