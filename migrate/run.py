"""Starts dotkit-migrate. The dotkit-migrate script runs this file with /usr/bin/python3 -I -B.

-I ignores the PYTHON* variables and the user site-packages. It also leaves the
directory of this file out of sys.path, so this file adds it. This file must run
on any Python 3: it checks the version before it imports the package.
"""

import os
import sys

HINT = "Install the Xcode Command Line Tools: xcode-select --install"

if sys.version_info < (3, 9):
    sys.exit("dotkit-migrate: Python %d.%d is too old. The wizard needs Python 3.9 or newer. %s"
             % (sys.version_info[0], sys.version_info[1], HINT))
try:
    import curses  # noqa: F401 # The wizard needs it; check before the scan.
except ImportError:
    sys.exit("dotkit-migrate: %s has no curses module. %s" % (sys.executable, HINT))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotkit_migrate.__main__ import main  # noqa: E402

sys.exit(main())
