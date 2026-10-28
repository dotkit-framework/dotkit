"""`dotkit add`, `remove`, `info`, and `list`: put a global tool in the correct place, so everyone who uses
dotkit installs it the same way.

The file names below are in the dotfiles repository. The dotkit base has
lists with the same names; add and remove do not change them.

A bare name goes to the first source that matches (resolve.py):
  1. A Homebrew cask that installs a macOS app -> nix/casks.txt.
  2. Nixpkgs, when its release is not older than mise's -> nix/packages.txt.
  3. The mise registry (no asdf or vfox plugin) -> config/mise.toml.
  4. A Homebrew formula, or a cask with only a command -> nix/brews.txt,
     nix/casks.txt.
The .txt files have one name per line (nix/read-list.nix reads them), so an
edit is a line append or a line delete. nix/mas.txt has an App Store ID and
the name of the app per line (nix/read-mas.nix); `dotkit add mas:ID` gets the
name from mas.
npm and PyPI are not curated: anyone can publish any name. A bare name never
selects them; the plan shows them with the prefix to use after a check.
An app cask and a formula with the same name stop with an error.

The modules: lookups.py asks the sources, declared.py reads the lists and
finds installed commands, and resolve.py selects a source. cli.py has the
commands; it changes the lists and applies them with core/change.py. lib/tests/BEHAVIOR.md has
the behavior of each, row by row.
"""
