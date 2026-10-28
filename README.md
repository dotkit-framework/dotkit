# dotkit

The shared base of an Apple Silicon Mac setup: nix-darwin, Home Manager, and
Homebrew. Each person has a dotfiles repository on top of it, with their own
tools and settings. dotkit is a flake input of that repository, so an
upgrade is one command and a rollback is one command.

## What the base has

| Part | What |
|---|---|
| Nix | nix-darwin, Home Manager, weekly garbage collection |
| Shell | zsh from Nix with completion, suggestions, syntax colors, history search; fzf, zoxide, Starship, mise, direnv. `ls` is eza; `diff` and `grep` have colors; `dk` expands to `dotkit`, which has zsh completion |
| Tools | `nix/packages.txt`: bat, eza, fd, ripgrep, jq, yq, tree, hyperfine, tealdeer, git, git-lfs, git-secret, gh, gnupg, the Zulu 17 JDK, and more; `timeout`, `nproc`, `tac`, `shuf`, and `numfmt` from GNU coreutils |
| Runtimes | Node.js 24.22.1, Python 3.14.10, and Ruby 3.4.12, pinned so that each Mac has the same versions, and uv, from mise (`config/mise.toml`), as defaults outside projects |
| Apps | Ghostty (`nix/casks.txt`), the JetBrains Mono Nerd Font, herdr (`config/mise.toml`) |
| Config | Git, Ghostty, herdr with its Git status plugin, Starship, mise, npm, pnpm, uv, bun, RubyGems, EditorConfig. New npm, pnpm, bun, PyPI (uv and pip), gem (Bundler), and mise releases wait 7 days |
| macOS | Touch ID for sudo, also in terminal multiplexers |

The base links its files from `config/` into the home directory. See the
links in `nix/home.nix`.

### Coding agents

Codex, Claude Code, Gemini CLI, and Cursor load `~/.zshrc` once and apply
it to each command they run. For them, and for other non-interactive shells,
`config/zsh/zshrc` stops after `mise activate`: no zsh options, no `ls`
function, no aliases, and no `~/.zshrc.local`. Put the variables that
scripts and agents need in `~/.zshenv.local`. A login shell that starts from
another dotkit shell keeps the directories that are first in PATH, so an
agent that runs in a direnv or `nix develop` project uses the tools of the
project, not the global ones. The global Git ignore file
already lists the local files of these agents, so they do not edit it.

