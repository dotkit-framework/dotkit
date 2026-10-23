"""Runs the wizard on a fake Mac, for test_wizard_pty.

Usage: python3 wizard_child.py <result.json> <root>
Writes {"ok": bool, "mode": "curses" or "plain", "choices": Choices.to_json()},
with the home directory as ~ in the repository path. With DOTKIT_TEST_CRASH=1,
the scan of logins (which has no page) and the plan of shell raise, and the
Session is not strict: the wizard must still finish. The fake Mac is made
in <root>, a directory that the parent removes, also when it kills this child.
"""

import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotkit_migrate.migrators import logins, shell  # noqa: E402
from dotkit_migrate.ui import curses_ui, plain  # noqa: E402
from tests import fake  # noqa: E402
from tests.test_session import used_mac  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1])
    h = fake.Home.at(Path(sys.argv[2]))
    crash = os.environ.get("DOTKIT_TEST_CRASH") == "1"
    if crash:
        for cls, phase in ((logins.Logins, "scan"), (shell.Shell, "plan")):
            mock.patch.object(cls, phase, side_effect=RuntimeError("boom")).start()
    try:
        p = fake.session(h, used_mac(h), strict=not crash)
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
