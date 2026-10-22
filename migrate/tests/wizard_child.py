"""Runs the wizard on a fake Mac, for test_wizard_pty.

Usage: python3 wizard_child.py <result.json>
Writes {"ok": bool, "mode": "curses" or "plain", "choices": {...}}.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotkit_migrate import prompts, wizard  # noqa: E402
from tests import fake  # noqa: E402
from tests.test_plan import used_mac  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1])
    h = fake.Home()
    try:
        s = fake.scanner(h, used_mac(h)).run()
        p = fake.planner(s)
        c = p.defaults(str(h.home / ".dotfiles"))
        if wizard.available():
            mode, ok = "curses", wizard.run(p, c)
        else:
            mode, ok = "plain", prompts.run(p, c)
        home = str(h.home)
        out.write_text(json.dumps({
            "ok": ok,
            "mode": mode,
            "choices": {
                "repo": c.repo.replace(home, "~"),
                "keymap": c.keymap,
                "groups": c.groups,
                "files": c.files,
                "adopt": sorted(c.adopt),
            },
        }))
    finally:
        h.cleanup()


if __name__ == "__main__":
    main()
