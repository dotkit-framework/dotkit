#!/bin/bash
# Checks functions of scripts/setup in a temporary home directory: the links
# in the way, the exit trap, the repository of an earlier run, and the
# Homebrew prefix. It sources setup with DOTKIT_SETUP_TEST=1, which defines
# the functions and runs nothing. No sudo, no network; it changes nothing
# outside its temporary directory. CI runs it in the check job. It must work
# with the macOS Bash 3.2.
set -euo pipefail

here=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export HOME=$tmp/home
mkdir -p "$HOME/.config"
unset DOTFILES

# shellcheck source=scripts/setup
DOTKIT_SETUP_TEST=1 source "$here/scripts/setup"
set +e

# setup has its own fail, which exits.
failures=0
pass() { printf 'ok: %s\n' "$*"; }
problem() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}
dotkit=$here

# A folded GNU Stow directory and a linked file, from the wizard's list.
mkdir -p "$HOME/stow/ghostty" "$tmp/build"
echo 'font-size = 16' >"$HOME/stow/ghostty/config"
ln -s "$HOME/stow/ghostty" "$HOME/.config/ghostty"
echo 'add_newline = false' >"$HOME/stow/starship.toml"
ln -s "$HOME/stow/starship.toml" "$HOME/.config/starship.toml"
printf '%s\n' .config/ghostty .config/starship.toml .config/missing >"$tmp/build/links-in-way"
build=$tmp/build

move_links_in_way 2>/dev/null
if [[ ! -e $HOME/.config/ghostty && -L $HOME/.config/ghostty.before-dotkit &&
  -L $HOME/.config/starship.toml.before-dotkit && -f $HOME/stow/ghostty/config ]]; then
  pass 'move_links_in_way moves the links aside, the files they point to stay'
else
  problem 'move_links_in_way'
fi

# An exit before the activation, also Ctrl-C at a sudo prompt, moves them back.
activated='' moved=()
(exit 130)
on_exit 2>/dev/null
if [[ -L $HOME/.config/ghostty && -L $HOME/.config/starship.toml && ! -e $HOME/.config/ghostty.before-dotkit ]]; then
  pass 'the exit trap moves the links back before the activation'
else
  problem 'the exit trap did not move the links back'
fi

# The next run reads the list again and moves them again.
moved_links=()
move_links_in_way 2>/dev/null
if [[ -L $HOME/.config/ghostty.before-dotkit && ${#moved_links[@]} == 2 ]]; then
  pass 'a second run moves the links again'
else
  problem 'a second run did not move the links'
fi
activated=1
on_exit 2>/dev/null
if [[ -L $HOME/.config/ghostty.before-dotkit ]]; then
  pass 'after the activation, the exit trap leaves the links moved'
else
  problem 'after the activation, the exit trap moved the links back'
fi

# A repository that the wizard created at another path: the hint after a
# failure, and the next run finds it.
other=$HOME/code/dots
mkdir -p "$other/nix"
touch "$other/nix/flake.nix"
default_repo=$HOME/.dotfiles repo=$other yes=1
out=$( (exit 1); on_exit 2>&1)
if [[ $out == *"DOTFILES=$other $here/scripts/setup --yes"* ]]; then
  pass 'a failure shows the command to continue with the other repository'
else
  problem "the hint after a failure: '$out'"
fi
out=$( (exit 0); on_exit 2>&1)
if [[ -z $out ]]; then pass 'no hint after success'; else problem "a hint after success: '$out'"; fi
repo=$HOME/.dotfiles url=''
mkdir -p "${created%/*}"
printf '%s\n' "$other" >"$created"
find_created_repo 2>/dev/null
if [[ $repo == "$other" && $build == "$other/.build" ]]; then
  pass 'a later run uses the repository of the earlier run'
else
  problem "find_created_repo: repo is '$repo'"
fi
repo=$HOME/.dotfiles
DOTFILES=$HOME/elsewhere find_created_repo 2>/dev/null
if [[ $repo == "$HOME/.dotfiles" ]]; then pass 'DOTFILES wins over the earlier run'; else problem 'DOTFILES lost'; fi

# An empty directory at the repository path is a new repository.
mkdir -p "$HOME/.dotfiles"
printf '%s\n' "$other" >"$created"
repo=$HOME/.dotfiles
find_created_repo 2>/dev/null
if [[ $repo == "$other" ]]; then
  pass 'an empty ~/.dotfiles does not hide the repository of the earlier run'
else
  problem "find_created_repo with an empty ~/.dotfiles: repo is '$repo'"
fi
repo=$HOME/.dotfiles
if is_absent "$repo" && ! is_absent "$other"; then
  pass 'is_absent accepts an empty directory only'
else
  problem 'is_absent'
fi

# A local path that is not a Git repository, for example the destination,
# stops setup before it installs anything.
for arg in "$HOME/.dotfiles" "$HOME/missing"; do
  out=$(url=$arg check_url 2>&1) && status=0 || status=$?
  if ((status != 0)) && [[ $out == *'run setup without it'* ]]; then
    pass "check_url stops at $arg"
  else
    problem "check_url $arg: status $status, '$out'"
  fi
done
git init --quiet "$tmp/remote"
for arg in "$tmp/remote" https://github.com/u/dots.git git@github.com:u/dots.git; do
  if (url=$arg check_url 2>/dev/null); then
    pass "check_url accepts $arg"
  else
    problem "check_url rejects $arg"
  fi
done
main=$(sed -n '/^check_url$/=; /^ensure_git_user$/=' "$here/scripts/setup" | paste -sd ' ' -)
if [[ $main =~ ^([0-9]+)\ ([0-9]+)$ ]] && ((BASH_REMATCH[1] < BASH_REMATCH[2])); then
  pass 'setup checks the repository URL before it asks questions'
else
  problem "the order of check_url and ensure_git_user: '$main'"
fi
rmdir "$HOME/.dotfiles"

# Homebrew in another prefix stops setup before it installs anything.
homebrew=$tmp/none/brew intel_homebrew=$tmp/intel/brew
mkdir -p "$tmp/intel"
printf '#!/bin/sh\n' >"$intel_homebrew"
chmod +x "$intel_homebrew"
out=$(PATH=/usr/bin:/bin check_brew_prefix 2>&1) && status=0 || status=$?
if ((status != 0)) && [[ $out == *"Existing executable: $intel_homebrew"* ]]; then
  pass 'check_brew_prefix stops at an Intel Homebrew'
else
  problem "check_brew_prefix: status $status, '$out'"
fi
main=$(sed -n '/^check_brew_prefix$/=; /^ensure_nix$/=' "$here/scripts/setup" | paste -sd ' ' -)
if [[ $main =~ ^([0-9]+)\ ([0-9]+)$ ]] && ((BASH_REMATCH[1] < BASH_REMATCH[2])); then
  pass 'setup checks the Homebrew prefix before it installs Nix'
else
  problem "the order of check_brew_prefix and ensure_nix: '$main'"
fi

printf '\n'
if ((failures > 0)); then
  printf '%s checks failed.\n' "$failures" >&2
  exit 1
fi
printf 'All checks passed.\n'
