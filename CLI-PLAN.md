# Plan: the dotkit command in Python

This plan replaces the Bash scripts of the `dotkit` command with one Python
package. The behavior stays the same, with one new feature: `dotkit add`
lets you select the source in a terminal. The scripts are:

- `dotkit`, 432 lines: apply, upgrade, clean, history, rollback, migrate, tidy.
- `scripts/dotkit-pkg`, 943 lines: add, remove, info, list.
- `scripts/nix.sh`, the Nix helpers.

`scripts/setup` stays in Bash. It runs through `curl | bash` on a Mac that
has no clone of dotkit yet, and it is not part of the `dotkit` command.

## 1. Why, and why Python

The Bash scripts must run with the macOS Bash 3.2. That gives them the
following problems:

- Lookup results go through globals (`c_*`, `r_*`) and through fields with a
  `\x1f` separator, so that `read` keeps empty fields.
- Parallel lookups go through temporary files.
- An awk scanner reads `config/mise.toml`.
- jq must be on PATH, and before the first apply it is not.
- There are no unit tests for the selection rules. Only
  `migrate/tests/test_dotkit_pkg.py` tests the script, and only `add mas:`.

The migration wizard (`migrate/`) is 10,000 lines of Python already. It runs
with `/usr/bin/python3 -I -B` (the Python 3.9 of the Command Line Tools)
and uses only the standard library and a vendored `tomli`. It has a curses
UI and unit tests. It also reads the same lists (`declared.py`) and writes
them with all-or-nothing semantics (`writer.apply_edits`). The CLI gets the
same runtime, so it needs no new toolchain, no build step, and no download,
and it works before the first apply.

## 2. Contracts that must not change

These are the interfaces that other code and released repositories use. A
change to one of them breaks a user who did not update.

| Contract | Who depends on it |
|---|---|
| `$dotkit/dotkit` is an executable at the root of dotkit. It reads `DOTFILES` and `DOTKIT_DIR`, and takes the same arguments. | `templates/default/dotkit` in every dotfiles repository (all versions), the `dotkit` wrapper of `nix/home.nix`, `~/.local/share/dotkit` |
| `dotkit __complete inputs\|tools-user\|tools-all` prints one name per line and runs no nix | `nix/completions/_dotkit` |
| `migrate/dotkit-migrate setup ...` | `scripts/setup`, `nix run .#migrate` (`flake.nix`) |
| Exit codes: 0 success, 1 failure, 2 usage error, 130 interrupt | scripts, CI, e2e |
| The text of messages, and whether a message goes to stdout or stderr | `tests/e2e/*.sh` (for example the exact `mas:` error), users' scripts that parse `dotkit list` |
| File formats and edits: one name per line, the missing final newline is added, delete keeps comments, `mise use -g`/`unuse -g` for mise | the dotfiles repositories, `nix/read-list.nix`, `nix/read-mas.nix` |
| The commands that dotkit runs, with their environment: `sudo -H /usr/bin/env NIX_REMOTE=daemon .../nix-env`, `darwin-rebuild activate`, `HOMEBREW_NO_AUTO_UPDATE=1`, `HOMEBREW_NO_INSTALL_CLEANUP=1`, `brew bundle cleanup </dev/null`, mise from `$HOME` | system state |

The root `dotkit` stays as a small POSIX `sh` launcher, the same as
`migrate/dotkit-migrate` now. It checks the Command Line Tools, then runs
`exec /usr/bin/python3 -I -B "$dir/lib/run.py" "$@"`.

## 3. Target layout

