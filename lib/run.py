"""Starts a dotkit program. The launchers run this file with /usr/bin/python3 -I -B:

    run.py dotkit <args>...            the dotkit command (dotkit/__main__.py)
    run.py dotkit-migrate <args>...    the migration wizard (migrate/dotkit-migrate)

-I ignores the PYTHON* variables and the user site-packages. It also leaves the
directory of this file out of sys.path, so this file adds it. This file must run
on any Python 3: it checks the version before it imports the package.
"""

import os
import sys

HINT = "Install the Xcode Command Line Tools: xcode-select --install"
PROGRAMS = ("dotkit", "dotkit-migrate")

if len(sys.argv) < 2 or sys.argv[1] not in PROGRAMS:
    sys.exit("run.py: the first argument must be one of: %s." % ", ".join(PROGRAMS))
prog = sys.argv[1]
if sys.version_info < (3, 9):
    sys.exit("%s: Python %d.%d is too old. dotkit needs Python 3.9 or newer. %s"
             % (prog, sys.version_info[0], sys.version_info[1], HINT))
if prog == "dotkit-migrate":
    try:
        import curses  # noqa: F401 # The wizard needs it; check before the scan.
    except ImportError:
        sys.exit("%s: %s has no curses module. %s" % (prog, sys.executable, HINT))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if prog == "dotkit":
    from dotkit.__main__ import main
else:
    from dotkit.migrate.__main__ import main

sys.exit(main(sys.argv[2:]))