## New Mac

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/podkovyrin/dotkit/main/scripts/setup)" setup [<repository-url>]
```

Setup installs the Xcode Command Line Tools, Nix, and Homebrew, writes your
Git name and email to `~/.gitconfig.user`, and applies the configuration.
`<repository-url>` is your dotfiles repository, which setup clones to
`~/.dotfiles` (`DOTFILES` sets another path). It is not the destination.
Without it, setup creates `~/.dotfiles` (also when it is an empty directory) from `templates/default`, with what the migration wizard can
carry over from this Mac (see below). Commit that repository and push it to
your own remote. A file that is in the way of a dotkit link, for example an
old `~/.zshrc`, is renamed to `<name>.before-dotkit`. When nix-darwin stops
at "Unexpected files in /etc", check those files, rename them as its message
says, and run setup again. Setup moves `/etc/zshrc` and `/etc/bashrc` with the
lines of the Nix installer aside before it activates, and moves them back
when the activation fails. `setup --help` lists the options.

Homebrew formulae and apps that are on the Mac before setup stay installed.
dotkit installs what the lists have, and `dotkit remove` uninstalls what it
removes. To make the lists the complete set, so that an apply uninstalls
everything else, set `homebrew.onActivation.cleanup = "uninstall";` in
`nix/darwin.nix`. `dotkit apply -n` then shows what it would uninstall.

### A Mac that you already use

When setup creates the repository, a wizard in the terminal scans the Mac
and asks a few questions. The wizard writes the repository only after
confirmation. `--yes` takes the defaults. The wizard runs with the macOS
Python of the Command Line Tools (3.9 or newer), so it needs no download.
Before the wizard, setup may install the Command Line Tools and Nix. Setup
may also write your Git identity before the wizard. The screens: where the
repository goes, the zsh keymap, what to carry over, the config files that
dotkit also has, other config to copy into the repository, and the plan.

| On the Mac | In the repository, by default |
|---|---|
| Homebrew formulae and casks that you installed | `nix/brews.txt`, `nix/casks.txt`. They stay installed. Tools that the base has are not repeated; the report says how to remove the Homebrew copy |
| Homebrew taps | `nix/taps.txt`, with the clone URL of a tap that is not on GitHub |
| App Store apps, with `mas` | `nix/mas.txt` |
| Global npm, pnpm, bun, pipx, and `uv tool` tools | `config/mise.toml`, as `npm:` and `pipx:` tools. cargo and Go tools are off: mise builds them from source |
| `~/.zshrc`, `~/.zshenv`, `~/.zprofile`, `~/.bash_profile` | Plain exports, aliases, functions, and PATH directories that exist, in `config/zsh/` and `config/bash/`, linked as the `.local` files. Framework, prompt, and version manager lines, tool setup that the base does, and blocks go to the report |
| Ghostty, Git, and mise config | Yours, read after the dotkit file |
| Starship and the other tools that read one file | Yours, instead of the dotkit file |
| `~/.npmrc`, `~/.config/uv/uv.toml` | Yours, read after the dotkit settings. The tokens of `~/.npmrc` go to `~/.secrets` |
| Detected secrets: tokens in shell files, `~/.npmrc`, and Git config | Variables in `~/.secrets`, outside the repository (see below) |
| Other `~/.config` entries, pi (`~/.pi/agent`), version managers | Stay in place, or go to `config/` if you select them. The report lists them, with the mise commands for the versions. An old place of an app's config, such as `~/.config/pi`, is not offered |

The wizard moves the secrets that it detects to `~/.secrets`, and the
repository files refer to them by name:

| Secret | In `~/.secrets` | In the repository |
|---|---|---|
| An assignment in a shell file, such as `export GITHUB_TOKEN=...` | The same line | Nothing |
| A registry token in `~/.npmrc` | `export NPM_TOKEN='...'`, named after the registry | `${NPM_TOKEN}` in `config/npmrc` |
| A Git setting with a token, such as a `url` section with the token in it | `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, and `GIT_CONFIG_VALUE_<n>`, which Git reads | `config/gitconfig` without the setting |

The wizard adds only the names that `~/.secrets` does not have, and it never
changes a line there. A secret in another place, such as an alias, a function, or
another config file, is not carried; the report names it, never its value.

Secret detection is heuristic. It can miss secrets and flag text that is not
secret. This does not guarantee that the generated files contain no secrets.
Review the generated files before you commit or publish the repository.

Newly mapped global tools use mise's `"latest"`, not their installed versions.
`MIGRATION.md` records the previous installed versions that the scan finds.

`MIGRATION.md` in the repository lists what setup carried over, and the next
step for everything else, with file and line. A link of another dotfiles
manager, for example GNU Stow, that is in the way of a dotkit link is renamed
to `<name>.before-dotkit` too, right before the first activation, also a link
of a directory above the file, such as `~/.config/ghostty`. When `~/.config`
itself is a link, setup stops and says what to do. When the first activation
fails, setup moves the links back, and the next run moves them again; when the
wizard chose another path than `~/.dotfiles`, setup prints the command to
continue with it. `setup --dry-run` prints the plan and the report and
changes nothing. It needs only the Command Line Tools. On a Mac with Nix,
`nix run github:podkovyrin/dotkit#migrate -- setup --dry-run` does the same.

Later, `dotkit migrate` finds Homebrew packages and taps, App Store apps, and
global tools that were installed by hand and that the lists do not have, and
adds them. It skips the Homebrew copy of a tool that the lists have from Nix
or mise. cargo and Go tools only with `--all`, because mise builds them from
source.

`dotkit tidy` does the same for apps from other places: a DMG, a `.pkg`
installer, or the App Store. It scans `/Applications` and `~/Applications`,
finds the cask of each app by its name and bundle ID, and writes a plan
with one line per app:

```
adopt    cask:brave-browser  /Applications/Brave Browser.app  # by hand; the cask auto-updates
replace  cask:joplin         /Applications/Joplin.app         # by hand; 3.6.14, the cask has 3.7.21
add      mas:409201541       /Applications/Pages.app          # from the App Store, not in nix/mas.txt
keep                         /Applications/Home VPN.app       # by hand; no cask
```