```
dotkit                      sh launcher (contract)
migrate/dotkit-migrate      sh launcher (contract), runs the same package
lib/
  run.py                    version and curses checks, sys.path, then main
  dotkit/
    __main__.py             dispatch; imports a command module only when it runs
    core/
      env.py                DOTFILES, repo, dotkit dir, DOTKIT_DIR, flake, result
      nix.py                nix_flake (override + lock-warning filter), build_system, switch, brewfile
      proc.py               run, capture, mise_home, curl; one place for env and stdin
      ui.py                 confirm, terminal width, info/warn, DotkitError (1), UsageError (2)
      args.py               the option parser (section 5.2)
      lists.py              read, append, delete for the .txt lists and nix/mas.txt
      mise_toml.py          the [tools] keys and values, with the same rules as the awk
    pkg/
      lookups.py            nix, brew, mise, npm, pypi, mas; each returns a Candidate or None
      resolve.py            pure: Candidates + the argument -> a Selection or a reason
      declared.py           find_declared, existing_command, command_origin
      add.py remove.py info.py list.py names.py
      picker.py             the interactive selection (section 7)
    commands/
      apply.py upgrade.py clean.py history.py rollback.py migrate.py tidy.py complete.py
    migrate/                the wizard (now migrate/dotkit_migrate), moved
      ...                   tidy/, ui/, migrators/, _vendor/ unchanged inside
  tests/
```

`lib/` is the root of `sys.path`, so the package is `dotkit` and the wizard is
`dotkit.migrate`. The wizard keeps its own entry, `dotkit-migrate`, for setup.

## 4. How we keep parity

### 4.1 A parity inventory

Before any port, we write `lib/tests/PARITY.md`: one row per behavior, each
with the test that covers it. The rows come from a line-by-line read of the
three scripts. Each row is one of the following:

- A command, an option, and the commands and options that it rejects (for
  example "apply does not ask, so it takes no -y", and `--all` is for migrate
  only).
- A message, with its stream and its exit code.
- An external command, with its arguments, environment, and stdin.
- A file edit.
- A failure path, and what it restores (lock files in upgrade, the Nix lists
  in add and remove, Ctrl-C).
- An implicit `set -e` exit: a command whose failure stops the script without
  a message, for example `mise outdated --json` in upgrade. For each one, we
  decide to keep the behavior (exit with the status of the command) or to add
  a message. A changed message is a recorded deviation.

A deviation from parity is allowed only when it is in the "Deviations"
section of PARITY.md, with a reason. The planned deviations are the following:

- jq is no longer necessary. "jq is not on PATH" goes away.
- `dotkit add` can ask for the source (section 7).

PARITY.md has the full list, and the Bash bugs that the port fixes.

### 4.2 Fake tools

`lib/tests/fakebin/` has fake `nix`, `brew`, `mise`, `mas`, `curl`, `sudo`,
and `darwin-rebuild`. Each one writes its argv, the environment variables that
matter, and its stdin to a log. Then it replies from a fixture, keyed by its
arguments. The fixtures come from real output (as the `mas` fixture of
`test_dotkit_pkg.py` does). The fake `nix` replies to `eval --apply` by a key
in the expression (the attribute path), not by the full text.

The Python code calls `curl` for npm and PyPI, as the script does. This keeps
the proxy and the certificate behavior of the system, and the fake can
replace it. `urllib` with the Command Line Tools Python is a later decision.

### 4.3 Differential tests

Until the cutover of a command group, the Bash script stays in the tree. A
test runs each scenario two times, once with the Bash script and once with
the Python code, on two copies of the same fixture repository, with the fake
tools. The test compares the following:

- stdout, stderr, and the exit code;
- the files of the repository after the run;
- the log of external commands, in order.

The scenarios cover every row of PARITY.md that the fake tools can reach.
Before we delete a Bash script, we record its outputs as golden files. Then the
goldens keep the parity after the script is gone. The tests run with
`/bin/bash` (3.2), so `nix flake check` skips them (its sandbox has no Bash
3.2). CI runs them in the check job, as it runs `test_dotkit_pkg.py` now.

### 4.4 The other test layers

- Unit tests for the pure parts:
  - the selection rules of `resolve.py`, on tables of Candidates;
  - list edits and the mise `[tools]` scanner, on odd files (comments, no
    final newline, quoted keys, inline tables, other sections);
  - `version_ge` against `sort -V` of macOS for a table of versions;
  - the plan rows of `lock_changes`, with the dates in UTC as jq `strftime`
    gives them;
  - the option parser.
- PTY tests for the picker, in the same way as `test_wizard_pty.py`.
- The e2e job (`tests/e2e/verify.sh`, `tidy.sh`, `migrated.sh`) runs without
  changes at each cutover. That is the acceptance test on a real Mac.
