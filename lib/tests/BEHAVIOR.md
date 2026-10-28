# Behavior of the dotkit command

What `dotkit` does, one row per behavior: the commands, their options, the
messages and their streams, the exit codes, the commands that dotkit runs,
and the edits of the files. A change of behavior changes a row here, its
test, and its golden.

The column "Covered by" says which test checks the row:

- **scenarios**: one or more scenarios of `cli/scenarios.py`, compared
  with the goldens in `cli/golden/`. `cli/test_scenarios.py` makes sure that
  each of these rows has a scenario, and that each scenario names rows of
  this file.
- **unit**: a unit test. The fake tools cannot reach the row, for example
  because dotkit runs a command by its absolute path.
- **e2e**: `tests/e2e/*.sh` on a real Mac, in the CI job e2e.

All messages of dotkit start with `dotkit: ` and go to stderr, unless a row
says otherwise. "Die" means the message and exit code 1. An error of the
options prints the usage of the command, then "dotkit CMD: error: MESSAGE",
exit code 2.

## G. Start

| Row | Behavior | Covered by |
|---|---|---|
| G1 | Without `DOTFILES`, every command stops with "DOTFILES is not set. Run dotkit, or ./dotkit in your dotfiles repository." and exit code 2, also `--help` and the pkg commands. | scenarios |
| G2 | `DOTFILES` is resolved to a physical path (`cd && pwd -P`). When it is not a directory, the command stops with exit code 1. | scenarios |
| G3 | When `DOTKIT_DIR` is set and is not a directory: "DOTKIT_DIR is not a directory: DIR", exit code 2, for all commands. | scenarios |
| G4 | With `DOTKIT_DIR`, the commands that read the flake add `--override-input dotkit git+file://DIR --no-write-lock-file --option warn-dirty false` (DIR resolved). | scenarios |
| G5 | The resolved `DOTFILES` is exported to the commands that dotkit runs. | scenarios |

## C. Commands

| Row | Behavior | Covered by |
|---|---|---|
| C1 | No command, `-h`, `--help`, and `help` print the usage of dotkit on stdout, exit code 0: the commands by group with a summary each, then examples (dotkit/cli.py, COMMANDS). `help CMD` prints the help of the command. | scenarios |
| C2 | Each command has its own options (argparse): an option of another command is an error of this one. | scenarios |
| C3 | `__complete inputs` prints the inputs of nix/flake.lock that do not follow another input, one per line; with `DOTKIT_DIR`, then the inputs of the checkout's flake.lock as `dotkit/NAME`. It runs no nix. | scenarios |
| C4 | `__complete tools-user` and `__complete tools-all` print the names of rows N. | scenarios |
| C5 | `__complete` with another argument, or none: "dotkit: __complete takes inputs, tools-user, or tools-all.", exit code 2. | scenarios |
| C6 | The old names stop with "dotkit: 'OLD' is now 'NEW'. Run dotkit --help.", exit code 2: switch, build, diff, update, brew, tools, changes, where, gc. | scenarios |
| C7 | Another command: "dotkit: unknown command: CMD. Run dotkit --help.", exit code 2. | scenarios |

## O. Options

argparse reads the options of each command (dotkit/cli.py).

| Row | Behavior | Covered by |
|---|---|---|
| O1 | Options can come anywhere after the command; the other words are the arguments. | scenarios |
| O2 | `-h`/`--help` after the command prints the help of the command on stdout, exit code 0. | scenarios |
| O3 | `--` ends the options: the words after it are arguments. | scenarios |
| O4 | An unknown option, an argument that a command does not take, or a long option that is not complete: an error. | scenarios |
| O5 | `--plan FILE` gives the wizard an absolute path: the directory resolved, the file name kept. Without FILE, or with a directory that does not exist: an error. | scenarios |
| O6 | The options: apply `-n`; upgrade `-n -y`; add `-n -y -f`; remove `-n -y`; migrate `-n -y -a`; tidy `-n -y --plan FILE`; clean `-n -y`; info, list, doctor, history, and rollback none. add, remove, and info need one tool or more. | scenarios |
| O7 | `clean` takes one age, `[0-9]+d`, default 14d; `list` takes nix, brew, or mise. Another value: an error. | scenarios |