`adopt` gives the app to the cask in place (`brew install --cask --adopt`),
and `replace` installs the cask over it. `add` adds an app that is from a
cask or the App Store already to the lists. `trash` moves an app to the
Trash, and `keep` changes nothing. Tidy opens the plan in your editor,
checks it, shows the commands, and asks once. Then it adds the casks and
App Store apps to `nix/casks.txt` and `nix/mas.txt`. The app data in
`~/Library` stays: tidy never touches it. Apps from Setapp, JetBrains
Toolbox, and Apple, and apps that you cannot change without sudo, stay as
they are.

The plan also has old copies of command-line tools that dotkit has: a
Homebrew formula of a tool that dotkit has from Nix, or an npm, pipx, uv, or
cargo package of a tool that `config/mise.toml` has (after `dotkit migrate`
moved it there). `uninstall` removes such a copy with its package manager,
for example `npm uninstall -g typescript` with the npm of that nvm node. A
file in `~/.local/bin`, `~/bin`, or `/usr/local/bin` that dotkit has, for
example `mise` from its installer, gets `trash`. Other files, version
managers such as `~/.nvm`, and zsh frameworks are `keep` lines, so that you
see them; change one to `trash` to move it to the Trash.

The last part of the plan has the packages of `.pkg` installers that are not
Apple's. `uninstall pkg:<id>` moves their files to a directory in the Trash
and makes macOS forget the package (`pkgutil --forget`); it asks for your
password. A package whose files are all gone gets `uninstall` by default;
the others are `keep`. Tidy never uninstalls a package with launch daemons,
kernel or system extensions, or privileged helpers: only the vendor's
uninstaller removes those. The packages of the App Store and of Homebrew
casks are not in the plan. `dotkit tidy -n` prints the plan and changes
nothing. `lib/dotkit/migrate/TIDY.md` has the details.

Then use `dotkit`, from any directory; the abbreviation `dk` expands to it.
`dotkit --help` lists the commands. On a terminal the output has colors;
`--no-color`, `NO_COLOR`, or `DOTKIT_NO_COLOR` turns them off. Before the first apply, `./dotkit` in the
dotfiles repository does the same.

## Your dotfiles repository

| File | For |
|---|---|
| `nix/flake.nix` | the dotkit input |
| `nix/user.nix` | your macOS account and the path of the repository |
| `nix/packages.txt`, `nix/brews.txt`, `nix/casks.txt` | your tools, after the base lists. `dotkit add` edits them |
| `nix/taps.txt` | Homebrew taps: `owner/repo`, and the clone URL after it for a tap that is not on GitHub |
| `nix/mas.txt` | App Store apps: the ID, then the name. `dotkit add mas:ID` edits it |
| `config/mise.toml` | your runtimes and mise tools (`~/.config/mise/config.toml`) |
| `nix/darwin.nix` | nix-darwin settings: macOS defaults, fonts |
| `nix/home.nix` | Home Manager settings: your config files (`dotkit.links`), variables |
| `nix/shell.nix` | the zsh keymap, the terminal title, and abbreviations |
| `config/zsh/zshrc` | your zsh lines (`~/.zshrc.local`) |

`dotkit add` does not add a tool that the base has. When your lists also have a
tool of the base, `dotkit remove` removes your copy, and the base keeps the
tool installed. A tool of the base only stays.

## Change a base setting

Most base files read a file of yours after them, so your settings win:

| Base file | Your file |
|---|---|
| `~/.zshenv`, `~/.zprofile`, `~/.zshrc` | `~/.zshenv.local`, `~/.zprofile.local`, `~/.zshrc.local` |
| `~/.bash_profile` | `~/.bash_profile.local` |
| `~/.config/git/config` | `~/.gitconfig` |
| `~/.config/ghostty/config` | `~/.config/ghostty/config.local` |
| `~/.config/mise/conf.d/dotkit.toml` | `~/.config/mise/config.toml` (a tool there replaces the base version) |
| `config/npmrc` of dotkit, the global config of npm (`NPM_CONFIG_GLOBALCONFIG`) | `~/.npmrc` |
| `/etc/uv/uv.toml` | `~/.config/uv/uv.toml` |

