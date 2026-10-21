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
| Runtimes | Node.js 24, Python 3.14, Ruby 3.4, and uv from mise (`config/mise.toml`), as defaults outside projects |
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
`<repository-url>` is your dotfiles repository. Without it, setup creates
`~/.dotfiles` from `templates/default`. Commit that repository and push it to
your own remote. A file that is in the way of a dotkit link, for example an
old `~/.zshrc`, is renamed to `<name>.before-dotkit`. When nix-darwin stops
at "Unexpected files in /etc", check those files, rename them as its message
says, and run setup again. `setup --help` lists the options.

Homebrew formulae and apps that are on the Mac before setup stay installed.
dotkit installs what the lists have, and `dotkit remove` uninstalls what it
removes. To make the lists the complete set, so that an apply uninstalls
everything else, set `homebrew.onActivation.cleanup = "uninstall";` in
`nix/darwin.nix`. `dotkit apply -n` then shows what it would uninstall.

Then use `dotkit`, from any directory; the abbreviation `dk` expands to it.
`dotkit --help` lists the commands. Before the first apply, `./dotkit` in the
dotfiles repository does the same.

## Your dotfiles repository

| File | For |
|---|---|
| `nix/flake.nix` | the dotkit input |
| `nix/user.nix` | your macOS account and the path of the repository |
| `nix/packages.txt`, `nix/brews.txt`, `nix/casks.txt` | your tools, after the base lists. `dotkit add` edits them |
| `config/mise.toml` | your runtimes and mise tools (`~/.config/mise/config.toml`) |
| `nix/darwin.nix` | nix-darwin settings: macOS defaults, App Store apps, fonts |
| `nix/home.nix` | Home Manager settings: your config files (`dotkit.links`), variables |
| `nix/shell.nix` | the zsh keymap, the terminal title, and abbreviations |
| `config/zsh/zshrc` | your zsh lines (`~/.zshrc.local`) |

`dotkit add` does not add a tool that the base has, and `dotkit remove` does not
remove one.

## Change a base setting

Most base files read a file of yours after them, so your settings win:

| Base file | Your file |
|---|---|
| `~/.zshenv`, `~/.zprofile`, `~/.zshrc` | `~/.zshenv.local`, `~/.zprofile.local`, `~/.zshrc.local` |
| `~/.config/git/config` | `~/.gitconfig` |
| `~/.config/ghostty/config` | `~/.config/ghostty/config.local` |
| `~/.config/mise/conf.d/dotkit.toml` | `~/.config/mise/config.toml` (a tool there replaces the base version) |

Link your file in `nix/home.nix`:

```nix
dotkit.links.".config/ghostty/config.local" = "config/ghostty.conf";
```

A relative source is a file in your dotfiles repository. These tools read one
file only: Starship, herdr, npm, pnpm, uv, bun, RubyGems, and EditorConfig.
To change one of them, copy the base file to your repository and link the
same target. Your link replaces the base link:

```nix
dotkit.links.".config/herdr/config.toml" = "config/herdr.toml";
dotkit.links.".npmrc" = null; # no link at all
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

## Upgrade

`dotkit upgrade` gets the newest dotkit, which pins Nixpkgs, nix-darwin, and
Home Manager, plus new Homebrew and mise releases. It shows one plan and asks
once. Commit `nix/flake.lock` after it. `dotkit rollback` goes back to the
previous Nix generation.

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

Check the base and a Mac from the template:

```sh
nix flake check                                     # evaluates
nix build .#checks.aarch64-darwin.example           # builds the example Mac
```

To release a change, commit it and push it. Each dotfiles repository gets
it with `dotkit upgrade dotkit`.

## CI

`.github/workflows/ci.yml` runs on each push and pull request, on macOS
arm64 runners. The `check` job runs the two commands above, shellcheck,
nixfmt, statix, and deadnix. The `e2e` job runs `scripts/setup --yes` on a
fresh macOS 15 and macOS 26 runner with `DOTKIT_DIR` set to the commit, so setup creates
`~/.dotfiles` from the template and builds it with that commit. It applies
again, then `tests/e2e/verify.sh` checks the result: the login shell, the
`dotkit` command, zsh, the links, mise, and `dotkit add` and `remove`.