## A. apply

| Row | Behavior | Covered by |
|---|---|---|
| A1 | apply takes no arguments (O4). | scenarios |
| A2 | Builds `path:REPO/nix#darwinConfigurations.mac.system` with the link `REPO/.build/system`, then runs `sudo -H /usr/bin/env NIX_REMOTE=daemon SYSTEM/sw/bin/nix-env -p /nix/var/nix/profiles/system --set SYSTEM` and `sudo SYSTEM/sw/bin/darwin-rebuild activate` (SYSTEM is the target of the link), then `mise install` in `$HOME`, then the report of rows Ov for SYSTEM. | scenarios |
| A3 | When the build fails, apply stops with the exit code of nix and no message. Nothing is activated. | scenarios |
| A4 | When a sudo command fails, apply stops with its exit code; the later commands do not run. | scenarios |
| A5 | When `mise install` fails, apply stops with its exit code. | scenarios |
| A6 | `apply -n` builds, prints "Nix package changes:" on stdout, runs `nix store diff-closures /run/current-system SYSTEM`, the Homebrew preview (A7), `mise install --dry-run` in `$HOME`, the report of rows Ov with `-n`, then "No change. Run dotkit apply to apply." on stderr (without `dotkit: `). | scenarios |
| A7 | The Homebrew preview runs only when `/opt/homebrew/bin/brew` exists and `homebrew.onActivation.cleanup` of the configuration is not `none`. | scenarios |
| A8 | The preview writes the Brewfile of the configuration to `REPO/.build/Brewfile` and runs `/opt/homebrew/bin/brew bundle cleanup --file=FILE` with `HOMEBREW_NO_AUTO_UPDATE=1` and stdin from /dev/null. Exit code 1 is accepted only after the complete report; the report loses its last line and the part from "Would \`brew cleanup\`:". A report that is left prints as "Homebrew removes:" and the lines. Another failure: die "the Homebrew cleanup preview failed with exit code N." | unit, e2e |
| A9 | With `DOTKIT_DIR`, the build evaluates `...system.drvPath` with the override (G4) and builds `DRV^*` without the flake, so nix keeps its progress bar. | scenarios |
| A10 | With `DOTKIT_DIR`, the stderr of each nix command that reads the flake goes through a filter. The filter drops "warning: not writing (modified )lock file of flake ..." and the lines after it that start with `• `, `  →`, or four spaces. Other lines pass at once; stdout does not change. | scenarios |

## U. upgrade

