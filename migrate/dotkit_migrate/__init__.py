"""The migration wizard of dotkit: scan a Mac, plan a dotfiles repository, write it.

../ARCHITECTURE.md is the reference: the contracts between the parts, the
rules for secrets, writes, and errors, and how to add a migrator (section
10). Read it before you change the structure.

registry.py has MIGRATORS, one per source of the Mac (migrators/), and the
Session that runs them: each migrator scans the Mac and changes nothing,
asks its questions as pages, and adds its part of the plan to the
documents (docs.py): the files of the new repository and the report. Each
migrator is isolated: a crash leaves its part out, and the run goes on.
For `dotkit migrate`, Session.adopt adds what the repository lacks, with
declared.py for what the base and the repository have already. writer.py
renders the documents into a plan, guards it against secrets, and writes it
(write_new for setup, apply_edits for `dotkit migrate`); checks.py checks
the plan against the Mac and makes the summary. context.py has the Context
of a scan and the read-only file helpers, and catalog.py the data of the
version managers, frameworks, and tool rules. ui/ asks for the choices:
ui/screens.py has the screens as data, and ui/curses_ui.py and ui/plain.py
show them.

The wizard runs with the macOS Python of the Xcode Command Line Tools, which
is Python 3.9 in the current tools (see ../dotkit-migrate). Keep the code compatible with 3.9: use
`from __future__ import annotations` for `X | None` annotations, and
Optional or Union where a type is evaluated at run time; no `match`, no
ExceptionGroup, no dataclass(slots=..., kw_only=...), and no
zip(strict=...). tomllib comes from _compat, which falls back to _vendor/tomli
on Python 3.9 and 3.10. tests/test_start.py checks the grammar, and CI runs the
tests with /usr/bin/python3.
"""
