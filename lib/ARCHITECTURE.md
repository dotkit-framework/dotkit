# The dotkit command

The `dotkit` command is Python, in `lib/dotkit/`. It replaced the Bash
scripts `dotkit`, `scripts/dotkit-pkg`, and `scripts/nix.sh` with the same
behavior, and added one feature: `dotkit add` asks for the source in a
terminal. `CLI-PLAN.md` at the top of dotkit has the history of the port.
The migration wizard is in `lib/dotkit/migrate/` and has its own
`migrate/ARCHITECTURE.md`.

## Contracts

These are interfaces that released dotfiles repositories use. A change to
one of them breaks a user who did not update.

- `dotkit` at the top of dotkit is an executable. It reads `DOTFILES` and
  `DOTKIT_DIR`, and takes the arguments of the command. The `./dotkit` of
  each dotfiles repository (`templates/default/dotkit`, all versions) and
  the `dotkit` command of `nix/home.nix` run it.
- `dotkit __complete inputs|tools-user|tools-all` prints one name per line
  and runs no nix. `nix/completions/_dotkit` uses it.
- `migrate/dotkit-migrate setup|adopt|tidy` is the entry of the wizard:
  `scripts/setup`, `nix run .#migrate`, and `dotkit migrate` and `tidy` run
  it.
- The messages, their streams, the exit codes, the commands that dotkit
  runs, and the edits of the files: `lib/tests/PARITY.md`, row by row.

## Runtime

Both launchers (`dotkit` and `migrate/dotkit-migrate`) check the Xcode
Command Line Tools, then run `/usr/bin/python3 -I -B lib/run.py PROGRAM`.
That is the Python of macOS (3.9 in the current tools), so dotkit needs
nothing from Nixpkgs and works before the first apply. `-I` ignores the
`PYTHON*` variables and the user site-packages; `-B` writes no `.pyc`
files, because dotkit can be in the read-only Nix store.

The rules for Python 3.9 are in the docstring of `lib/dotkit/migrate/__init__.py`:
`from __future__ import annotations` in each module, no `match`, and only
the standard library (`tomllib` comes from `migrate/_compat.py`).
`tests/test_start.py` checks the grammar of each file.

## Layout

```
lib/
  run.py                    the start file: checks the Python, then runs dotkit or dotkit-migrate
  dotkit/
    __main__.py             main(): the environment, the command, and the exit code
    core/                   what the commands share
      env.py                DOTFILES, DOTKIT_DIR, and the paths of a run
      ui.py                 messages, errors as exceptions, the question, the width of the terminal
      proc.py               the only place that starts processes
      nix.py                the flake commands: the override of DOTKIT_DIR, the build, the switch
      args.py               the option parser
      lists.py              the list files: read, append, delete
      mise_toml.py          the [tools] keys of a mise config
      versions.py           the order of `sort -V`
      backup.py             copies of files to restore on a failure or a signal
    commands/               the usage, the dispatch, apply, upgrade, clean, history, rollback, migrate, tidy
    pkg/                    add, remove, info, list
      lookups.py            ask Nixpkgs, Homebrew, mise, npm, PyPI, and mas
      declared.py           where a tool is declared, and which global tool has a command
      resolve.py            select the source; choose() has the rules and runs no command
      picker.py             the source picker of add
      edit.py               change the lists, build, and restore them on a failure
      cli.py                the commands
    migrate/                the migration wizard
  tests/
    PARITY.md               the behavior of the command, row by row
    cli/                    the parity scenarios, the sandbox, the fake tools, the goldens
    core/, pkg/, commands/  unit tests
    (the other files)       the tests of the wizard
```

## Rules

- **Errors are exceptions.** A command raises `ui.die(msg)` (exit code 1)
  or `ui.usage_error(msg)` (exit code 2), or `ui.Exit(code, text)`;
  `__main__.main()` prints the text and exits with the code. A command
  returns the exit code of a command that it ran, as the Bash scripts did
  with `set -e`.
- **One place starts processes:** `core/proc.py`. It flushes the output of
  dotkit first, so the lines stay in order. On Ctrl-C it waits until the
  command ends, then raises `KeyboardInterrupt`, so that the caller can
  restore files.
- **Only `core/lists.py` and `core/backup.py` change the config files**
  (and `mise use`/`unuse`, which mise does). A change that must not stay
  half done goes inside `backup.Backup` and `backup.on_signals()`:
  `pkg/edit.py` and `commands/upgrade.py` show how.
- **The lookups are apart from the decisions.** `pkg/lookups.py` talks to
  the tools and returns data; `pkg/resolve.choose()` decides and runs no
  command, so its tests are a table.
- **The readers copy the old awk.** `core/lists.py` and `core/mise_toml.py`
  read the files line by line, as the awk programs of the Bash scripts did,
  not with a TOML parser. The wizard has its own readers
  (`migrate/base.py`): it parses `config/mise.toml` as TOML, because it
  needs the values. The two do not share code, because each must keep its
  own behavior.

## Tests

```sh
cd lib && /usr/bin/python3 -B -m unittest discover -s tests -t .
```

- **The parity scenarios** (`tests/cli`) run the real launcher in a
  sandbox: a dotkit, a dotfiles repository, a home, and fake `nix`,
  `brew`, `mise`, `mas`, `curl`, and `sudo` (`fakebin.py`), which log each
  call and reply from rules. A scenario compares the exit code, stdout,
  stderr (or the text of a terminal), the changed files, and the calls with
  its golden. The goldens came from the Bash scripts, so a change of a
  golden is a change of behavior: it needs a deviation in `PARITY.md`.
  `DOTKIT_CLI_REGOLD=1` writes the goldens again; review the diff. The
  golden of a deviation (DV...) keeps the output of the Bash scripts.
- **The inventory tests** check that each row of `PARITY.md` that says
  "scenarios" has one, and that each scenario names rows that exist.
- **The unit tests** cover the rows that the scenarios cannot reach (an
  absolute path such as `/opt/homebrew/bin/brew`, or a signal) and compare
  the readers with the awk programs and `/usr/bin/sort` that they replace.
- CI runs them with the macOS Python and the Python of Nixpkgs. `nix flake
  check` runs them too, without the parity scenarios (its sandbox has no
  launcher).

## Add a command

1. Add the behavior to `PARITY.md`: new rows, with "scenarios" or the unit
   test that covers each.
2. Add the command to `commands/__init__.py` (the usage, the dispatch) or
   to `pkg/cli.py`, and to `nix/completions/_dotkit`.
3. Add scenarios to `tests/cli/scenarios.py` with the rows, and write their
   goldens with `DOTKIT_CLI_REGOLD=1`. Read each new golden before you
   commit it.
