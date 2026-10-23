"""The migration wizard of dotkit: scan a Mac, plan a dotfiles repository, write it.

scan.py reads the Mac and changes nothing. plan.py turns a scan and the
choices into a plan: the files of the new repository and the report.
writer.py renders the documents into a plan, guards it against secrets, and
writes it; checks.py checks the plan against the Mac and makes the summary.
ui/ asks for the choices: ui/screens.py has the screens as data, and
ui/curses_ui.py and ui/plain.py show them.

The wizard runs with the macOS Python of the Xcode Command Line Tools, which
is Python 3.9 in the current tools (see ../dotkit-migrate). Keep the code compatible with 3.9: use
`from __future__ import annotations` for `X | None` annotations, and no
`match`, no ExceptionGroup, no dataclass(slots=..., kw_only=...), and no
zip(strict=...). tomllib comes from _compat, which falls back to _vendor/tomli
on Python 3.9 and 3.10. tests/test_start.py checks the grammar, and CI runs the
tests with /usr/bin/python3.
"""
