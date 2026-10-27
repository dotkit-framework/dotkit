# Design: `dotkit tidy`

Status: all three phases are implemented in `dotkit_migrate/tidy/`: apps
(sections 3 to 6), command-line tools (section 8.1), and installer packages
(section 8.2).

## 1. The problem

A Mac that someone used before dotkit has apps from many places: Homebrew
casks, the App Store, DMG downloads, `.pkg` installers, and Setapp. The
migration wizard and `dotkit migrate` carry over what a package manager
already manages. An app from a DMG stays outside the lists, and a new Mac
from the same repository does not get it.

`dotkit tidy` finds those apps, proposes a clean source for each one, lets
you edit the proposal, and then changes the Mac. The data of an app stays:
tidy never touches `~/Library`.

It is a separate command, not a screen of the setup wizard. Setup must stay
safe on a Mac that nobody looked at yet, and tidy installs and moves apps.

## 2. The flow

```
dotkit tidy [-n] [--plan FILE]
```

1. **Scan** (read-only). The apps, their sources, and the casks that match
   them (section 3).
2. **Plan.** One line per app, with a verb and a reason (section 4). Tidy
   opens the plan in `$EDITOR`, as `git rebase -i` does. You change verbs,
   save, and close the editor. An empty file or a plan without changes stops
   the run, and nothing changes.
3. **Check.** Tidy parses the edited plan and checks each line against the
   scan (section 5). A bad line stops the run with the line number.
4. **Confirm.** Tidy prints the commands that it will run, grouped by verb,
   and asks once.
5. **Apply.** Tidy runs the commands one app at a time, then adds the apps
   that it installed to the lists (section 6).

`-n` prints the plan with its default verbs and the commands, and changes
nothing. `--plan FILE` reads an edited plan from a file instead of the
editor, for scripts and for the e2e test. `-y` skips the question; without
`--plan`, it also skips the editor and applies the default verbs. Without a
terminal, without `--plan`, and without `-y`, tidy stops with an error, as
`dotkit migrate` does.

The editor is `$VISUAL`, then `$EDITOR`, then `vi`. Tidy runs it through
`sh`, as Git does, so `code --wait` works. The plan file is a temp file with
mode 0600, and tidy removes it after the editor.

## 3. Scan

### 3.1 The apps

Tidy lists the `.app` bundles directly in these directories:

| Directory | Note |
|---|---|
| `/Applications` | also one level of subdirectories, such as `Utilities` |
| `~/Applications` | |
| `/Applications/Setapp` | the source is always Setapp |
| `~/Applications/JetBrains Toolbox` | the source is always JetBrains Toolbox |

It skips links (Safari is a link into the system cryptex), `*.localized`
directories (the web apps of Chrome and Brave), `Nix Apps`, and
`Home Manager Apps`. `/System/Applications` is not scanned.

For each app, tidy reads `Contents/Info.plist` with `plistlib`:
`CFBundleIdentifier`, `CFBundleShortVersionString`, and `CFBundleVersion`.
An app without a readable `Info.plist` gets no bundle ID and no version.

### 3.2 The source of an app

Tidy gives each app one source. The first rule that matches wins:

| Source | How tidy knows |
|---|---|
| `cask` | an installed cask has the app as an artifact or by its package (`brew info --json=v2 --installed --cask`, one command), or the cask that section 3.4 matches is installed |
| `mas` | `Contents/_MASReceipt/receipt` exists |
| `setapp` | the app is in `/Applications/Setapp` |
| `toolbox` | the app is in `~/Applications/JetBrains Toolbox` |
| `pkg` | the receipt of a package that is not Apple's lists files in the app (section 8.2) |
| `system` | the bundle ID starts with `com.apple.` (Xcode from a `.xip`, for example) |
| `hand` | none of these: a DMG, a zip, or a copy |

Tidy also records two facts that change the default verb:

