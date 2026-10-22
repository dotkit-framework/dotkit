#!/bin/bash
# Checks a Mac after scripts/setup stopped at "Unexpected files in /etc".
# Setup must have put back the /etc files that it moved. CI runs it, see the
# e2e-etc job in .github/workflows/ci.yml. It must work with the macOS Bash 3.2.
# Usage: etc-restored.sh <exit status of setup> <log of setup> <directory with
# the copies of /etc/zshrc and /etc/bashrc from before setup>
set -euo pipefail

status=$1 log=$2 before=$3
failures=0

pass() { printf 'ok: %s\n' "$*"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}

if [[ $status != 0 ]]; then pass "setup exited with $status"; else fail 'setup succeeded'; fi
if grep -q 'Unexpected files in /etc' "$log"; then
  pass 'nix-darwin stopped at the unexpected files in /etc'
else
  fail 'setup did not stop at the unexpected files in /etc'
fi
if grep -q 'back\. Fix the problem above, then run setup again\.$' "$log"; then
  pass 'setup says that the files are back'
else
  fail 'setup does not say that the files are back'
fi
for name in zshrc bashrc; do
  file=/etc/$name
  if [[ -f $file && ! -L $file ]] && cmp "$before/$name" "$file"; then
    pass "$file is the file from before setup"
  else
    ls -l "$file" || true
    fail "$file is not the file from before setup"
  fi
  if [[ ! -e $file.before-nix-darwin && ! -L $file.before-nix-darwin ]]; then
    pass "$file.before-nix-darwin does not exist"
  else
    fail "$file.before-nix-darwin exists"
  fi
done

printf '\n'
if ((failures > 0)); then
  printf '%s checks failed.\n' "$failures" >&2
  exit 1
fi
printf 'All checks passed.\n'
