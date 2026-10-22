"""The migration wizard of dotkit: scan a Mac, plan a dotfiles repository, write it.

scan.py reads the Mac and changes nothing. plan.py turns a scan and the
choices into a plan: the files of the new repository and the report.
write.py writes a plan. wizard.py and prompts.py ask for the choices.

The wizard runs with the macOS Python of the Xcode Command Line Tools, which
is Python 3.9 in the current tools (see ../dotkit-migrate). Keep the code compatible with 3.9: use
`from __future__ import annotations` for `X | None` annotations, and no
`match`, no ExceptionGroup, no dataclass(slots=..., kw_only=...), and no
zip(strict=...). tomllib comes from _compat, which falls back to _vendor/tomli
on Python 3.9 and 3.10. tests/test_start.py checks the grammar, and CI runs the
tests with /usr/bin/python3.
"""
