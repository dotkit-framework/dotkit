# Parity inventory of the dotkit command

The behavior of the Bash scripts (`dotkit`, `scripts/dotkit-pkg`, and
`scripts/nix.sh`, deleted in steps 4 and 7 of CLI-PLAN.md; git history has
them), one row per behavior. The Python code must keep each row,
unless the row is a deviation or a Bash bug (the last two sections).
CLI-PLAN.md, section 4, explains the method.

The column "Covered by" says which test checks the row:

- **scenarios**: one or more scenarios of `cli/scenarios.py`, compared
  with the goldens in `cli/golden/`. `cli/test_parity.py` makes sure that each
  of these rows has a scenario, and that each scenario names rows of this file.
- **e2e**: `tests/e2e/*.sh` on a real Mac, in the CI job e2e.
- **unit (step N)**: a unit test of the Python code, written in step N of
  CLI-PLAN.md. The fake tools cannot reach the row, for example because the
  script runs a command by its absolute path.

All messages of dotkit start with `dotkit: ` and go to stderr, unless a row
says otherwise. "Usage error" means exit code 2 and the message, then
` Use dotkit --help.`. "Die" means exit code 1 and the message.

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

argparse reads the options of each command (DV9). An error of the options
prints the usage of the command and "dotkit CMD: error: MESSAGE" on stderr,
exit code 2.

| Row | Behavior | Covered by |
|---|---|---|
| O1 | Options can come anywhere after the command; the other words are the arguments. | scenarios |
| O2 | `-h`/`--help` after the command prints the help of the command on stdout, exit code 0. | scenarios |
| O3 | `--` ends the options: the words after it are arguments. | scenarios |
| O4 | An unknown option, an argument that a command does not take, or a long option that is not complete: an error. | scenarios |
| O5 | `--plan FILE` gives the wizard an absolute path: the directory resolved, the file name kept. Without FILE, or with a directory that does not exist: an error. | scenarios |
| O6 | The options: apply `-n`; upgrade `-n -y`; add `-n -y -f`; remove `-n -y`; migrate `-n -y -a`; tidy `-n -y --plan FILE`; clean `-n -y`; info, list, history, and rollback none. add, remove, and info need one tool or more. | scenarios |
| O7 | `clean` takes one age, `[0-9]+d`, default 14d; `list` takes nix, brew, or mise. Another value: an error. | scenarios |

## A. apply

| Row | Behavior | Covered by |
|---|---|---|
| A1 | apply takes no arguments (O4). | scenarios |
| A2 | Builds `path:REPO/nix#darwinConfigurations.mac.system` with the link `REPO/.build/system`, then runs `sudo -H /usr/bin/env NIX_REMOTE=daemon SYSTEM/sw/bin/nix-env -p /nix/var/nix/profiles/system --set SYSTEM` and `sudo SYSTEM/sw/bin/darwin-rebuild activate` (SYSTEM is the target of the link), then `mise install` in `$HOME`. | scenarios |
| A3 | When the build fails, apply stops with the exit code of nix and no message. Nothing is activated. | scenarios |
| A4 | When a sudo command fails, apply stops with its exit code; the later commands do not run. | scenarios |
| A5 | When `mise install` fails, apply stops with its exit code. | scenarios |
| A6 | `apply -n` builds, prints "Nix package changes:" on stdout, runs `nix store diff-closures /run/current-system SYSTEM`, the Homebrew preview (A7), `mise install --dry-run` in `$HOME`, then "No change. Run dotkit apply to apply." on stderr (without `dotkit: `). | scenarios |
| A7 | The Homebrew preview runs only when `/opt/homebrew/bin/brew` exists and `homebrew.onActivation.cleanup` of the configuration is not `none`. | scenarios |
| A8 | The preview writes the Brewfile of the configuration to `REPO/.build/Brewfile` and runs `/opt/homebrew/bin/brew bundle cleanup --file=FILE` with `HOMEBREW_NO_AUTO_UPDATE=1` and stdin from /dev/null. Exit code 1 is accepted only after the complete report; the report loses its last line and the part from "Would \`brew cleanup\`:". A report that is left prints as "Homebrew removes:" and the lines. Another failure: die "the Homebrew cleanup preview failed with exit code N." | unit (step 6), e2e |
| A9 | With `DOTKIT_DIR`, the build evaluates `...system.drvPath` with the override (G4) and builds `DRV^*` without the flake, so nix keeps its progress bar. | scenarios |
| A10 | With `DOTKIT_DIR`, the stderr of each nix command that reads the flake goes through a filter. The filter drops "warning: not writing (modified )lock file of flake ..." and the lines after it that start with `• `, `  →`, or four spaces. Other lines pass at once; stdout does not change. | scenarios |