- Both Pythons: `/usr/bin/python3` (3.9) and the Python of Nixpkgs, as for the
  wizard now. That means no `match`, and `from __future__ import annotations`
  in each module.

## 5. Design rules for the port

### 5.1 Structure

- The lookups are separate from the decisions. `lookups.py` talks to the
  tools and returns data. `resolve.py` decides and does not run commands, so
  the rules of the script header (app cask, then Nixpkgs when it is current,
  then mise, then Homebrew; npm and PyPI never from a bare name) are tested as
  a table.
- One `Candidate` dataclass (`source`, `spec`, `version`, `description`,
  `command`, `is_app`) replaces `c_*` and the `\x1f` fields. A `Selection`
  replaces `r_*`.
- `gather` uses a `ThreadPoolExecutor` with the six lookups, as the script
  runs six background jobs. A lookup that fails is "not found", except a nix
  eval that fails, which stops with `nix_failed_reason`, as now.
- One reader of the lists. `core/lists.py` and `core/mise_toml.py` become the
  code that `dotkit.migrate.declared` uses too, in a later step (section 6,
  step 8), and only when its tests show the same results.
- Only the edit functions change files, and they restore on failure. The
  same as `apply_nix` now: back up, edit, build, restore when the build fails
  or on SIGINT and SIGTERM, then `dotkit apply`. Upgrade restores the lock
  files in a `finally` and in the signal handlers.

### 5.2 Arguments and messages

- We use a small parser of our own, not argparse. argparse has its own usage
  and error text, accepts abbreviated long options, and combines short
  options. The script accepts options anywhere after the command and stops at
  `--`. The two help texts (`dotkit --help` and `dotkit add --help`) stay
  written by hand, character for character.
- All messages go through `core/ui.py`: `die` (stderr, exit 1), `usage_error`
  (stderr, exit 2, "Use dotkit --help."), and `warn`. The pkg commands keep
  their own usage error form ("Use dotkit add --help.").

### 5.3 Processes

- `nix_flake` with `DOTKIT_DIR`: stdout goes through unchanged. stderr goes
  through the lock-warning filter line by line, and the filter flushes each
  line. `build_system` gives nix the terminal on stderr, so its progress bar
  stays.
- Commands that the user watches (nix build, sudo, brew bundle, mise install)
  inherit stdin, stdout, and stderr. Commands whose output we read are
  captured.

### 5.4 Speed

`__complete` must start fast. `__main__.py` imports only the module of the
command that runs. The target is 100 ms or less for `__complete tools-all`,
and a test measures it.

## 6. Steps

Each step is one PR. CI and e2e pass after each step, and `main` stays
releasable.

| # | Step | Changes for users | Done when |
|---|---|---|---|
| 0 | **The parity inventory and the harness.** Write PARITY.md (4.1), the fake tools (4.2), and the differential runner, with only the Bash side for now. Record the first goldens from Bash. | none | each row of PARITY.md has a scenario or a "manual/e2e only" note; the Bash goldens pass |
| 1 | **Move the wizard.** `git mv migrate/dotkit_migrate lib/dotkit/migrate`, and the tests to `lib/tests`. Add `lib/run.py`, point `migrate/dotkit-migrate` at it, and update `migrate/tests.nix` (fileset), the CI paths, and the paths in ARCHITECTURE.md, TIDY.md, and README.md. Mechanical only. | none | all wizard tests pass on both Pythons; `nix run .#migrate -- setup --dry-run` works; e2e passes |
| 2 | **The core.** `core/` (env, nix, proc, ui, args, lists, mise_toml) with unit tests. Nothing uses it yet. | none | unit tests pass; the list and mise readers agree with the awk on every fixture |
| 3 | **Port add, remove, info, list, and __names.** `pkg/` without the picker. Run the differential tests on both sides. Port `test_dotkit_pkg.py` to the Python code. | none (jq is not necessary for these commands) | the differential tests are equal; e2e passes |
| 4 | **Cutover of the pkg commands.** In `dotkit`, `exec dotkit-pkg` becomes `exec /usr/bin/python3 -I -B lib/run.py`. Record the goldens of `scripts/dotkit-pkg`, then delete the script. Remove the script from the shellcheck list. | the Python code runs add, remove, info, and list | goldens pass; e2e passes |
| 5 | **The interactive picker** (section 7). | the new feature | PTY tests; `-y` and the no-terminal goldens do not change |
| 6 | **Port the other commands.** apply, upgrade, clean, history, rollback; migrate and tidy run `migrate/dotkit-migrate` as now, with the same arguments and the same `declared` JSON; `__complete inputs`; the hints for the old command names. Differential tests against `dotkit`. | none | the differential tests are equal |
| 7 | **Cutover of the launcher.** `dotkit` becomes the `sh` launcher. Record the goldens, then delete `scripts/nix.sh` and the Bash `dotkit`. Update the comments that say "It must work with the macOS Bash 3.2". | the Python code runs all of dotkit | goldens pass; e2e passes, also with a dotfiles repository made from the template of the previous release |
| 8 | **Clean up.** `dotkit.migrate.declared` and `docs.py` use `core/lists.py` and `core/mise_toml.py` where the tests show the same results. Write `lib/ARCHITECTURE.md` for the CLI. Remove the differential runner, and keep the goldens. | none | wizard goldens do not change |