- **Not yours:** the app or its parent directory is not writable by you,
  for example an app that is owned by root (Tailscale here). Tidy cannot
  move it to the Trash without sudo, and an MDM may manage it.
- **Running:** a process runs from inside the bundle (`ps -axo comm=`).

The last rule of the `cask` row is for pkg casks. Such a cask often names
no app, and the package ID of the app can differ from the cask's:
SF Symbols has `com.apple.pkg.SFSymbolsAuto`, and the cask names
`com.apple.pkg.SFSymbolsPackage`. Google Drive has no receipt at all. Their
bundle IDs find the casks.

For an App Store app, tidy reads the App Store ID from Spotlight
(`mdls -raw -name kMDItemAppStoreAdamID <app>`), so it needs `mas` only to
install, not to find the ID. An app whose path has ` #` or a line break is
not a line of the plan (section 4.1); the header of the plan names it.

### 3.3 The casks

Tidy needs the `app` artifacts and the bundle IDs of all casks, not only
the installed ones. `brew info` for each of 7800 casks is too slow, so tidy
reads the bulk file that `brew update` downloads:
`~/Library/Caches/Homebrew/api/internal/packages.<tag>.jws.json.payload`.
Its first line is the signature header; the second line is the JSON. One
read takes about 0.1 seconds. The newest file wins when there are several
tags.

From each cask, tidy indexes:

- the names of its `app` artifacts and their `target`;
- the `.app` paths under `/Applications` in its `uninstall` `delete` list
  (pkg casks such as `zoom` have no `app` artifact);
- the `pkgutil` IDs of `uninstall`;
- the bundle IDs: `quit`, `launchctl`, `signal`, and `login_item` of
  `uninstall`, and the bundle-ID part of `zap` paths under
  `~/Library/{Preferences,Containers,Group Containers,Caches,HTTPStorages,
  Saved Application State,Application Support}`.