## U. upgrade

| Row | Behavior | Covered by |
|---|---|---|
| U1 | The arguments are sources (nix, brew, mise) or flake inputs. A name matches an input of nix/flake.lock, or `dotkit/NAME` of the checkout's lock. Another word: usage error "'X' is not a source or a flake input. Use nix, brew, mise, or: NAMES. Run dotkit upgrade --help." (NAMES as in C3, on one line). | scenarios |
| U2 | Without arguments: nix, brew, and mise. | scenarios |
| U3 | `nix` with input names updates all inputs. | scenarios |
| U4 | Nix: "Checking Nix..." on stderr (without `dotkit: `). For each lock file (nix/flake.lock; with `DOTKIT_DIR` also the checkout's flake.lock): a backup, then `nix flake update [INPUTS] --flake path:REPO/nix` (or `git+file://DIR`), with its output captured. A lock file without selected inputs is skipped when inputs are given. On failure: the output, then die "nix flake update failed for FILE. The lock files are restored." | scenarios |
| U5 | One plan row per input whose `locked` changed: `LABEL+NAME`, "YYYY-MM-DD REV7" before and after (UTC). A lock file without a change is restored from the backup. | scenarios |
| U6 | brew: "Checking Homebrew...", `brew update --quiet` with its output captured (on failure: the output, then die "brew update failed."), then `brew outdated --json=v2` with `HOMEBREW_NO_AUTO_UPDATE=1`. Rows only for the formulae and casks of the base and repository lists: name, the installed versions joined by spaces, the current version. | scenarios |
| U7 | mise: "Checking mise...", `mise outdated --json` in `$HOME`: rows of name, current, latest. When it fails, upgrade stops with its exit code and no message. | scenarios |
| U8 | No rows: "Everything is up to date." on stdout, exit code 0. | scenarios |
| U9 | The plan on stdout: `  %-5s %-36s %-22s -> %s` per row, nix rows, then brew, then mise. | scenarios |
| U10 | With nix rows: a build, then "\nNix package changes:" and diff-closures on stdout. | scenarios |
| U11 | `-n`: the lock files are restored, "No change. Run dotkit upgrade to apply." on stderr. | scenarios |
| U12 | The question "Upgrade? [y/N] " (rows C-confirm). No: the lock files are restored, die "nothing changed." | scenarios |
| U13 | Nix rows: a switch (A2 without mise); when it fails, die "the switch failed. The lock files are restored." brew rows: the Brewfile of the configuration goes to `brew bundle install --file=-` on stdin, with `HOMEBREW_NO_INSTALL_CLEANUP=1`. mise rows: `mise upgrade` in `$HOME`. | scenarios |
| U14 | After an upgrade with nix rows: "Commit nix/flake.lock to keep the upgrade." and with `DOTKIT_DIR` "Commit flake.lock in DIR too, if it changed." on stderr (without `dotkit: `). | scenarios |
| U15 | Any exit before the switch restores the lock files: an error, a refused question, and a signal. | scenarios (error, question); unit (step 6) for signals |
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
| H2 | `history` reads the link `/nix/var/nix/profiles/system` (`system-N-link`). Without `system-(N-1)-link`: die "there is no previous generation." Else it prints "system-(N-1)-link -> system-N-link" on stdout and runs `nix store diff-closures` of the two. | unit (step 6), e2e |
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
| L5 | mise: the `[tools]` table of the base and the repository config/mise.toml, read as TOML (core/mise_toml.py), in the order of the file. The version is the string, the `version` of a table, the items of an array joined by ", ", or else the value as compact JSON. A file that is not valid TOML: "dotkit: FILE is not valid TOML: ERROR.", exit code 1 (DV7). | scenarios |
| L6 | A list file has the names of nix/read-list.nix: the text of each line before `#`, without the whitespace at its ends (also `\r`); blank lines and comments give none (DV7). taps.txt: the first word of such a name. mas.txt: the lines `ID name` of nix/read-mas.nix. | scenarios; unit (tests/core/test_files.py, against the Nix code) |
| L7 | When the evaluation of the App Store apps fails, list stops with exit code 1, after the rows before. | scenarios |

## N. Names for the completion

| Row | Behavior | Covered by |
|---|---|---|
| N1 | `user`: the names of the repository's packages.txt, brews.txt, casks.txt, mas.txt (as `mas:ID`), and the mise keys; sorted (deviation DV4), each once. | scenarios |
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
| R17 | Versions compare with `builtins.compareVersions` of Nix, by `nix eval --expr '{ a, b }: builtins.compareVersions a b' --argstr a A --argstr b B`, without a leading `v` (DV8). When nix cannot compare them: die "nix could not compare the versions A and B." | scenarios |
| R18 | The command of a formula, a mise tool, a PyPI package, or a spec that is not npm is the `meta.mainProgram` of the Nixpkgs package with the same name (the last part of the name), when Nixpkgs has one. That can be one more nix evaluation. Else the command of the lookup, else the entry after its last `:` or `/`. | scenarios |
| R19 | For an `npm:` spec, the command comes from the npm registry, else the package name after its last `/`. | scenarios |
| R20 | The package of a spec has no backend and no version: `npm:@scope/pkg@1.0` is `@scope/pkg`. | scenarios |
| R21 | The lookups: `brew info --json=v2 --cask|--formula NAME` with `HOMEBREW_NO_AUTO_UPDATE=1` (only with brew on PATH and a valid name); a cask is an app when an artifact has `app`, `pkg`, or `suite`, and its command is the file name of the first `target` of a `binary` artifact. npm: `curl -fsSL --max-time 10 https://registry.npmjs.org/NAME/latest` (the first `/` as `%2F`); only with `bin`; the command is the first key of `bin` in sorted order. PyPI: `curl ... https://pypi.org/pypi/NAME/json`. mise: `mise registry`, `mise latest` (an empty version when it fails), `mise tool --description` (`[none]` is empty), in `$HOME`. Tabs and line breaks in the fields become spaces. | scenarios |

## Pc. The candidates

| Row | Behavior | Covered by |
|---|---|---|
| Pc1 | On stdout, in the order app, nix, mise, formula, cask, npm, pypi, spec: `  %s %-30s %-14s %s`, a check mark for the selection, the spec, the version or `?`, and the description. When stdout is a terminal, the description is cut to the width of the terminal minus 50 (at least 20), with `…`. | scenarios; unit (step 3) for the cut |

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
| Ad13 | List changes first: a backup of the Nix lists (packages.txt, brews.txt, casks.txt, mas.txt), the edits, the build. When an edit fails: restore, die "could not change the Nix files. They are restored."; when the build fails: restore, die "the build failed. The Nix files are restored."; on SIGINT, SIGTERM, SIGHUP, or SIGPIPE: restore, "\ndotkit: stopped. The Nix files are restored.", exit code 130. Then the activation of the built system and `mise install` (DV10); when either fails: die "dotkit apply failed. The Nix files keep the change. Fix the problem, then run dotkit apply." | scenarios; unit (tests/pkg/test_pkg.py) for signals |
| Ad14 | Then mise: `mise use -g SPEC` in `$HOME` for each mise entry, in order; when one fails: die "mise could not add SPEC. The tools before it in the list are added." | scenarios |
| Ad15 | The entries: Nixpkgs to packages.txt, casks to casks.txt, formulae to brews.txt, App Store apps to mas.txt as "ID LABEL"; mise entries are NAME, `npm:NAME`, `pipx:NAME`, or the spec. | scenarios |

## Pk. The picker of add (DV2)

New in the Python code. The goldens of these rows come from the Python code.

| Row | Behavior | Covered by |
|---|---|---|
| Pk1 | add asks for the source of a bare name when stdin and stderr are a terminal, without `-y` and `-n`, and when two sources or more have the tool, or when the rules select none of them (R10, R15). It does not ask for a prefix, for one source, when the nix evaluation fails, or when no source has the tool. | scenarios |
| Pk2 | The list is questionary.select on stderr: the title "TOOL: REASON. Select the source:", one row per candidate in the order of Pc1 (spec, version, description, and for npm and PyPI "(anyone can publish here; check the package)", cut to the width of the terminal), then "Skip this tool" and "Stop, and change nothing". | scenarios |
| Pk3 | The arrow keys and `j`/`k` move, and the list wraps at its ends; Enter selects. "Skip this tool" skips it ("TOOL: skipped" on stdout). "Stop, and change nothing": die "nothing changed." | scenarios |
| Pk4 | The cursor starts on the source that the rules select. When they select none, it starts on "Skip this tool"; the title is "TOOL: Homebrew has an app and a command-line tool with this name. Select one:" or "TOOL: only npm or PyPI has this name. Anyone can publish there: check the package, then select it:". | scenarios |
| Pk5 | After the selection, add goes on as without the picker: the checks of Ad6 and Ad7, the plan line, the candidates, the question. For a source other than the selection of the rules, the reason is "selected in the list". | scenarios |
| Pk6 | With `TERM=dumb` or without `TERM`: a numbered list on stderr and the question "Select 1-N, Enter for D, s to skip, q to stop: " (without ", Enter for D" when the rules select none), asked again until the answer is valid. The end of the input stops. | scenarios |
| Pk7 | Ctrl-C in the list: exit code 130, nothing changed. | scenarios |

## Rm. remove

| Row | Behavior | Covered by |
|---|---|---|
| Rm1 | A prefix selects the file: `nix:` packages.txt, `brew:` brews.txt or casks.txt, `cask:` casks.txt, `mas:` mas.txt, another prefix config/mise.toml. | scenarios |
| Rm2 | A declaration in the base: "TOOL: in the dotkit base (FILE). Only dotkit can remove it." | scenarios |
| Rm3 | No declaration: "TOOL: not in FILES. See dotkit info TOOL." | scenarios |
| Rm4 | More than one declaration: "TOOL: in more than one place: F1,F2. Use a prefix (nix:, brew:, cask:, mise:), or remove it by hand." Two lines of one file count as two places. | scenarios |
| Rm5 | The plan line on stdout: "TOOL: remove ENTRY from FILE"; an entry that comes again is skipped. | scenarios |
| Rm6 | When one tool failed: die "nothing changed. Fix the errors above, then run the command again." | scenarios |
| Rm7 | `-n`: "No change. Run the command without -n to remove." on stderr. | scenarios |
| Rm8 | The question "Remove?"; no: die "nothing changed." | scenarios |
| Rm9 | List changes as in Ad13. Then, for a removed formula or cask that `brew list --KIND NAME` shows: `brew uninstall --KIND NAME` (with `HOMEBREW_NO_AUTO_UPDATE=1`); when it fails: "could not uninstall the KIND NAME. Run: brew uninstall --KIND NAME". For an App Store app: "the App Store app ID stays installed. dotkit does not uninstall App Store apps." | scenarios |
| Rm10 | Then `mise unuse -g KEY` in `$HOME` for each mise entry; when one fails: die "mise could not remove KEY. The tools before it in the list are removed." | scenarios |

## X. Installed commands

| Row | Behavior | Covered by |
|---|---|---|
| X1 | A global tool has a command when it is in `/etc/profiles/per-user/USER/bin`, `/run/current-system/sw/bin`, `~/.nix-profile/bin`, or `/opt/homebrew/bin`, else when `mise which BIN` in `$HOME` finds it. The PATH of the caller does not count. | scenarios (mise); unit (step 3) for the directories |
| X2 | The origin of a path, after `realpath`: `/nix/store/HASH-NAME/...` is "nix NAME"; `MISE_DATA_DIR/installs/TOOL/VERSION/...` (default `~/.local/share/mise`) is "mise TOOL VERSION"; `/opt/homebrew/Cellar/NAME/VERSION/...` is "brew NAME VERSION"; `/opt/homebrew/Caskroom/...` and `/Applications/...` are "brew (cask)"; else the path. | scenarios |

## F. List files

| Row | Behavior | Covered by |
|---|---|---|
| F1 | An append adds the missing final newline first, then "NAME\n". | scenarios |
| F2 | A delete removes each line whose name (L6) is the name, and each line of mas.txt with the ID. The other lines stay as they are, also without a final newline (DV7). The file is written in place, so a link stays a link. | scenarios |
| F3 | The ID of an App Store app name: the first app of mas.txt (L6), or of the planned apps, whose name is the name exactly. | scenarios |

## Deviations

| Row | Deviation | Reason |
|---|---|---|
| DV1 | Python needs no jq, so "jq is not on PATH. Run dotkit apply first." goes away. | The Python standard library reads JSON. |
| DV2 | `dotkit add` asks for the source of a bare name in a terminal (CLI-PLAN.md, section 7). | The new feature. |
| DV3 | Bash prints its own errors with a line number: `cd` when `DOTFILES` or `DOTKIT_DIR` is not a directory, and "command not found". Python prints only the dotkit message ("DOTFILES is not a directory: X", "NAME: command not found"). The exit codes stay (1, 2, and 127). | Bash internals are not part of the interface. |
| DV4 | `__complete tools-*` prints the names sorted by code point, not with `sort` in the locale of the user. | zsh sorts the completions itself. |
| DV5 | A list file or config/mise.toml that does not exist reads as empty. In Bash, awk printed an error, and most commands stopped with exit code 2. | The template has all the files; an empty list is the safe reading. |
| DV6 | Output that dotkit cannot use is a message, exit code 1: a lock file that is not a JSON object ("FILE is not valid JSON: ...", "FILE is not a JSON object."), output of brew, mise, or nix that is not a JSON object ("COMMAND did not print a JSON object."), and a file that dotkit cannot read or write ("dotkit: [Errno N] ..."). Bash printed the error of jq (exit code 5) or went on with no data, and "Everything is up to date." could be false. | Tracebacks and false reports are defects. |
| DV7 | The readers of the lists and of config/mise.toml have the rules of the code that installs from them: nix/read-list.nix, nix/read-taps.nix, nix/read-mas.nix, and the TOML parser of mise. The awk of the Bash scripts took the first word of a line, kept `\r`, did not read indented mise keys, kept comments in mise values, and did not see `[tools.NAME]` tables, so dotkit could disagree with what Nix and mise install. A delete keeps the other lines as they are. The wizard uses the same readers. | One rule for each file, the rule of its real reader. |
| DV8 | Versions compare with `builtins.compareVersions` of Nix, not with `sort -V` of macOS. | The question is whether Nixpkgs has the current release, and Nixpkgs orders its versions with this function. |
| DV9 | argparse reads the options: its usage line, its error messages ("dotkit CMD: error: ..."), and the help of each command, made from COMMANDS of dotkit/cli.py. Each command accepts only its own options (the pkg commands accepted -n, -y, and -f all three; the others rejected an option of another command with their own message). `-h` after an unknown option prints the help. The rules stay: options anywhere, `--`, no abbreviations, exit code 2. | A standard parser, not one of our own; the text of an error is not an interface. |
| DV10 | add and remove activate the system that they built, then run `mise install`, in the same process. Bash ran `dotkit apply`, which evaluated and built the system a second time. | One evaluation fewer; the steps are the same. |

## Bash bugs

The Python code does what the Bash code means to do. The scenarios of these
rows ran with the Bash scripts only; their goldens keep the Bash behavior.
The scenarios of the correct behavior are option-plan-missing-dir-fixed
(B1) and upgrade-nix-switch-stops (B2); unit tests cover B3
(tests/core/test_runtime.py), and B4 to B6 and DV6 (tests/commands/test_commands.py).

| Row | Bug | Python |
|---|---|---|
| B1 | `--plan DIR/FILE` with a directory that does not exist: the `cd` error goes to stderr, and the wizard gets `--plan /FILE`. The usage error "'X' is not in a directory that exists." never shows. | The usage error, exit code 2. |
| B2 | `upgrade` runs the switch in `switch \|\| ...`, where Bash does not stop at an error. When `nix-env --set` fails, `darwin-rebuild activate` runs anyway, and only the last exit code counts. | The switch stops at the first failure, as in `apply`. |
| B3 | With `DOTKIT_DIR`, add and remove run the build in `if ! build_system`, where Bash does not stop at an error: when the evaluation of `drvPath` fails, `nix build` runs with the path `^*`. | The build stops with the exit code of the evaluation. |
| B4 | `upgrade` pipes the evaluation of the Brewfile into `brew bundle install --file=-`. When the evaluation fails, `brew bundle` still runs, with no Brewfile. | upgrade stops with the exit code of the evaluation, before `brew bundle`. |
| B5 | `dotkit upgrade -n \| head`: when the reader of the output goes away, SIGPIPE ends the script before its EXIT trap restores the lock files. | SIGPIPE is one of the signals of `backup.on_signals()`: the lock files are restored, exit code 141. |
| B6 | (A defect of the first Python port, not of Bash.) SIGTERM during `nix flake update` restored the lock files while nix still ran, so nix could write them after the restore. Bash ran its trap after the command. | `proc.run` passes SIGTERM and SIGHUP on to the command and waits for it; the restore blocks the signals (`backup.shielded()`). |
