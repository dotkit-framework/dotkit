#!/bin/bash
# Checks what the migration wizard of setup carried over from a used Mac: the
# CI runner, with its Homebrew packages and the dotfiles that the e2e job
# adds. CI runs it after tests/e2e/verify.sh. It changes nothing.
# Usage: migrated.sh <directory with the lists from before setup>
# It must work with the macOS Bash 3.2.
set -euo pipefail

before=$1
repo=${DOTFILES:-$HOME/.dotfiles}
report=$repo/MIGRATION.md
login_shell=/run/current-system/sw/bin/zsh
failures=0

pass() { printf 'ok: %s\n' "$*"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}
step() { printf '\n== %s\n' "$*"; }
zsh_login() { "$login_shell" -lic "$1" </dev/null; }
# The names in a list file, without comments.
names() { awk '{ sub(/#.*/, "") } NF { print $1 }' "$1"; }

step 'the report'
if [[ -s $report ]]; then pass "$report exists"; else fail "$report is missing"; fi
# The e2e job writes these lines to ~/.zshrc; see .github/workflows/ci.yml.
# shellcheck disable=SC2016,SC2088 # Literal text of the report.
for want in '~/.zshrc:3 `eval "$(starship init zsh)"`' '~/.zshrc:4-6 `if [[ -n $OLD_ZSHRC ]]; then`' \
  '~/.zshrc:7 `source ~/.old-zsh-plugin.zsh`' '~/.zprofile:1 `eval "$(/opt/homebrew/bin/brew shellenv)"`' \
  '`MIGRATION_TEST_PASSWORD` -> local/zshrc.zsh'; do
  if grep -qF -- "$want" "$report"; then pass "the report has: $want"; else fail "the report does not have: $want"; fi
done

step 'Homebrew lists'
# Each formula and cask from before setup is in the lists of the new
# repository, or the report says that the dotkit base has it.
for kind in formula cask; do
  if [[ $kind == formula ]]; then
    file=$before/brew-leaves-before list=$repo/nix/brews.txt
  else
    file=$before/brew-casks-full-before list=$repo/nix/casks.txt
  fi
  missing=''
  listed=0 base=0
  while IFS= read -r name; do
    [[ -n $name ]] || continue
    if [[ -f $list ]] && names "$list" | grep -qxF -- "$name"; then
      listed=$((listed + 1))
    elif grep -qF -- "- \`$name\`: dotkit has" "$report"; then
      base=$((base + 1))
    else
      missing+=" $name"
    fi
  done <"$file"
  if [[ -z $missing ]]; then
    pass "every $kind from before setup is in the list ($listed) or in the dotkit base ($base)"
  else
    fail "these ${kind}s are not in the list or the report:$missing"
  fi
done
if names "$repo/nix/casks.txt" 2>/dev/null | grep -qx ghostty; then
  fail 'nix/casks.txt repeats ghostty, which the base has'
else
  pass 'nix/casks.txt does not repeat the base casks'
fi

step 'tools that moved to mise'
# The e2e job installs cowsay with npm and pycowsay with pipx before setup.
for spec in npm:cowsay:cowsay pipx:pycowsay:pycowsay; do
  key=${spec%:*} cmd=${spec##*:}
  if grep -qxF "\"$key\" = \"latest\"" "$repo/config/mise.toml"; then
    pass "config/mise.toml has $key"
  else
    fail "config/mise.toml does not have $key"
  fi
  out=$(cd "$HOME" && zsh_login "mise which $cmd") || out=''
  if [[ $out == "$HOME/.local/share/mise/installs/"* ]]; then
    pass "$cmd is from mise: $out"
  else
    fail "$cmd is not from mise: '$out'"
  fi
done

step 'config files'
# The e2e job links ~/.config/starship.toml to ~/old-dotfiles/starship.toml.
old=$HOME/.config/starship.toml.before-dotkit
if [[ -L $old && $(readlink "$old") == "$HOME/old-dotfiles/starship.toml" ]]; then
  pass "setup moved the old link to $old"
else
  fail "$old is not the old link"
fi
if cmp -s "$HOME/.config/starship.toml" "$HOME/old-dotfiles/starship.toml" &&
  [[ $(realpath "$HOME/.config/starship.toml") == "$repo/config/starship.toml" ]]; then
  pass "$HOME/.config/starship.toml is the old file, from the repository"
else
  fail "$HOME/.config/starship.toml is not the old file in $repo/config/starship.toml"
fi

step 'shell lines'
# shellcheck disable=SC2016 # The inner shell expands these.
out=$(zsh_login 'echo "$OLD_ZSHRC/${OLD_BLOCK-unset}/$MIGRATION_TEST_PASSWORD"; alias oldll') || out=''
if [[ $out == $'1/unset/not-a-real-secret-0042\noldll=\'ls -l\'' ]]; then
  pass 'a new zsh has the carried export, alias, and secret, and not the block'
else
  fail "a new zsh: '$out'"
fi
if (cd "$repo" && git ls-files -z --cached --others --exclude-standard | xargs -0 grep -l 'not-a-real-secret') >/dev/null 2>&1; then
  fail 'a file that Git does not ignore has the secret'
else
  pass 'only an ignored file has the secret'
fi
if [[ $(stat -f %Lp "$repo/local/zshrc.zsh" 2>/dev/null) == 600 ]]; then
  pass 'local/zshrc.zsh is private'
else
  fail 'local/zshrc.zsh is missing or not mode 600'
fi

printf '\n'
if ((failures > 0)); then
  printf '%s checks failed.\n' "$failures" >&2
  exit 1
fi
printf 'All checks passed.\n'