This file is an internal format of Homebrew. Tidy uses it only to find
candidates. For the casks that the plan names, tidy then runs one
`brew info --json=v2 --cask <token>...` and takes `version`,
`auto_updates`, and the artifacts from that stable format. `brew info`
fails as a whole when one token is unknown, so tidy passes only tokens of
`cask_names.txt`. `auto_updates` is `null`, not `false`, for a cask
without it. When the bulk
file is missing or does not parse, tidy adds a scan warning ("run
`brew update`, then dotkit tidy again") and proposes no cask.

Every brew command passes `catalog.BREW_ENV`.

### 3.4 Matching an app to a cask

1. The candidates are the casks whose `app` artifact or `uninstall` path
   has the name of the app, plus the pkg casks whose `pkgutil` ID equals the
   `pkgid` of the app. When no cask has the name, the candidates are the
   casks that list the bundle ID of the app. Tailscale.app has no pkg
   receipt, and the cask `tailscale-app` has no `app` artifact, but it
   lists `io.tailscale.ipn.macsys` in `uninstall` `quit`.
2. A candidate that names the bundle ID of the app, in `uninstall` or in a
   `zap` path, is **confirmed**. When there are confirmed candidates, only
   they stay.
3. Otherwise, a candidate whose `uninstall` (`quit`, `launchctl`, `signal`)
   names other bundle IDs only is **another edition**: the data does not
   follow (1Password 7 and 8). The `zap` paths do not count here, because
   they often keep the IDs of old versions: the cask `monitorcontrol` names
   `me.guillaumeb.MonitorControl`, and the app is
   `app.monitorcontrol.MonitorControl`.
4. When candidates remain, tidy prefers a token without `@`, so `slack`
   wins over `slack@beta`.
5. When more than one remains, the token that equals the app name in
   Homebrew's token form wins (`google-chrome`). Then a token that is the
   start of all the others wins (`zoom`, not `zoom-for-it-admins`).
   Otherwise the app has no match, and the plan comment names the
   candidates.

An app artifact with a `target` installs under the name of the target: the
cask `telegram-desktop` has `Telegram.app`, which goes to
`/Applications/Telegram Desktop.app`. So the index has the name of the
target.

On the Mac where this design was written, this finds the right cask for
every app with a cask: Telegram is `telegram` (bundle ID
`ru.keepcoder.Telegram`), not `telegram-desktop`, and Helium is
`helium-browser`, not `helium`.

## 4. The plan

### 4.1 Format

```
# dotkit tidy: 14 apps. Change a verb, save, and close the editor.
# An empty file stops tidy, and nothing changes.
#
# adopt    the cask takes over the app in place. Nothing is downloaded again
#          when the cask auto-updates.
# replace  quit the app, install the cask over it. The app data stays.
# add      the app is from a cask or the App Store already: add it to the list.
# keep     change nothing.
# trash    move the app to the Trash.

adopt    cask:brave-browser   /Applications/Brave Browser.app   # by hand; the cask auto-updates
adopt    cask:whatsapp        /Applications/WhatsApp.app        # by hand; the cask auto-updates
replace  cask:joplin          /Applications/Joplin.app          # by hand; 3.6.14, the cask has 3.7.21
replace  cask:anydesk         /Applications/AnyDesk.app         # by hand; 9.6.3, the cask has 9.8.1
add      mas:409201541        /Applications/Pages.app           # from the App Store, not in nix/mas.txt
keep     /Applications/Tailscale.app                            # owned by root; or: replace cask:tailscale-app (a pkg, asks for your password)
keep     /Applications/Pixelmator Pro.app                       # from the App Store; no cask
keep     /Applications/Xcode-26.6.0.app                         # Apple
keep     /Applications/Home VPN.app                             # by hand; no cask
```

The lines above are from the Mac where this design was written.

The rules of a line:

- The verb, then the thing, then the path. ` #` and the text after it are a
  comment. Blank lines and lines that start with `#` are ignored.
- The thing is `cask:<token>` or `mas:<id>`. `keep` and `trash` take only
  the path; tidy ignores a thing on such a line, so you can change a verb
  without deleting the cask.
- You may change the verb, and the token of `adopt` and `replace`. You may
  delete a line: that is `keep`.
- The path identifies the app. Paths have spaces, so the path is the rest of
  the line before the comment. A path never contains ` #`; an app whose path
  does, is not in the plan, and the header names it.

Apps that the lists already have are not lines. The header counts them.

### 4.2 Default verbs

The first row that matches gives the verb. `trash` is never a default.

| The app | Default | Comment |
|---|---|---|
| source `cask` or `mas`, in the lists | not a line | |
| source `cask`, not in `nix/casks.txt` | `add cask:<token>` | |
| source `mas`, Spotlight has no ID | `keep` | |
| source `mas`, without `mas` | `keep` | run `dotkit add brew:mas` first |
| source `mas`, not in `nix/mas.txt` | `add mas:<id>` | when a cask exists, it says that the App Store data and license do not carry over (4.3) |
| source `setapp`, `toolbox`, or `system` | `keep` | |
| not yours | `keep` | owned by `<user>`; `or: replace cask:<token>` when a cask matches |
| a cask of another edition | `keep` | the data does not follow (4.3) |
| several candidates | `keep` | the candidates |
| no cask | `keep` | no cask |
| a matching cask with `auto_updates` | `adopt` | |
| a matching app cask, the same version | `adopt` | |
| a matching app cask, the app is older | `replace` | both versions |
| a matching app cask, the app is newer | `keep` | `replace` would downgrade it |
| a matching pkg cask | `replace` | the installer can ask for your password |

A match that is not confirmed (section 3.4) and whose cask names other
bundle IDs gets a note: the cask copy may have another bundle ID, and then
it does not find the app data. `adopt` keeps the app, so it stays. `replace`
becomes `keep`, with `or: replace cask:<token>`. Raspberry Pi Imager is such
a case: the app is `org.raspberrypi.imagingutility`, and the cask names
`com.raspberrypi.imagingutility`.

The version comparison uses the numbers in `CFBundleShortVersionString` of
the app and in the first comma-separated part of the cask `version`, without
zeros at the end: `7.2.2 (88465)` equals `7.2.2.88465`. When one of them has
no numbers, the default is `keep`. Homebrew's own check (`brew install --adopt`, in
`cask/artifact/moved.rb`) compares both `CFBundleShortVersionString` and
`CFBundleVersion` of the downloaded app, which tidy cannot see before the
download. So a default `adopt` can still fail. Section 6 says what happens
then.

