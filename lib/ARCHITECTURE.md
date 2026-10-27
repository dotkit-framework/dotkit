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
    cli.py                  COMMANDS: each command, its argparse parser, and how to run it
    complete.py             __complete for zsh: reads files only, imports no argparse
    _vendor/                questionary, prompt_toolkit, wcwidth, tomli (_vendor/README.md)
    core/                   what the commands share
      env.py                DOTFILES, DOTKIT_DIR, and the paths of a run
      ui.py                 messages, errors as exceptions, the question, the width of the terminal
      proc.py               the only place that starts processes
      nix.py                the flake commands: the override of DOTKIT_DIR, the build, the switch
      lists.py              the list files: read (the rules of nix/read-*.nix), append, delete
      mise_toml.py          the [tools] table of a mise config, read as TOML
      backup.py             copies of files to restore on a failure or a signal
    commands/               apply, upgrade, clean, history, rollback, migrate, tidy
    pkg/                    add, remove, info, list
      names.py              the declared names for the completion (imports only the readers)
      entries.py            KINDS (kind -> file), Entry, Declaration, and the prefixes of arguments
      lookups.py            ask Nixpkgs, Homebrew, mise, npm, PyPI, and mas; compare versions with Nix
      declared.py           where a tool is declared, and which global tool has a command
      resolve.py            select the source; choose() has the rules and runs no command
      picker.py             the source picker of add (questionary)
      edit.py               change the lists, build, activate; restore the lists on a failure
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
- **Each file has one reader, with the rules of its real consumer.**
  `core/lists.py` reads the lists as `nix/read-list.nix`, `read-taps.nix`,
  and `read-mas.nix` do, and its tests compare it with them;
  `core/mise_toml.py` reads `config/mise.toml` with a TOML parser, as mise
  does. The command and the wizard (`migrate/base.py`) use the same
  readers; each caller has its own policy for a file that it cannot read.
- **Do not write what a tool already does.** Versions compare with
  `builtins.compareVersions` of Nix, TOML comes from `tomllib` (or the
  vendored `tomli`), and third-party packages come from `dotkit/_vendor`
  (`_vendor/README.md`).

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
2. Add an entry to `COMMANDS` in `cli.py`: its group, its summary, and a
   setup function that adds its options to the parser and returns how to
   run it. Put the code in `commands/` or `pkg/`, and add the command to
   `nix/completions/_dotkit`. The help of `dotkit --help` comes from
   `COMMANDS`.
3. Add scenarios to `tests/cli/scenarios.py` with the rows, and write their
   goldens with `DOTKIT_CLI_REGOLD=1`. Read each new golden before you
   commit it.