Step 1 goes before step 0, so that the harness of step 0 is in `lib/tests`
from the start. Steps 3 and 6 can go in parallel after step 2. Step 5 needs
step 4.

## 7. The interactive picker of `dotkit add`

It asks for a bare name (no prefix) when stdin and stderr are a terminal and
there is no `-y` and no `-n`.

It does not ask with `-y`, with `-n`, without a terminal, with a prefix, or
when only one source has the tool. Then the result is the same as now: `-n`
shows the plan with the source that the rules select.

The screen, on stderr, for each tool:

```
lazygit: Nixpkgs has the current release
> nix:lazygit      0.55.1   Simple terminal UI for git commands
  mise:lazygit     0.55.1   Simple terminal UI for git commands
  brew:lazygit     0.55.1   Simple terminal UI for git commands
↑/↓ select · enter add · s skip · q stop
```

- The cursor starts on the source that the rules select, so Enter does what
  `add` does now.
- An app cask and a formula with the same name now stop with an error. With
  the picker, you select one, and the cursor starts on neither.
- When only npm or PyPI has the name, it now stops. With the picker, you can
  select them. They show "anyone can publish here; check the package", and
  the cursor starts on neither.
- The checks after the selection stay the same: "already installed" (unless
  `--force`), and the same command from two arguments.
- After all tools, the plan and the single question "Add the tools?" do not
  change.
- `s` skips the tool. `q` stops with "nothing changed." and exit 1.
- Ctrl-C exits with 130 and changes nothing.

The picker is a small inline widget: raw mode with `termios` and `tty`,
redrawn in place with ANSI codes. It does not use full-screen curses, so the
plan and the earlier output stay on the screen. It falls back to a numbered
prompt when the terminal is too narrow or `TERM=dumb`.

## 8. Risks

| Risk | Mitigation |
|---|---|
| A small change of text or stream breaks a user's script, or an e2e grep | differential tests compare bytes; deviations are listed |
| A behavior that only the real tools show (sudo prompts, nix progress, brew output) | e2e at each cutover; stdio inheritance rules (5.3) |
| The Python 3.9 of the Command Line Tools differs from the Nixpkgs Python | CI runs both, as for the wizard |
| Startup time of the completion | lazy imports, a timing test (5.4) |
| A dotfiles repository with an old template, or a pinned old release | the launcher contract (section 2); e2e with the old template in step 7 |
| The move in step 1 breaks paths in docs or Nix | step 1 is mechanical and alone in its PR; `nix flake check` builds the fileset |
| Ctrl-C during an edit leaves a changed list or lock file | restore in `finally` and in the signal handlers; one test for each restore path sends SIGINT |

## 9. Decisions

1. The package directory is `lib/`.
2. `-n` does not ask (section 7).
3. The wizard tests and the CLI tests share `lib/tests`.