### 4.3 Cases that tidy flags and never changes by default

- **App Store to a cask.** The App Store app has its data in
  `~/Library/Containers/<bundle id>`, and the cask copy is often another
  build with another container. A license from the App Store does not carry
  over. The comment says so; `replace` on such a line is allowed.
- **Another edition.** A cask with another bundle ID, such as 1Password 8
  for 1Password 7, starts with empty data. The comment names both IDs.
- **A pkg with system parts.** When `pkgutil --files` of the app's package
  has launch daemons, kernel extensions, or system extensions, the comment
  says that only the vendor's uninstaller removes them. `trash` on such a
  line moves only the app.
- **Not yours, Setapp, JetBrains Toolbox.** Another tool manages these.
- **A running app.** `replace` quits it first (section 6). `adopt` does not
  need that.

## 5. Checking the edited plan

Before it asks, tidy checks each line:

- The verb is one of the five, and the thing fits the verb: `add` takes the
  cask that has the app, or the App Store ID of the app.
- The path is an app of the scan, and no other line has it. A path that the
  scan did not find is an error, so a typo cannot trash another app.
- The token of `adopt` and `replace` is a cask in `cask_names.txt`. A token
  that the scan did not propose gets a note ("you chose the cask `<token>`;
  the scan proposed `<other>`"), not an error. Tidy runs `brew info` for it
  before the check.
- `adopt` with a pkg cask is an error: a pkg cask has nothing to adopt. Use
  `replace`.
- `adopt` or `replace` of an App Store app gets a note: the cask copy does
  not get the App Store data and license.
- `trash` of an app from a cask is an error: `dotkit remove cask:<token>`
  uninstalls it. `trash` of an app that is not yours is an error too.
- Just before it runs a line, tidy reads the `Info.plist` of the app again.
  When the bundle ID or the version changed since the scan, tidy skips the
  line and reports it.

## 6. Apply

Tidy runs each line on its own. A line that fails does not stop the others.

| Verb | Commands |
|---|---|
| `adopt` | `brew install --cask --adopt <token>` |
| `replace` | quit the app if it runs (`osascript -e 'quit app id "<bundle id>"'`, then wait up to 30 s), then `brew install --cask --force <token>` |
| `add` | nothing on the Mac |
| `trash` | quit the app if it runs, then `/usr/bin/trash <path>`, which keeps "Put Back" in the Finder; on a macOS without `/usr/bin/trash`, the Finder moves it |
| `keep` | nothing |

Tidy runs `brew` itself, before it changes the lists. So a version mismatch
on `adopt` fails here, with brew's message, and not in the middle of
`dotkit apply`. The failed line is reported with its next step: "the app
differs from the cask. To use the cask, set the line to `replace`." Tidy
never falls back to `--force` by itself.

After the commands, tidy adds a cask for each `adopt`, `replace`, and
`add cask:` line that worked to `nix/casks.txt`, and each `add mas:` line
to `nix/mas.txt`. It writes them as `dotkit migrate` does: through `Docs`
in adopt mode, `ListDoc`, and `writer.apply_edits`, with the comment
`# Added by dotkit tidy on <host>.`. It writes the lists also after a
`Ctrl-C`, for the lines that worked until then.

Tidy does not run `dotkit apply`. The casks are installed already, so the
next apply only records them. The last line says so. When the lists then
have every installed cask, tidy also says that
`homebrew.onActivation.cleanup = "uninstall"` is now an option, and that
`dotkit apply -n` shows what it would uninstall. Tidy does not change
`nix/darwin.nix`.

The exit code is 0 when every line worked, 1 when a line failed or you said
no, 2 for a plan with errors, and 130 after `Ctrl-C`.

## 7. Where the code goes

In the migrate package, as a new mode next to `setup` and `adopt`:
`dotkit-migrate tidy --repo <repo> --declared <json>`. `dotkit tidy` in the
`dotkit` script calls it like `cmd_migrate`. `__main__.py` imports
`tidy/cli.py` only in this mode. The reasons: the same Python
3.9 runtime, `Context`, `FakeRunner`, `fake.Home`, `Declared`, `ListDoc`,
`writer.apply_edits`, and the secret guard of the list lines.

```
dotkit_migrate/tidy/
  __init__.py
  apps.py      the scan: apps, sources, the cask index, matching. Read-only, through ctx.runner.
  plan.py      pure: facts -> plan lines and text; parse and check an edited plan
  actions.py   the only module that changes the Mac: brew install, quit, trash
  cli.py       the tidy mode: editor, confirmation, apply, the list edits
```

Rules:

- `apps.py` and `plan.py` follow the rules of `migrators/`: no writes, no
  `subprocess`, commands only through `ctx.runner`. The AST no-write test
  covers them too (`test_registry.py`).
- `actions.py` gets an `Actions` object (`brew_install`, `quit_app`,
  `trash`), and tests pass a fake that records the calls. Only `cli.py`
  creates the real one.
- `cli.py` writes the plan to a temp file (mode 0600) for the editor and
  removes it at the end. The list edits go through `writer.apply_edits`.
- `Docs` has a `by` argument for the comment of the lists in adopt mode:
  `dotkit migrate` by default, `dotkit tidy` here.
- Tidy is not a migrator in `MIGRATORS`. Setup and `dotkit migrate` stay as
  they are, and their goldens do not change. ARCHITECTURE.md section 1 gets
  one sentence: the package changes the Mac only in the tidy mode, through
  `tidy/actions.py`.

## 8. Phases

1. **Apps:** sections 3 to 6.
2. **Command-line tools:** section 8.1.
3. **Installer packages:** section 8.2.

### 8.1 Command-line tools

The same plan has a second part, after the apps, for old copies of
command-line tools. `dotkit migrate` already adds the Homebrew, npm, pipx,
uv, cargo, and Go tools that the lists do not have, so tidy does not list
those again. After `dotkit migrate` and `dotkit apply`, the old copies of
such tools are redundant, and tidy proposes to remove them.

What tidy scans (`tidy/clis.py`, read-only, with the rules of section 7):

| Kind | Where | The thing | Its path in the plan | Uninstall |
|---|---|---|---|---|
| `brew` | `brew leaves --installed-on-request` | `brew:<formula>` | `<prefix>/Cellar/<formula>` | `brew uninstall <formula>` |
| `npm` | the global `node_modules` of each node that the scan of `node_tools` finds (Homebrew, nvm, fnm, asdf, `~/.npm-global`, ...) | `npm:<package>` | the package directory | `<prefix>/bin/npm uninstall -g <package>`, with `<prefix>/bin` first in PATH, so that the npm of that node runs |
| `pipx` | `$PIPX_HOME/venvs` | `pipx:<package>` | the venv | `pipx uninstall <package>` |
| `uv` | `$UV_TOOL_DIR` | `uv:<package>` | the tool directory | `uv tool uninstall <package>` |
| `cargo` | `$CARGO_HOME/.crates2.json` | `cargo:<crate>` | its first command in `$CARGO_HOME/bin` | `cargo uninstall <crate>` |
| `go` | `$GOBIN`, or `$GOPATH/bin` | `go:<name>` | the file | none: `trash` |
| `file` | `~/.local/bin`, `~/bin`, `/usr/local/bin`, `$CARGO_HOME/bin` | none | the file or the link | none: `trash` |
| `manager` | the version managers of `catalog.VERSION_MANAGERS` and the zsh frameworks of `catalog.FRAMEWORKS` | none | their directory | none: `trash` |

A `file` is an executable that no other row has. The scan skips links into
the Nix store, into an app (`/usr/local/bin/docker` of OrbStack), into a
Homebrew `Cellar` or `Caskroom`, into a pipx venv, a uv tool, or a
`node_modules` directory (other rows have those), and links to a file in
the same directory (the commands of rustup).

The default verbs:

| The tool | Default | Comment |
|---|---|---|
| `brew`, and the lists or the base have it from Homebrew | not a line | |
| `brew`, and dotkit has it from Nix or mise (`Declared.brew`) | `uninstall` | what dotkit has |
| `npm`, `pipx`, `uv`, `cargo`, `go`, and dotkit has its mise key or its command (`Declared.mise_key`, `Declared.tool`) | `uninstall` (`trash` for `go`) | what dotkit has |
| one of these, and dotkit does not have it | not a line | `dotkit migrate` adds it |
| `npm` without an npm next to it | `keep` | no npm to uninstall it |
| `file`, and dotkit has the command | `trash` | what dotkit has |
| `file`, and dotkit does not have it | `keep` | the link target, or "a file from an installer" |
| `file` or `go` that you cannot move without sudo | `keep` | the owner |
| `manager` | `keep` | dotkit does not load it; check that nothing points into it before `trash` |

A `pipx` or `uv` package has the mise key `pipx:<package>`, as in
`dotkit migrate`.

The plan line:

```
# Command-line tools: old copies of tools that dotkit has, and files that no package manager knows.
uninstall  brew:jq          /opt/homebrew/Cellar/jq                               # dotkit has jq (from Nix)
uninstall  npm:typescript   ~/.nvm/versions/node/v20.11.0/lib/node_modules/typescript  # nvm v20.11.0; dotkit has npm:typescript
trash                       ~/.local/bin/ruff                                     # a file; dotkit has pipx:ruff (from mise)
keep                        /usr/local/bin/tailscale                              # a file from an installer; owned by root
keep                        ~/.nvm                                                # nvm; dotkit does not load it. Before trash, check that nothing points into it: LaunchAgents, IDE interpreter settings, git hooks
```

The check adds to section 5:

- `uninstall` takes the thing of the scan for that path, and the scan must
  have an uninstall command for it.
- `trash` is for `file`, `go`, and `manager` lines. A package gets
  `uninstall`, so that its package manager forgets it too.
- `adopt`, `replace`, and `add` are for apps only, and `uninstall` is for
  tools only.

The apply adds to section 6: `uninstall` runs the command of the table, with
its output on the terminal. `trash` moves the file or the directory to the
Trash. Before a line runs, tidy checks that its path still exists. Phase 2
writes nothing to the lists.

### 8.2 Installer packages

A `.pkg` installer leaves a receipt: `pkgutil --pkgs` lists the package
IDs, `pkgutil --pkg-info <id>` gives the volume, the install location, and
the version, and `pkgutil --only-files --files <id>` lists the files,
relative to the volume and the location. The third part of the plan has one
line per receipt that is not Apple's (`com.apple.*`).

The scan (`tidy/pkgs.py`, read-only, with the rules of section 7) reads all
receipts once. The phase 1 scan also uses them: an app that a receipt lists
has the source `pkg` and the system parts of that receipt. `pkgutil
--file-info` missed apps that a receipt lists, such as Tailscale.app.

A receipt lists every file of a bundle. Tidy works on **units**: the
outermost bundle (`.app`, `.framework`, `.kext`, `.plugin`, and the other
bundle types of `pkgs.BUNDLES`) that has a file, or the file itself. Tidy
never moves a directory that is not a bundle, so it cannot move
`/Applications` or `/Library/Application Support`. A unit stays when:

- another receipt lists it too (**shared**);
- it is in a system directory: `/System`, `/bin`, `/sbin`, `/usr/bin`,
  `/usr/sbin`, `/usr/lib`, `/usr/libexec`, `/private`, `/opt/homebrew`, or
  `/nix` (**protected**);
- it does not exist.

The other units are the **present** ones. A receipt has **system parts**
when one of its files is in `/Library/LaunchDaemons`,
`/Library/LaunchAgents`, `/Library/Extensions`,
`/Library/PrivilegedHelperTools`, `/Library/SystemExtensions`,
`/Library/DriverExtensions`, `/Library/Audio/Plug-Ins/HAL`,
`/Library/Security`, `/Library/Filesystems`, `/Library/Input Methods`, or
`/System`, or in `Contents/Library/SystemExtensions`,
`Contents/Library/LaunchServices`, or `Contents/Library/LaunchDaemons` of a
bundle, or in a `.kext`, `.dext`, or `.systemextension`. Moving such files
does not unload them; the vendor's uninstaller does that. The paths compare
as they are on the volume of the package.

The default verbs:

| The receipt | Default | Comment |
|---|---|---|
| an app of it has an App Store receipt | not a line | the App Store app has its own line |
| an installed cask names it in `uninstall` `pkgutil` | not a line | `brew uninstall --cask` uses it |
| it has system parts | `keep` | the first system part; `uninstall` is an error |
| no unit exists | `uninstall` | uninstall only forgets the package |
| no present unit, but shared or protected ones | `keep` | uninstall only forgets the package; what stays |
| present units | `keep` | how many units uninstall moves; what stays |

A receipt whose files are all gone is harmless, but it is noise in
`pkgutil --pkgs`; forgetting it changes no file. Google's updater and the
updater of ChatGPT Classic left such receipts on the Mac where this was
written. A live package is `keep` by default, because its app usually has a
line of its own in phase 1.

The plan line has the thing `pkg:<id>` and the path of the first present app,
else the first present unit, else the first unit:

```
# Installer packages (.pkg): uninstall pkg:<id> moves their files to the Trash and makes macOS forget the
# package. It asks for your password. The App Store and Homebrew casks have their own packages.
uninstall  pkg:com.google.Keystone       /Library/Application Support/Google/GoogleUpdater/PkgStaging/GoogleUpdater.app  # version 0; no files of it are left, so uninstall only forgets the package
keep       pkg:com.jabra.directonline    /Applications/Jabra Direct.app      # version 8.3.26701; uninstall moves 1 item to the Trash
keep       pkg:com.tailscale.ipn.macsys  /Applications/Tailscale.app         # version 1.104.2; system parts (io.tailscale.ipn.macsys.tssentineld.plist) that only the vendor's uninstaller removes
```

A package line is keyed by its thing, not by its path, because its path is
often the path of an app line. The check adds to section 5:

- The package is a receipt of the scan, not one of the App Store or of an
  installed cask, and the path is the path of the scan.
- The verb is `uninstall` or `keep`. `uninstall` of a package with system
  parts is an error.
- When `uninstall pkg:` moves a unit, no other line may have `adopt`,
  `replace`, `trash`, or `uninstall` for it.

The apply adds to section 6. For an `uninstall pkg:` line, tidy quits each
app of the present units that runs, then, with `sudo`:

1. moves each present unit that still exists to
   `~/.Trash/<id> <date and time>/<its absolute path>`, with `mv -n`;
2. gives the moved files to you (`chown -R`), so that the Finder can empty
   the Trash;
3. runs `pkgutil --forget <id>`.

When a move fails, tidy stops that line and does not forget the package.
The directories that the receipt lists stay, also when they are empty. The
Finder cannot "Put Back" these units, but their path under the Trash
directory says where they were.

## 9. Tests

- `tests/tidy/mac.py`: a fake Mac for tidy. Apps with an `Info.plist` from
  `plistlib.dump` and an `_MASReceipt`, a small bulk cask file with
  `cask_names.txt`, and `FakeRunner` answers for `brew info`, `pkgutil`,
  `mdls`, `mas`, and `ps`.
- `tests/tidy/test_apps.py`: the matching cases of section 3.4 (Slack and
  `slack@beta`, Telegram, a `target`, 1Password 7, MonitorControl, `zoom`,
  Tailscale, several casks, Google Chrome), the sources of section 3.2
  (SF Symbols, Google Drive, Karabiner, a pkg with a launch daemon), what
  the scan skips, an app that is not yours or runs, and a Mac without the
  bulk file or without Homebrew.
- `tests/tidy/test_plan.py`: the default verbs of section 4.2, the plan text
  and its parse, the errors and notes of section 5, and the version
  comparison.
- `tests/tidy/test_cli.py`: a fake `Actions`: the order of the commands and
  the list edits, `-n` and `-y`, a no to the question, a failed `adopt`, an
  app that does not quit, `Ctrl-C` after one line, an `Info.plist` that
  changes before the apply, a plan with errors, an empty plan, the cleanup
  hint, and the editor.
- `tests/tidy/test_clis.py`: a fake Mac with Homebrew formulae, nvm with
  npm, `~/.npm-global` without npm, pipx, uv, cargo with rustup, Go, files
  and links in `~/.local/bin` and `/usr/local/bin`, nvm, and Oh My Zsh. The
  scan and its uninstall commands, the default verbs of section 8.1, the
  check, and the apply: the commands run in order, and the lists do not
  change.
- `tests/tidy/test_pkgs.py`: a fake Mac whose receipts have the fake root
  as their volume: units, system parts, shared and protected units, a
  stale receipt, the App Store, and an installed cask; the default verbs;
  the check, also a line that changes a file of a package that
  `uninstall pkg:` moves; the commands; and the apply with a fake
  `Actions`.
- **e2e** (`tests/e2e/tidy.sh`, after the migration checks of the CI e2e
  job): it installs a small cask, copies its app aside, uninstalls the
  cask, and puts the copy back, so the app has the version of the cask and
  Homebrew does not know it. Then `dotkit tidy -n` must propose `adopt`, and
  `dotkit tidy -y --plan` adopts Rectangle. A second app, Maccy, gets
  adopted by `dotkit add cask:maccy`, through brew bundle, which passes
  `--adopt`. Both times the bundle must be the same directory (its inode),
  a setting in the preferences of the app must stay, and `nix/casks.txt`
  must have the token. Then phase 2: the CI job installs pycowsay with pipx
  before setup, and the wizard moves it to mise. `dotkit tidy -n` must
  propose `uninstall pipx:pycowsay`, and `trash` for a copy of `jq` in
  `~/.local/bin`. After the plan runs, pipx does not have pycowsay, the
  Trash has `jq`, and the `pycowsay` of mise runs. Then phase 3: it builds
  two packages with `pkgbuild` and installs them with `installer`, and
  deletes the file of the second one. `dotkit tidy -n` must propose `keep`
  for the first and `uninstall` for the second. After `uninstall` of both,
  `pkgutil` knows neither, the file of the first is in its directory in the
  Trash, and owned by you.

## 10. Decisions

1. **No `ignore` verb in phase 1.** `keep` has no memory, so the next run
   proposes the same apps again.
2. **Tidy does not run `dotkit apply`** (section 6), because an apply also
   builds Nix.
3. **Tidy does not set `homebrew.onActivation.cleanup = "uninstall"`.** It
   only says when the setting is an option (section 6).
4. **The plan opens in an editor**, not in the curses UI of the wizard.
5. **Tidy runs `brew install --cask --adopt` itself**, before it changes the
   lists, so that a failure happens outside the activation.
