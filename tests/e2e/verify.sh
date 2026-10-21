#!/bin/bash
# Checks a Mac after scripts/setup and a second dotkit apply. CI runs it on a
# fresh runner, see .github/workflows/ci.yml. It changes the Mac: it adds a
# tool with dotkit and removes it again. Do not run it on your own Mac.
# It must work with the macOS Bash 3.2.
set -euo pipefail

repo=${DOTFILES:-$HOME/.dotfiles}
user=$(id -un)
login_shell=/run/current-system/sw/bin/zsh
failures=0

pass() { printf 'ok: %s\n' "$*"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}
# Prints a heading, so the log shows which check printed what.
step() { printf '\n== %s\n' "$*"; }

# Runs $1 in a new login shell, as a new terminal does. Without a terminal,
# so zle is off.
zsh_login() { "$login_shell" -lic "$1" </dev/null; }

step 'activation'
if [[ -e /run/current-system && $(readlink /run/current-system) == "$(readlink "$repo/.build/system")" ]]; then
  pass "/run/current-system is the last build of $repo"
else
  fail "/run/current-system is not $repo/.build/system"
fi

step 'login shell'
shell=$(dscl . -read "/Users/$user" UserShell | sed 's/^UserShell: //')
if [[ $shell == "$login_shell" && -x $shell ]]; then
  pass "the login shell is $shell"
else
  fail "the login shell is $shell, expected $login_shell"
fi
if grep -qx "$login_shell" /etc/shells; then
  pass "/etc/shells has $login_shell"
else
  fail "/etc/shells does not have $login_shell"
fi

step 'dotkit command'
out=$(zsh_login 'command -v dotkit') || out=''
if [[ $out == "/etc/profiles/per-user/$user/bin/dotkit" ]]; then
  pass "dotkit is $out"
else
  fail "dotkit is not on PATH of a login shell: '$out'"
fi
if zsh_login 'dotkit --help' >/dev/null; then pass 'dotkit --help'; else fail 'dotkit --help'; fi
if out=$(zsh_login 'dotkit list') && grep -q '^nix   ripgrep (dotkit)$' <<<"$out"; then
  pass 'dotkit list has the base tools'
else
  printf '%s\n' "$out"
  fail 'dotkit list'
fi
if zsh_login 'dotkit apply -n'; then pass 'dotkit apply -n'; else fail 'dotkit apply -n'; fi
out=$(zsh_login 'dotkit __complete tools-all') || out=''
for tool in ripgrep mise ghostty node; do
  if grep -qx "$tool" <<<"$out"; then
    pass "dotkit __complete tools-all has $tool"
  else
    fail "dotkit __complete tools-all does not have $tool"
  fi
done

step 'interactive zsh'
errors=$(mktemp)
zsh_login 'exit' 2>"$errors" >/dev/null || fail 'zsh -lic exit failed'
if [[ -s $errors ]]; then
  cat "$errors"
  fail 'an interactive zsh printed errors'
else
  pass 'an interactive zsh starts without errors'
fi
# The same in a terminal, with zle and the first prompt. script(1) gives zsh a
# terminal; stderr goes to the file. zsh reads the input after the first
# prompt, and perl stops it after 120 seconds.
: >"$errors"
printf -v redirect 'exec 2>%q' "$errors"
if { sleep 5; printf 'exit\n'; } | TERM=xterm-256color perl -e 'alarm 120; exec @ARGV' \
  script -q /dev/null "$login_shell" -c "$redirect; exec $login_shell -li" >/dev/null; then
  if [[ -s $errors ]]; then
    cat "$errors"
    fail 'an interactive zsh in a terminal printed errors'
  else
    pass 'an interactive zsh in a terminal starts without errors'
  fi
else
  fail 'an interactive zsh in a terminal did not exit'
fi
rm -f "$errors"
out=$(zsh_login 'abbr expand dk') || out=''
if [[ $out == dotkit ]]; then pass 'dk expands to dotkit'; else fail "dk expands to '$out'"; fi

step 'links'
for target in .zshenv .zprofile .zshrc .zshrc.local .config/git/config .config/ghostty/config \
  .config/mise/conf.d/dotkit.toml .config/mise/config.toml .config/starship.toml \
  .config/herdr/config.toml .local/share/dotkit; do
  if [[ -L $HOME/$target && -e $HOME/$target ]]; then
    pass "$HOME/$target -> $(realpath "$HOME/$target")"
  else
    fail "$HOME/$target is not a working link"
  fi
done
out=$(zsh_login 'git config --get init.defaultBranch') || out=''
if [[ $out == main ]]; then pass 'git init.defaultBranch is main'; else fail "git init.defaultBranch is '$out'"; fi

step 'mise'
for spec in 'node:v24.' 'python:Python 3.14.'; do
  tool=${spec%%:*} want=${spec#*:}
  out=$(cd "$HOME" && zsh_login "$tool --version") || out=''
  if [[ $out == "$want"* && $(cd "$HOME" && zsh_login "mise which $tool") == "$HOME/.local/share/mise/installs/"* ]]; then
    pass "$tool from mise: $out"
  else
    fail "$tool is not from mise: '$out'"
  fi
done

step 'dotkit add and remove'
if zsh_login 'dotkit add -y nix:hello' && grep -qx hello "$repo/nix/packages.txt" &&
  [[ $(zsh_login 'hello') == 'Hello, world!' ]]; then
  pass 'dotkit add -y nix:hello'
else
  fail 'dotkit add -y nix:hello'
fi
if zsh_login 'dotkit remove -y nix:hello' && ! grep -qx hello "$repo/nix/packages.txt" &&
  ! zsh_login 'command -v hello' >/dev/null; then
  pass 'dotkit remove -y nix:hello'
else
  fail 'dotkit remove -y nix:hello'
fi

printf '\n'
if ((failures > 0)); then
  printf '%s checks failed.\n' "$failures" >&2
  exit 1
fi
printf 'All checks passed.\n'
