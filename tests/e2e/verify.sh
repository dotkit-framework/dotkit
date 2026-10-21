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

step 'coding agents'
# Codex sources ~/.zshrc in a non-interactive login shell; Claude Code sets
# CLAUDECODE. Both must get a plain zsh, without the interactive options.
# shellcheck disable=SC2016 # The inner shell expands these.
out=$("$login_shell" -lc '. ~/.zshrc; f=$(mktemp); echo x >"$f"; echo y >"$f"; cat "$f"; rm -f "$f"; echo a#b' </dev/null 2>&1) || out="$out (exit $?)"
if [[ $out == $'y\na#b' ]]; then
  pass 'a non-interactive zsh that sources ~/.zshrc overwrites files and does not glob #'
else
  fail "a non-interactive zsh that sources ~/.zshrc: '$out'"
fi
out=$(CLAUDECODE=1 "$login_shell" -lic 'setopt; whence -w ls; command -v node' </dev/null 2>&1) || out=''
if ! grep -qE '^(noclobber|extendedglob|correct)$' <<<"$out" && grep -q '^ls: command$' <<<"$out" &&
  grep -q '/mise/installs/node/' <<<"$out"; then
  pass 'CLAUDECODE=1 zsh -lic is plain and has mise'
else
  printf '%s\n' "$out"
  fail 'CLAUDECODE=1 zsh -lic'
fi
for tool in timeout nproc tac shuf numfmt yq tree; do
  if zsh_login "command -v $tool" >/dev/null; then pass "$tool is on PATH"; else fail "$tool is not on PATH"; fi
done
out=$(zsh_login 'timeout 1 sleep 5; echo $?') || out=''
if [[ $out == 124 ]]; then pass 'timeout stops a command'; else fail "timeout 1 sleep 5 exits with '$out'"; fi
ignore_repo=$(mktemp -d)
git init -q "$ignore_repo"
if git -C "$ignore_repo" check-ignore -q --no-index sub/.claude/settings.local.json; then
  pass 'the global Git ignore file has the agent files'
else
  fail 'the global Git ignore file does not ignore .claude/settings.local.json'
fi
rm -rf "$ignore_repo"

step 'PATH'
out=$(zsh_login 'command -v git') || out=''
if [[ $out == "/etc/profiles/per-user/$user/bin/git" ]]; then
  pass "a login shell finds the git of Nix: $out"
else
  fail "a login shell finds git at '$out'"
fi
# A login shell started from a dotkit shell keeps the directories that were
# first in PATH, as for an agent in a direnv or nix develop project.
project=$(mktemp -d)
printf '#!/bin/sh\necho project\n' >"$project/jq"
chmod +x "$project/jq"
for inner in '-lc' '-lic'; do
  out=$(zsh_login "export PATH=$project:\$PATH; $login_shell $inner jq </dev/null" 2>/dev/null) || out=''
  if [[ $out == project ]]; then
    pass "a nested zsh $inner keeps the project jq first"
  else
    fail "a nested zsh $inner runs '$out' for jq, not the project jq"
  fi
done
rm -rf "$project"

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

step 'Homebrew'
# Setup and the applies uninstall nothing that was on the Mac before.
if [[ -n ${BREW_BEFORE-} ]]; then
  for kind in formula cask; do
    if [[ $kind == formula ]]; then file=$BREW_BEFORE/brew-formulae-before; else file=$BREW_BEFORE/brew-casks-before; fi
    missing=$(comm -23 <(sort "$file") <(brew list "--$kind" -1 | sort))
    if [[ -z $missing ]]; then
      pass "every $kind from before setup is still installed ($(wc -l <"$file" | tr -d ' '))"
    else
      fail "setup uninstalled these ${kind}s: $(tr '\n' ' ' <<<"$missing")"
    fi
  done
fi
# dotkit remove uninstalls the formula that it removes.
if zsh_login 'dotkit add -y brew:hello' && grep -qx hello "$repo/nix/brews.txt" &&
  brew list --formula hello >/dev/null 2>&1; then
  pass 'dotkit add -y brew:hello'
else
  fail 'dotkit add -y brew:hello'
fi
if zsh_login 'dotkit remove -y brew:hello' && ! grep -qx hello "$repo/nix/brews.txt" &&
  ! brew list --formula hello >/dev/null 2>&1; then
  pass 'dotkit remove -y brew:hello uninstalls it'
else
  fail 'dotkit remove -y brew:hello'
fi

printf '\n'
if ((failures > 0)); then
  printf '%s checks failed.\n' "$failures" >&2
  exit 1
fi
printf 'All checks passed.\n'