`npm config ls` shows the settings of both npm files, and which of yours
replace a base setting. `npm config set -g` would write the base file: set
your settings in `~/.npmrc` instead. Programs that do not get the environment
of the shell or of the GUI apps, such as `env -i`, do not read the npm base
settings.

`~/.bash_profile` gives `bash -l`, for example a script with `#!/bin/bash -l`,
the same PATH and mise as zsh. An interactive bash then reads `~/.bashrc`.

Keep tokens and other secrets out of every repository: put their `export`
lines in `~/.secrets`, readable only by you (`chmod 600 ~/.secrets`). Config
files refer to the variables, for example `${NPM_TOKEN}` in `config/npmrc`.
Write plain `export NAME='value'` lines, because zsh, bash, and `/bin/sh`
read the file:

- The base `~/.zprofile` sources it before `~/.zprofile.local`, and
  `~/.bash_profile` before `~/.bash_profile.local`, so login shells and the
  programs that they start have the variables. `zsh -c` without `-l` does not
  read it.
- GUI apps get them too. A launchd agent reads the exported variables of
  `~/.secrets` at login and when the file changes. An app that is open keeps
  the old values until you restart it, and a variable that you remove stays
  until the next login. To turn it off, set
  `dotkit.guiSecrets.enable = false;` in `nix/darwin.nix`.
- Keep only secrets in the file. Every GUI app gets its variables, so a
  setting such as `NODE_OPTIONS` belongs in `~/.zshenv.local`.

Link your file in `nix/home.nix`:

```nix
dotkit.links.".config/ghostty/config.local" = "config/ghostty.conf";
```

A relative source is a file in your dotfiles repository. These tools read one
file only: Starship, herdr, pnpm, bun, RubyGems, EditorConfig, and the Git
ignore file. To change one of them, make the base file yours:

```sh
dotkit own herdr                  # or a path: dotkit own ~/.config/starship.toml
```

`dotkit own` copies the base file to `config/` of your repository, links it
there instead of the base file, and applies. The tool can then save its own
settings, such as the theme of herdr, and Git shows each change. After a
`dotkit upgrade`, the report and `dotkit doctor` show how your copy differs
from the base file. `dotkit own --release herdr` gives the file back to the
base: it removes your link, and your copy stays in the repository until you
delete it. The base files that a tool reads before a file of yours (zsh,
bash, Git config, Ghostty, mise) cannot be owned: set what you need in your
file instead, so the rest of the base still applies. To remove a base link
without a file of yours:

```nix
dotkit.links.".gemrc" = null; # no link at all
```

zsh settings that need a rebuild are options in `nix/shell.nix`:

```nix
dotkit.zsh = {
  keymap = "vi";      # the default is emacs
  title = "%~";
  abbreviations."g" = "git";
};
```

Other parts of the base are nix-darwin and Home Manager options. Set them in
`nix/darwin.nix` or `nix/home.nix`, with `lib.mkForce` if the base sets them
too. The dotkit options are in `nix/darwin.nix`, `nix/home.nix`,
`nix/links.nix`, and `nix/modules/zsh` of this repository.

A link of yours inside a directory that you or the base link (for example
`~/.pi/agent/extensions/x.ts` when `~/.pi/agent/extensions` is a link) stops
the build: Home Manager would write the file into the source of the
directory.

## Upgrade

`dotkit upgrade` gets the newest dotkit, which pins Nixpkgs, nix-darwin, and
Home Manager, plus new Homebrew and mise releases. It shows one plan and asks
once. Commit `nix/flake.lock` after it. `dotkit rollback` goes back to the
previous Nix generation.

### When the base gets what you have

A new dotkit can add a tool or a config file that your repository has
already. After each apply and upgrade, dotkit reports what your repository
repeats or replaces of the base:

