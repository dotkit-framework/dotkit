"""The commands of dotkit other than add, remove, info, and list (dotkit/pkg): apply, upgrade, clean,
history, rollback, migrate, tidy, and doctor. dotkit/cli.py parses their arguments.

The tools come from three sources, and dotkit uses the same three words everywhere:
  nix   nix/packages.txt (CLIs), nix/flake.lock (the dotkit release, which
        pins Nixpkgs)
  brew  nix/casks.txt, nix/brews.txt, nix/taps.txt, nix/mas.txt (App Store apps)
  mise  config/mise.toml (runtimes and fast-moving CLIs)
The dotkit base has its own lists of each kind. `dotkit add` and `dotkit
remove` change only the lists of the dotfiles repository. apply, upgrade, and
doctor report what the repository repeats of the base (overlap.py).

DOTKIT_DIR selects a dotkit checkout instead of the release in nix/flake.lock,
for work on dotkit itself (core/env.py).
"""
