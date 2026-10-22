"""Runs dotkit-migrate on a fake Mac, for test_main.

Usage: python3 main_child.py <home> <args>...
The scan of the fake Mac replaces the real one; the arguments go to main().
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotkit_migrate import __main__ as cli  # noqa: E402
from dotkit_migrate import scan  # noqa: E402
from tests import fake  # noqa: E402
from tests.test_plan import used_mac  # noqa: E402


def main() -> int:
    h = fake.Home.at(Path(sys.argv[1]))
    runner = used_mac(h)
    runner.commands["mas list"] = "409183694 Keynote (14.2)\n1274495053 Notes \u270f\ufe0f (2.0)\n"
    real = scan.Scanner

    def fake_scanner(*_args, **_kwargs):
        return real(h.home, runner, applications=h.apps, base=fake.base.Base(fake.ROOT), brew_prefix=h.prefix,
                    env={})

    cli.scan.Scanner = fake_scanner
    try:
        return cli.main(sys.argv[2:])
    finally:
        cli.scan.Scanner = real


if __name__ == "__main__":
    sys.exit(main())
