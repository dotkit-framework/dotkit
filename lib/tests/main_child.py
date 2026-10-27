"""Runs dotkit-migrate on a fake Mac, for test_main.

Usage: python3 main_child.py <home> <args>...
main() scans the fake Mac instead of this one; the arguments go to main().
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotkit.migrate import __main__ as cli  # noqa: E402
from dotkit.migrate import writer  # noqa: E402
from tests import fake  # noqa: E402
from tests.test_session import used_mac  # noqa: E402


def main() -> int:
    h = fake.Home.at(Path(sys.argv[1]))
    runner = used_mac(h)
    runner.commands["mas list"] = os.environ.get("DOTKIT_TEST_MAS_LIST",
                                                 "409183694 Keynote (14.2)\n1274495053 Notes \u270f\ufe0f (2.0)\n")
    forced = os.environ.get("DOTKIT_TEST_FORCE_SECRET")
    if forced:
        # A secret that only the write-time verification sees: setup's write
        # and adopt's apply_edits raise as the verification does.
        if forced == "config/after-the-guard":
            writer.verify = lambda tree, plan: [f"{forced} (line 1: a GitHub token)"]
        else:
            writer.verify_edits = lambda staged, old: [f"{forced} (line 1: a GitHub token)"]
    return cli.main(sys.argv[2:], make_context=lambda _home, _base: fake.context_of(h, runner))


if __name__ == "__main__":
    sys.exit(main())
