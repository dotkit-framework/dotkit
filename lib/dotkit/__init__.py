"""dotkit: the dotkit command (run.py dotkit) and the migration wizard (migrate/).

_vendor/ has the third-party packages that dotkit carries (_vendor/README.md).
They go first on sys.path, so that `import prompt_toolkit` finds them; dotkit runs
with -I, which leaves out the user site-packages, so nothing else competes.
"""

import os
import sys

VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")
if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)
