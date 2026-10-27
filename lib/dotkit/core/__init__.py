"""The parts that the dotkit commands share: the environment, messages, processes, Nix, and the config files.

The commands are in dotkit/commands and dotkit/pkg. Each module here has one
job, and only lists.py and backup.py change files. The Bash
scripts that these modules replace are the reference for their behavior:
lib/tests/PARITY.md lists it, row by row.
"""