| Overlap | What to do |
|---|---|
| A tool in your list and in the base list | `dotkit remove` it: the base keeps it |
| A mise tool with the same value | `dotkit remove` it |
| A mise tool with another value | Nothing, if you want your version: yours wins |
| Your link that replaces a base file, with the same content | `dotkit own --release` it |
| Your link that replaces a base file (`dotkit own`), with other content | The report shows the diff when the files are similar. For a file that the tool reads after the base file (`~/.gitconfig`, `~/.zshrc.local`, ...), move your changes there and remove your link. Else keep yours, or take the base file |
| `~/<file>.before-dotkit` | An apply moved your file aside to link the base file. Take what you need from it, then delete it |

The report shows an overlap once, and again when one of the two sides
changes. `dotkit doctor` lists all of them. `dotkit apply -n` and
`dotkit upgrade -n` show them before the change, also the files that the
apply will move aside.

## Security

`dotkit audit` asks [OSV.dev](https://osv.dev) about the tools that dotkit
installs, and prints their known vulnerabilities and malicious releases:

```
$ dotkit audit
fzf 0.74.5 (Nix):
  CVE-2026-115319 (unknown): fzf 0.36.0 through 0.74.5 Denial of Service via maskActionContents
    https://osv.dev/vulnerability/CVE-2026-115319
```

| Tools | How |
|---|---|
| Nix packages | One `nix eval` of the configuration gives the source repository and the tag or commit of each package. OSV has no Nix ecosystem, so the audit asks about the repository at that tag. It leaves out a CVE that a Nixpkgs patch of the package fixes |
| Homebrew formulae | `brew vulns` (Homebrew 6.0.11 or newer), which asks OSV and knows the patches of Homebrew |
| mise tools | The installed version, else the version that an apply installs now. For npm and PyPI tools, OSV also lists the malicious releases (`MAL-*`) |
| dotkit | The Python packages of `lib/dotkit/_vendor`, and the inputs of `flake.lock` |

The report also names what it does not check: a package whose source is
not in a Git repository that OSV knows (for example a tarball from gnu.org),
Ruby, and the Homebrew casks, because OSV has no data for apps. A run asks
OSV once, with a few kilobytes, and reads only the advisories that are new
or changed. A run takes a few seconds.

An advisory that does not apply to you, for example one for Windows only,
goes in `audit.toml` of your repository. dotkit has its own `audit.toml`
for the base.

```toml
[[ignore]]
id = "CVE-2024-3220"
tool = "python"      # optional: only for this tool
reason = "Only on Windows."
until = 2027-01-01   # optional: then the finding comes back
```

The audit finds what is known. The 7-day wait for new npm, pnpm, bun, PyPI,
gem, and mise releases (see What the base has) is the other half: a
registry often removes a malicious release in that time. The runtimes
are pinned: when the audit reports an advisory, set the fixed release in
`config/mise.toml`.

### Each day, on GitHub

`.github/workflows/audit.yml` of the template runs `dotkit audit --issues`
each day on a macOS runner, with the dotkit of `nix/flake.lock`. It opens
one issue per finding, with the label `dotkit-audit`, so GitHub notifies you.
It closes the issue when the finding is gone. When a part of the audit
fails, the workflow fails, and GitHub sends an email. In a public
repository, GitHub turns the schedule off after 60 days without a commit:
run the workflow from the Actions tab to turn it on again. A repository
from an older template does not have the workflow: copy
`templates/default/.github` of dotkit into it.

The dotkit repository runs the same audit for the base each day, with the
template as the dotfiles repository.

### Each day, on the Mac

In `nix/darwin.nix`:

```nix
dotkit.audit.enable = true;   # at 10:00; dotkit.audit.hour sets another hour
```

A launchd agent then runs `dotkit audit --notify` each day. It shows a
macOS notification when there is a new finding, and nothing else. When the
Mac sleeps at that hour, the audit runs when it wakes. The log is
`~/Library/Logs/dotkit-audit.log`. macOS shows the notification as one of
Script Editor; when none shows, allow the notifications of Script Editor in
System Settings.

## Work on dotkit

Clone dotkit and set `DOTKIT_DIR` to the checkout:

```sh
git clone git@github.com:podkovyrin/dotkit.git ~/.dotkit
export DOTKIT_DIR=~/.dotkit   # for example in config/zsh/zprofile
```

With `DOTKIT_DIR`, `dotkit` builds with the checkout instead of the release in
`nix/flake.lock` (`--override-input`). Nix reads the files that Git tracks,
also their uncommitted changes, so `git add` a new file first. `dotkit upgrade
nix` then also updates `flake.lock` of the checkout (Nixpkgs and the other
inputs).

To edit the base config files without a rebuild, also link them to the
checkout, in `nix/home.nix`:

```nix
dotkit.liveDir = "${user.home}/.dotkit";
```

To add a config file to the base, add it to `dotkit.baseLinks` in
`nix/home.nix`. When its tool reads a file of yours after it, add that file
to `dotkit.layers` too, and to `LINKS` in `lib/dotkit/migrate/base.py`, so
that setup carries a file of a new user to the layer (`lib/tests/test_base.py`
compares them). Do not link a file that its tool writes, and do not link a
file inside a directory that users link. The report of apply then tells the
users who have the tool or the file already.

Check the base and a Mac from the template:

```sh
nix flake check                                     # evaluates
nix build .#checks.aarch64-darwin.example           # builds the example Mac
```

The `dotkit` command and the migration wizard are Python, in `lib/dotkit/`,
and run with the macOS Python of the Xcode Command Line Tools. The command
is in `commands/` (apply, upgrade, and the others), `pkg/` (add, remove,
info, list), and `core/` (what they share); `lib/ARCHITECTURE.md` describes
them, and `lib/tests/BEHAVIOR.md` lists the behavior, row by row, with the
tests that cover each row. The wizard is in `lib/dotkit/migrate/`; its
`ARCHITECTURE.md` describes its parts, the
rules for secrets, writes, and errors, and how to add a migration; the
docstring of `lib/dotkit/migrate/__init__.py` has the short version and the
Python 3.9 rules. Run the tests with the macOS Python:

```sh
cd lib && /usr/bin/python3 -B -m unittest discover -s tests -t .
```

To release a change, commit it and push it. Each dotfiles repository gets
it with `dotkit upgrade dotkit`.

## CI

`.github/workflows/ci.yml` runs once for each pull request, on each push to
`main`, and on request (workflow_dispatch), on macOS 26 arm64 runners. The
jobs run in parallel. The `check` job runs shellcheck, nixfmt, statix,
deadnix, actionlint, and zizmor (the security checks of the workflows, also
of the template) first, then `nix flake check`, which builds the
example Mac and runs the Python tests with the Python of Nixpkgs, then
`tests/setup.sh` (functions of setup in a temporary home directory). The
`python` job runs the Python tests of the `dotkit` command and the migration
wizard (`lib/tests`) with the macOS Python, and the tests that skip in the
Nix sandbox (the scenarios, and the tests that need `nix`) with the Python of
Nixpkgs. Each action of the workflows is pinned to the commit of its
release, with the tag in a comment; Dependabot updates the actions and the
inputs of `flake.lock` (`.github/dependabot.yml`), after a wait of 7 days.
`.github/workflows/audit.yml` runs `dotkit audit` of the base each day (see
Security). The `e2e` job runs `scripts/setup --yes` on a
fresh macOS 26 runner with `DOTKIT_DIR` set to the commit, so setup creates
`~/.dotfiles` with the wizard and builds it with that commit. The runner is a
used Mac: it has many Homebrew packages, and the job adds old dotfiles. It
applies again, then `tests/e2e/verify.sh` checks the result: the login shell, the
`dotkit` command, zsh, `bash -l`, the links, mise, and `dotkit add` and `remove`.
`tests/e2e/migrated.sh` checks the new lists, the carried shell lines, and
`MIGRATION.md`. `tests/e2e/tidy.sh` makes two apps that look installed by hand, then
checks that `dotkit tidy` adopts one and that `dotkit add` adopts the
other, with the same bundle and the same app data. Then tidy uninstalls the
pipx copy of pycowsay, which mise has after setup, moves a copy of `jq` in
`~/.local/bin` to the Trash, and uninstalls two packages that it builds with
`pkgbuild`.
The `e2e-etc` job, on macOS 26 only, installs Nix first and keeps the changed
`/etc/shells` of the runner, so the first activation fails. It checks that
setup moved `/etc/zshrc` and `/etc/bashrc` back, then moves `/etc/shells`
aside and runs setup again. Before that, it runs `setup --dry-run`.
