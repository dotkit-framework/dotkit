# My Mac

A dotfiles repository on top of [dotkit](https://github.com/podkovyrin/dotkit).
dotkit supplies the base: Nix, zsh, Git, Ghostty, herdr, Starship, and the
common tools. This repository adds your own tools and settings.

```sh
dotkit apply        # apply the config: Nix, Homebrew, and mise
dotkit add <tool>   # add a tool, from the source that dotkit add selects
dotkit upgrade      # new releases, also of dotkit
dotkit --help       # all commands. `dk` expands to `dotkit`
```

`dotkit` works from any directory. `./dotkit` does the same, also before the first
apply.

| File | For |
|---|---|
| `nix/packages.txt`, `nix/brews.txt`, `nix/casks.txt` | your tools, after the dotkit lists |
| `nix/taps.txt`, `nix/mas.txt` | Homebrew taps, App Store apps |
| `config/mise.toml` | your runtimes and mise tools |
| `nix/darwin.nix` | macOS settings |
| `nix/home.nix` | your config files (`dotkit.links`) |
| `nix/shell.nix` | the zsh keymap and abbreviations |
| `config/zsh/zshrc` | your zsh lines |

See the dotkit README for how to change a base setting.