| Row | Behavior | Covered by |
|---|---|---|
| U1 | The arguments are sources (nix, brew, mise) or flake inputs. A name matches an input of nix/flake.lock, or `dotkit/NAME` of the checkout's lock. Another word: exit code 2 and "'X' is not a source or a flake input. Use nix, brew, mise, or: NAMES. Run dotkit upgrade --help." (NAMES as in C3, on one line). | scenarios |
| U2 | Without arguments: nix, brew, and mise. | scenarios |
| U3 | `nix` with input names updates all inputs. | scenarios |
| U4 | Nix: "Checking Nix..." on stderr (without `dotkit: `). For each lock file (nix/flake.lock; with `DOTKIT_DIR` also the checkout's flake.lock): a backup, then `nix flake update [INPUTS] --flake path:REPO/nix` (or `git+file://DIR`), with its output captured. A lock file without selected inputs is skipped when inputs are given. On failure: the output, then die "nix flake update failed for FILE. The lock files are restored." | scenarios |
| U5 | One plan row per input whose `locked` changed: `LABEL+NAME`, "YYYY-MM-DD REV7" before and after (UTC). A lock file without a change is restored from the backup. | scenarios |
| U6 | brew: "Checking Homebrew...", `brew update --quiet` with its output captured (on failure: the output, then die "brew update failed."), then `brew outdated --json=v2` with `HOMEBREW_NO_AUTO_UPDATE=1`. Rows only for the formulae and casks of the base and repository lists: name, the installed versions joined by spaces, the current version. | scenarios |
| U7 | mise: "Checking mise...", `mise outdated --json` in `$HOME`: rows of name, current, latest. When it fails, upgrade stops with its exit code and no message. | scenarios |
| U8 | No rows: "Everything is up to date." on stdout, exit code 0. | scenarios |
| U9 | The plan on stdout: `  %-5s %-36s %-22s -> %s` per row, nix rows, then brew, then mise. | scenarios |
| U10 | With nix rows: a build, then "\nNix package changes:" and diff-closures on stdout. | scenarios |
| U11 | `-n`: the lock files are restored; with nix rows, the report of rows Ov with `-n` for the built system; then "No change. Run dotkit upgrade to apply." on stderr. | scenarios |
| U12 | The question "Upgrade? [y/N] " (rows C-confirm). No: the lock files are restored, die "nothing changed." | scenarios |
| U13 | Nix rows: a switch (A2 without mise); when it fails, die "the switch failed. The lock files are restored." brew rows: the Brewfile of the configuration goes to `brew bundle install --file=-` on stdin, with `HOMEBREW_NO_INSTALL_CLEANUP=1`. mise rows: `mise upgrade` in `$HOME`. | scenarios |
| U14 | After an upgrade with nix rows: the report of rows Ov for the built system, then "Commit nix/flake.lock to keep the upgrade." and with `DOTKIT_DIR` "Commit flake.lock in DIR too, if it changed." on stderr (without `dotkit: `). | scenarios |
| U15 | Any exit before the switch restores the lock files: an error, a refused question, and a signal. | scenarios (error, question); unit for signals |
| U16 | When the build fails, upgrade stops with the exit code of nix, and the lock files are restored. | scenarios |

## Q. The question

| Row | Behavior | Covered by |
|---|---|---|
| Q1 | With `-y`, no question. Without `-y`, stdin and stderr must be a terminal, else die "confirmation needs a terminal. Use -y to continue without one." The prompt is "TEXT [y/N] " on stderr; only `y` and `Y` mean yes. | scenarios |

## K. clean

| Row | Behavior | Covered by |
|---|---|---|
| K1 | clean takes one age (O7). | scenarios |
| K2 | The age is `14d` by default (O7). | scenarios |
| K3 | `-n`: `sudo nix-collect-garbage --delete-older-than AGE --dry-run`, `brew cleanup --dry-run`, `mise prune --dry-run` in `$HOME`, then "No change. Run dotkit clean to delete." on stderr. | scenarios |
| K4 | The question "Delete Nix generations older than AGE, old Homebrew downloads, and unused mise versions?"; no: die "nothing changed." | scenarios |
| K5 | Yes: `sudo nix-collect-garbage --delete-older-than AGE`, `brew cleanup`, `mise prune --yes` in `$HOME`. | scenarios |

## H. history and rollback

| Row | Behavior | Covered by |
|---|---|---|
| H1 | `history` takes no arguments (O4). | scenarios |
| H2 | `history` reads the link `/nix/var/nix/profiles/system` (`system-N-link`). Without `system-(N-1)-link`: die "there is no previous generation." Else it prints "system-(N-1)-link -> system-N-link" on stdout and runs `nix store diff-closures` of the two. | unit, e2e |
| H3 | `rollback` takes no arguments (O4). | scenarios |
| H4 | `rollback` runs `sudo darwin-rebuild switch --rollback`; its exit code is the exit code of dotkit. | scenarios |

## M. migrate and tidy

| Row | Behavior | Covered by |
|---|---|---|
| M1 | migrate and tidy take no arguments (O4). | scenarios |
| M2 | Both evaluate `homebrew` of the configuration with `--apply 'h: { taps = map (t: t.name) h.taps; masApps = h.masApps; }'` through the filter of A10. | scenarios |
| M3 | migrate runs `DOTKIT/migrate/dotkit-migrate adopt --repo REPO --declared JSON`, then `--dry-run`, `--yes`, `--all` for the options that are set; its exit code is the exit code of dotkit. | scenarios |
| M4 | tidy runs `DOTKIT/migrate/dotkit-migrate tidy --repo REPO --declared JSON`, then `--dry-run`, `--yes`, `--plan FILE`. | scenarios |
| M5 | When the evaluation fails, the command stops with its exit code and no message. | scenarios |

## L. list

| Row | Behavior | Covered by |
|---|---|---|
| L1 | list takes nix, brew, or mise (O7). | scenarios |
| L2 | Without sources: nix, brew, mise. With sources: in their order, also a source twice. | scenarios |
| L3 | nix: `nix   NAME`, the base list first with ` (dotkit)` after the name. | scenarios |
| L4 | brew: the formulae (`brew  %-36s formula`), then the casks, base first; then the App Store apps of the configuration (`homebrew.masApps`), sorted by name, with the escapes `\\`, `\"`, `\#` removed, as `brew  NAME` padded to 36, then ` app store`. | scenarios |
| L5 | mise: the `[tools]` table of the base and the repository config/mise.toml, read as TOML (core/mise_toml.py), in the order of the file. The version is the string, the `version` of a table, the items of an array joined by ", ", or else the value as compact JSON. A file that is not valid TOML: "dotkit: FILE is not valid TOML: ERROR.", exit code 1. | scenarios |
| L6 | A list file has the names of nix/read-list.nix: the text of each line before `#`, without the whitespace at its ends (also `\r`); blank lines and comments give none. taps.txt: the first word of such a name. mas.txt: the lines `ID name` of nix/read-mas.nix. | scenarios; unit (tests/core/test_files.py, against the Nix code) |
| L7 | When the evaluation of the App Store apps fails, list stops with exit code 1, after the rows before. | scenarios |

## N. Names for the completion

| Row | Behavior | Covered by |
|---|---|---|
| N1 | `user`: the names of the repository's packages.txt, brews.txt, casks.txt, mas.txt (as `mas:ID`), and the mise keys; sorted, each once. | scenarios |
| N2 | `all`: the same with the base lists too. | scenarios |
| N3 | Without nix/mas.txt, the other names. | scenarios |

## I. info

| Row | Behavior | Covered by |
|---|---|---|
| I1 | For each tool: its name, then `  declared  FILE (ENTRY)` for each declaration (rows D), or `  declared  no`. | scenarios |
| I2 | `  add picks SPEC (REASON)`, or `  add picks none: REASON` (rows R). | scenarios |
| I3 | The command: the one that the selection gives, or the tool name after its last `:` or `/`. When a global tool has it (X1): `  command   PATH (ORIGIN)`. | scenarios |
| I4 | When any source has the tool: `  sources`, then the candidates (Pc1). | scenarios |
| I5 | All tools are shown. The exit code is 1 when a tool has no declaration, no command, and no source; else 0. | scenarios |

## D. Declarations

| Row | Behavior | Covered by |
|---|---|---|
| D1 | `mas:ID`: only nix/mas.txt of the repository, by ID; the result is `nix/mas.txt` and the ID. | scenarios |
| D2 | Other tools: the base (with the label `dotkit:`) and then the repository, in packages.txt, brews.txt, casks.txt (by the name after the prefix, in all three lists) and the mise keys. A key matches when it is the tool, the name after the prefix, `npm:TOOL`, or when its part after the last `:` or `/` is that name. | scenarios |

## R. The selection of a source

| Row | Behavior | Covered by |
|---|---|---|
| R1 | `nix:ATTR`: ATTR must be a Nix attribute path, else "'X' is not a valid Nix attribute path."; when the evaluation fails, the reason of R9; when Nixpkgs has no package, "Nixpkgs has no package 'X' for this Mac."; the reason is "selected with nix:". | scenarios |
| R2 | `brew:NAME`: a valid Homebrew name, else "'X' is not a valid Homebrew name."; the formula, else the cask (an app or a command); else "Homebrew has no formula or cask 'X'." | scenarios |
| R3 | `cask:NAME`: the cask; else "Homebrew has no cask 'X'." | scenarios |
| R4 | `mise:NAME`: the mise registry, when a backend is not asdf or vfox; else "the mise registry has no tool 'X' (without asdf or vfox)." | scenarios |
| R5 | `mas:ID`: an ID of digits that does not start with 0, else "'X' is not an App Store ID. mas list shows the IDs." The name comes from `mas list`, else from `mas info --json ID` (the name of the first JSON line). Without mas: "mas is not installed, so the name of the app is unknown. Run dotkit add brew:mas first."; no name: "cannot read the name of App Store app ID from mas. Add it to nix/mas.txt as \`ID Name\`."; unknown: "mas finds no App Store app with the ID ID." | scenarios |
| R6 | Another `BACKEND:NAME`: `mise latest SPEC`; else "mise cannot find 'X'."; the reason is "selected with BACKEND:". | scenarios |
| R7 | `@scope/name`: npm, only with a command; else "npm has no package 'X' with a command."; the reason is "a scoped npm package". | scenarios |
| R8 | A bare name must match `^[A-Za-z0-9][A-Za-z0-9._+-]*$`, else "'X' is not a valid tool name." | scenarios |
| R9 | A bare name is looked up in the six sources at the same time: cask, formula, Nixpkgs, mise, npm, PyPI. When the nix evaluation fails: "nix eval failed, so Nixpkgs cannot be checked. Run dotkit apply -n to see the error." | scenarios |
| R10 | An app cask and a formula: "Homebrew has an app and a command-line tool with this name. Add one of them by the name in the list." | scenarios |
| R11 | An app cask: "a macOS app". | scenarios |
| R12 | Nixpkgs and mise: Nixpkgs when a version is empty or the Nixpkgs version is the same or newer ("Nixpkgs has the current release"), else mise ("Nixpkgs has the older VERSION"). | scenarios |
| R13 | Only Nixpkgs: "not in the mise registry". Only mise: "not in Nixpkgs". | scenarios |
| R14 | A formula, else a cask without an app: "not in Nixpkgs or mise". | scenarios |
| R15 | Only npm or PyPI: "only npm or PyPI has this name. Anyone can publish there: check the package, then add it by the name in the list." | scenarios |
| R16 | Nothing: "no source has this name. Check the name, or use a prefix (dotkit add --help)." | scenarios |
| R17 | Versions compare with `builtins.compareVersions` of Nix: `nix eval --impure --expr` with the two versions in `DOTKIT_VERSION_A` and `DOTKIT_VERSION_B`, without a leading `v`. When nix cannot compare them: die "nix could not compare the versions A and B." | scenarios; unit (the real nix) |
| R18 | The command of a formula, a mise tool, a PyPI package, or a spec that is not npm is the `meta.mainProgram` of the Nixpkgs package with the same name (the last part of the name), when Nixpkgs has one. That can be one more nix evaluation. Else the command of the lookup, else the entry after its last `:` or `/`. | scenarios |
| R19 | For an `npm:` spec, the command comes from the npm registry, else the package name after its last `/`. | scenarios |
| R20 | The package of a spec has no backend and no version: `npm:@scope/pkg@1.0` is `@scope/pkg`. | scenarios |
| R21 | The lookups: `brew info --json=v2 --cask|--formula NAME` with `HOMEBREW_NO_AUTO_UPDATE=1` (only with brew on PATH and a valid name); a cask is an app when an artifact has `app`, `pkg`, or `suite`, and its command is the file name of the first `target` of a `binary` artifact. npm: `curl -fsSL --max-time 10 https://registry.npmjs.org/NAME/latest` (the first `/` as `%2F`); only with `bin`; the command is the first key of `bin` in sorted order. PyPI: `curl ... https://pypi.org/pypi/NAME/json`. mise: `mise registry`, `mise latest` (an empty version when it fails), `mise tool --description` (`[none]` is empty), in `$HOME`. Tabs and line breaks in the fields become spaces. | scenarios |

## Pc. The candidates

| Row | Behavior | Covered by |
|---|---|---|
| Pc1 | On stdout, in the order app, nix, mise, formula, cask, npm, pypi, spec: `  %s %-30s %-14s %s`, a check mark for the selection, the spec, the version or `?`, and the description. When stdout is a terminal, the description is cut to the width of the terminal minus 50 (at least 20), with `…`. | scenarios; unit for the cut |

## Ad. add

| Row | Behavior | Covered by |
|---|---|---|
| Ad1 | An argument that comes again is skipped. | scenarios |
| Ad2 | A declared tool: "TOOL: already in FILE" on stdout (the first declaration). It is not an error. | scenarios |
| Ad3 | A tool without a selection: "TOOL: REASON", then its candidates on stdout. | scenarios |
| Ad4 | The same entry as an earlier argument: "TOOL: same entry as an earlier argument (SPEC)" on stdout. | scenarios |
| Ad5 | An App Store app: without nix/mas.txt, "TOOL: nix/mas.txt does not exist. Create it, and list it in dotkit.lists.masApps in nix/darwin.nix." When another app (in the file or earlier in the plan) has the name, the label is "NAME (ID)"; when that is taken too, "TOOL: other apps have the names 'NAME' and 'NAME (ID)'. Add it to nix/mas.txt under another name: ID <name>". The plan line is "SPEC -> nix/mas.txt (LABEL)", then "nix/mas.txt has another app named NAME; added as 'LABEL'." for a new label. | scenarios |
| Ad6 | Without `--force`, a tool whose command a global tool has (X1): "TOOL: 'BIN' is already installed (ORIGIN). See dotkit info BIN, or use --force to add it anyway." | scenarios |
| Ad7 | Two arguments with the same command: "TOOL: an earlier argument also supplies the command 'BIN'. Add only one of them." | scenarios |
| Ad8 | The plan line on stdout: "SPEC -> FILE" when the tool is the spec, else "TOOL: SPEC -> FILE (REASON)"; then the candidates. | scenarios |
| Ad9 | After all tools, when one of them failed: die "nothing changed. Fix the errors above, then run the command again." | scenarios |
| Ad10 | Nothing to add: exit code 0 and no more output. | scenarios |
| Ad11 | `-n`: "No change. Run the command without -n to add." on stderr. | scenarios |
| Ad12 | The question "Add the tool?" or "Add the N tools?"; no: die "nothing changed." | scenarios |
| Ad13 | List changes first: a backup of the Nix lists (packages.txt, brews.txt, casks.txt, mas.txt), the edits, the build. When an edit fails: restore, die "could not change the Nix files. They are restored."; when the build fails: restore, die "the build failed. The Nix files are restored."; on SIGINT, SIGTERM, SIGHUP, or SIGPIPE: restore, "\ndotkit: stopped. The Nix files are restored.", exit code 130. Then the activation of the built system and `mise install`; when either fails: die "dotkit apply failed. The Nix files keep the change. Fix the problem, then run dotkit apply." | scenarios; unit (tests/pkg/test_pkg.py) for signals |
| Ad14 | Then mise: `mise use -g SPEC` in `$HOME` for each mise entry, in order; when one fails: die "mise could not add SPEC. The tools before it in the list are added." | scenarios |
| Ad15 | The entries: Nixpkgs to packages.txt, casks to casks.txt, formulae to brews.txt, App Store apps to mas.txt as "ID LABEL"; mise entries are NAME, `npm:NAME`, `pipx:NAME`, or the spec. | scenarios |

## Pk. The picker of add

New in the Python code. The goldens of these rows come from the Python code.

| Row | Behavior | Covered by |
|---|---|---|
| Pk1 | add asks for the source of a bare name when stdin and stderr are a terminal, without `-y` and `-n`, and when two sources or more have the tool, or when the rules select none of them (R10, R15). It does not ask for a prefix, for one source, when the nix evaluation fails, or when no source has the tool. | scenarios |
| Pk2 | The list, on stderr (prompt_toolkit): the tool name as the title; one row per candidate in three columns, the manager (nix, mise, brew, cask, npm, pipx, or the mise backend), the version, and the description, which takes the rest of the width and is cut with …; npm and PyPI rows end with "· anyone can publish" when it fits; then the help "↑/↓ move · enter select · esc cancel". The selected row is in reverse video. With `NO_COLOR`, no colors. The list goes away when it ends. | scenarios; unit (tests/pkg/test_pkg.py) for the lines |
| Pk3 | Up and Down move, and stop at the first and the last row; Enter selects. Esc cancels: die "nothing changed." There is no skip: each tool needs a source or the cancel. | scenarios |
| Pk4 | The first row is the source that the rules select, and the cursor starts there; the other rows follow in the order of Pc1. When the rules select none, the rows have the order of Pc1 and the cursor starts on the first. | scenarios |
| Pk5 | After the selection, add goes on as without the picker: the checks of Ad6 and Ad7, the plan line, the candidates, the question. For a source other than the selection of the rules, the reason is "selected in the list". | scenarios |
| Pk6 | With `TERM=dumb` or without `TERM`: a numbered list on stderr in the same columns, and "Select 1-N (Enter for 1), or q to cancel: ", asked again until the answer is valid. q and the end of the input cancel. | scenarios |
| Pk7 | Ctrl-C in the list: exit code 130, nothing changed. | scenarios |

## Rm. remove

| Row | Behavior | Covered by |
|---|---|---|
| Rm1 | A prefix selects the file: `nix:` packages.txt, `brew:` brews.txt or casks.txt, `cask:` casks.txt, `mas:` mas.txt, another prefix config/mise.toml. | scenarios |
| Rm2 | A declaration in the base only: "TOOL: in the dotkit base (FILE). Only dotkit can remove it." | scenarios |
| Rm3 | No declaration: "TOOL: not in FILES. See dotkit info TOOL." | scenarios |
| Rm4 | More than one declaration in the repository: "TOOL: in more than one place: F1,F2. Use a prefix (nix:, brew:, cask:, mise:), or remove it by hand." Two lines of one file count as two places. | scenarios |
| Rm5 | The plan line on stdout: "TOOL: remove ENTRY from FILE"; an entry that comes again is skipped. | scenarios |
| Rm6 | When one tool failed: die "nothing changed. Fix the errors above, then run the command again." | scenarios |
| Rm7 | `-n`: "No change. Run the command without -n to remove." on stderr. | scenarios |
| Rm8 | The question "Remove?"; no: die "nothing changed." | scenarios |
| Rm9 | List changes as in Ad13. Then, for a removed formula or cask that `brew list --KIND NAME` shows: `brew uninstall --KIND NAME` (with `HOMEBREW_NO_AUTO_UPDATE=1`); when it fails: "could not uninstall the KIND NAME. Run: brew uninstall --KIND NAME". For an App Store app: "the App Store app ID stays installed. dotkit does not uninstall App Store apps." | scenarios |
| Rm10 | Then `mise unuse -g KEY` in `$HOME` for each mise entry; when one fails: die "mise could not remove KEY. The tools before it in the list are removed." | scenarios |
| Rm11 | An entry that the repository and the base both have: the plan line is "TOOL: remove ENTRY from FILE; the dotkit base keeps it (dotkit:FILE)", and only the file of the repository changes. No `brew uninstall` for it; for a mise key, `mise unuse -g --no-prune KEY`, so that the versions of the base stay. | scenarios |

## X. Installed commands

| Row | Behavior | Covered by |
|---|---|---|
| X1 | A global tool has a command when it is in `/etc/profiles/per-user/USER/bin`, `/run/current-system/sw/bin`, `~/.nix-profile/bin`, or `/opt/homebrew/bin`, else when `mise which BIN` in `$HOME` finds it. The PATH of the caller does not count. | scenarios (mise); unit for the directories |
| X2 | The origin of a path, after `realpath`: `/nix/store/HASH-NAME/...` is "nix NAME"; `MISE_DATA_DIR/installs/TOOL/VERSION/...` (default `~/.local/share/mise`) is "mise TOOL VERSION"; `/opt/homebrew/Cellar/NAME/VERSION/...` is "brew NAME VERSION"; `/opt/homebrew/Caskroom/...` and `/Applications/...` are "brew (cask)"; else the path. | scenarios |

## F. List files

| Row | Behavior | Covered by |
|---|---|---|
| F1 | An append adds the missing final newline first, then "NAME\n". | scenarios |
| F2 | A delete removes each line whose name (L6) is the name, and each line of mas.txt with the ID. The other lines stay as they are, also without a final newline. The file is written in place, so a link stays a link. | scenarios |
| F3 | The ID of an App Store app name: the first app of mas.txt (L6), or of the planned apps, whose name is the name exactly. | scenarios |

## Ov. Overlaps with the base

What the dotfiles repository repeats or replaces of the dotkit base (commands/overlap.py). The base is the `dotkit` directory of the manifest (Ov3), else the dotkit of the command.

| Row | Behavior | Covered by |
|---|---|---|
| Ov1 | A name of nix/packages.txt, brews.txt, or casks.txt that the same list of the base has, at FILE (the list): "NAME is in the dotkit base too. Remove yours: dotkit remove PREFIX:NAME", where PREFIX is nix, brew, or cask. | scenarios |
| Ov2 | A key of config/mise.toml that the base has: with the same value, "KEY is the same in the dotkit base. Remove yours: dotkit remove KEY"; else "KEY = VALUE replaces VALUE of the dotkit base.", the values as TOML on one line. When a file is not valid TOML: warning "FILE is not valid TOML: ERROR. The mise tools are not compared with the base." | scenarios |
| Ov3 | The config files come from the manifest `SYSTEM/etc/dotkit/links.json` (nix/links.nix, nix/darwin.nix): the dotkit directory, the base links, the links of the configuration, the layers, and the backup extension of Home Manager. Without it, only Ov1 and Ov2. A manifest that is not JSON: warning "cannot read FILE: ERROR. The config files are not compared with the base."; another version than 1: warning "FILE has a format that this dotkit does not know. The config files are not compared." | scenarios |
| Ov4 | A base link that the configuration replaces, at `~/TARGET`. With null: "has no link: your configuration sets it to null, so the base file is off." With a file whose lines are the same as the base file (without the spaces at their ends and the blank lines at the end): "your FILE is the same as the base file. Remove your link of ~/TARGET." With a similar file (difflib: at least 50% of the lines the same, and at most 40 changed lines): "your FILE replaces the base file; N lines differ.", then the unified diff with one line of context, indented by 6, in colors when stderr is a terminal and `NO_COLOR` is not set. With another text file: "your FILE replaces the base file, and differs from it a lot.", then "P% of the lines are the same. Compare them: diff -u BASE YOURS". Else (not text, or missing): "your FILE replaces the base file." For a target with a layer, the text ends with "Its tool reads ~/LAYER after the base file: move your changes there, and remove your link of ~/TARGET." (for null: "put your settings there instead."). FILE is relative to the repository. | scenarios |
| Ov5 | For each link target and each directory above it, `~/PATH.EXT` that exists (EXT is the backup extension): "an apply moved your file aside for the link ~/PATH.", then "Move the settings that you need to ~/LAYER, then delete it." for a target with a layer, else "Compare it with ~/PATH, take what you need, then delete it." | scenarios |
| Ov6 | With `-n`, a link target that exists and is not a link into /nix/store: "apply moves your file to ~/TARGET.EXT and links the file of your configuration.", then " Move your settings to ~/LAYER first." for a target with a layer. | scenarios |
| Ov7 | The report, on stderr: the overlaps whose fingerprint (the values or the contents of both sides) is not in the state file, after "dotkit: your dotfiles repeat or replace parts of the dotkit base:", one per line as "  WHERE  TEXT" with WHERE padded. Then, for the others: "And N known overlap(s) with the dotkit base. dotkit doctor lists them." ("lists it" for one), or "Your dotfiles have N known ..." when no overlap is new. Without `-n`, when the overlaps changed, the state file `$XDG_STATE_HOME/dotkit/overlaps.json` (default `~/.local/state`) gets them all. | scenarios |
| Ov8 | `doctor` lists all overlaps of the active system (`/run/current-system`) on stdout, after "Your dotfiles repeat or replace these parts of the dotkit base:", or prints "Your dotfiles do not repeat or replace any part of the dotkit base." Without a manifest, first "SYSTEM has no link manifest, so the config files are not compared. Run dotkit apply." on stderr. It does not change the state file. Exit code 0. | unit (tests/commands/test_overlap.py) |
