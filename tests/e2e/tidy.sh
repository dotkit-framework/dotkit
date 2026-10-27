#!/bin/bash
# tests/e2e/tidy.sh: dotkit tidy and the adopt of brew bundle on the CI runner,
# after setup. migrate/TIDY.md, section 9.
#
# For each of two small casks, it makes an app that is installed "by hand": it
# installs the cask, copies the app aside, uninstalls the cask, and puts the
# copy back. The app then has the version of the cask, and Homebrew does not
# know it. Then:
#   1. dotkit tidy adopts the first app: brew install --cask --adopt.
#   2. dotkit add cask:<token> adopts the second app through brew bundle,
#      which passes --adopt.
# Both times the bundle must stay the same directory (its inode), and a
# setting in the preferences of the app must stay.
#   3. Command-line tools (TIDY.md, section 8.1): the CI job installs
#      pycowsay with pipx before setup, and the wizard moves it to mise, so
#      the pipx copy is an old copy. A copy of jq in ~/.local/bin is a file
#      that dotkit has from Nix. tidy uninstalls the first and moves the
#      second to the Trash.
#   4. Installer packages (TIDY.md, section 8.2): two packages from
#      pkgbuild. The file of the second one is deleted, so its receipt is
#      stale. tidy proposes keep and uninstall, and uninstalls both: the
#      file of the first goes to a directory in the Trash, and pkgutil
#      forgets both packages.
set -euo pipefail

fail() { printf 'tidy.sh: %s\n' "$*" >&2; exit 1; }

export HOMEBREW_NO_AUTO_UPDATE=1
dotkit=(/run/current-system/sw/bin/zsh -lc)

# install_by_hand <token> <app>: the app of the cask, without the cask.
install_by_hand() {
  local token=$1 app=/Applications/$2 copy
  ! brew list --cask "$token" >/dev/null 2>&1 || fail "$token is installed already."
  brew install --cask "$token"
  copy=$(mktemp -d)/$2
  ditto "$app" "$copy"
  brew uninstall --cask "$token"
  [[ ! -e $app ]] || fail "brew uninstall left $app."
  ditto "$copy" "$app"
  rm -rf "$(dirname "$copy")"
  ! brew list --cask "$token" >/dev/null 2>&1 || fail "Homebrew still has $token."
}

# remember <app>: the inode of the bundle, after a setting in the app data (its preferences).
remember() {
  local app=/Applications/$1 id
  id=$(defaults read "$app/Contents/Info" CFBundleIdentifier)
  defaults write "$id" dotkitTidyE2E -string kept
  stat -f %i "$app"
}

# check_adopted <token> <app> <inode>
check_adopted() {
  local token=$1 app=/Applications/$2 inode=$3 id
  brew list --cask "$token" >/dev/null || fail "Homebrew does not have $token."
  [[ $(stat -f %i "$app") == "$inode" ]] || fail "$app is not the same directory."
  grep -qx "$token" ~/.dotfiles/nix/casks.txt || fail "nix/casks.txt does not have $token."
  id=$(defaults read "$app/Contents/Info" CFBundleIdentifier)
  [[ $(defaults read "$id" dotkitTidyE2E) == kept ]] || fail "the app data of $app changed."
}

# 1. dotkit tidy, with a plan file. Rectangle is a cask with auto_updates.
install_by_hand rectangle Rectangle.app
inode=$(remember Rectangle.app)
out=$("${dotkit[@]}" 'dotkit tidy -n')
printf '%s\n' "$out"
grep -Eq '^adopt +cask:rectangle +/Applications/Rectangle\.app' <<<"$out" || fail 'the plan does not adopt Rectangle.'
plan=$(mktemp)
echo 'adopt cask:rectangle /Applications/Rectangle.app' >"$plan"
"${dotkit[@]}" "dotkit tidy -y --plan '$plan'"
rm -f "$plan"
check_adopted rectangle Rectangle.app "$inode"
grep -qx '# Added by dotkit tidy on .*\.' ~/.dotfiles/nix/casks.txt || fail 'nix/casks.txt has no tidy comment.'

# 2. brew bundle adopts an app that the list has.
install_by_hand maccy Maccy.app
inode=$(remember Maccy.app)
"${dotkit[@]}" 'dotkit add -y cask:maccy'
check_adopted maccy Maccy.app "$inode"

# 3. Command-line tools.
grep -q '"pipx:pycowsay"' ~/.dotfiles/config/mise.toml || fail 'config/mise.toml does not have pipx:pycowsay.'
pipx list --short | grep -q '^pycowsay ' || fail 'pipx does not have pycowsay.'
mkdir -p ~/.local/bin
cp "$(/run/current-system/sw/bin/zsh -lc 'command -v jq')" ~/.local/bin/jq
out=$("${dotkit[@]}" 'dotkit tidy -n')
printf '%s\n' "$out"
grep -Eq '^uninstall +pipx:pycowsay +' <<<"$out" || fail 'the plan does not uninstall pipx:pycowsay.'
grep -Eq '^trash +~/\.local/bin/jq +# a file from an installer; dotkit has jq' <<<"$out" ||
  fail 'the plan does not move ~/.local/bin/jq to the Trash.'
plan=$(mktemp)
grep -E '^(uninstall +pipx:pycowsay|trash +~/\.local/bin/jq) ' <<<"$out" >"$plan"
"${dotkit[@]}" "dotkit tidy -y --plan '$plan'"
rm -f "$plan"
! pipx list --short | grep -q '^pycowsay ' || fail 'pipx still has pycowsay.'
[[ ! -e ~/.local/bin/jq ]] || fail "$HOME/.local/bin/jq is still there."
[[ -n $(find ~/.Trash -maxdepth 1 -name 'jq*') ]] || fail 'the Trash does not have jq.'
# mise has pycowsay still.
/run/current-system/sw/bin/zsh -lc 'pycowsay hi' >/dev/null || fail 'pycowsay of mise does not run.'

# 4. Installer packages.
pkgroot=$(mktemp -d)
for n in one two; do
  mkdir -p "$pkgroot/$n/usr/local/share/dotkit-tidy-e2e-$n"
  echo "$n" >"$pkgroot/$n/usr/local/share/dotkit-tidy-e2e-$n/$n.txt"
  pkgbuild --root "$pkgroot/$n" --identifier "dev.dotkit.e2e.$n" --version 1.0 --install-location / \
    "$pkgroot/$n.pkg"
  sudo installer -pkg "$pkgroot/$n.pkg" -target /
done
rm -rf "$pkgroot"
one=/usr/local/share/dotkit-tidy-e2e-one/one.txt
two=/usr/local/share/dotkit-tidy-e2e-two/two.txt
sudo rm "$two"
out=$("${dotkit[@]}" 'dotkit tidy -n')
printf '%s\n' "$out"
grep -Eq "^keep +pkg:dev\.dotkit\.e2e\.one +$one +# version 1\.0; uninstall moves 1 item to the Trash" <<<"$out" ||
  fail 'the plan does not keep dev.dotkit.e2e.one.'
grep -Eq "^uninstall +pkg:dev\.dotkit\.e2e\.two +$two +# version 1\.0; no files of it are left" <<<"$out" ||
  fail 'the plan does not uninstall dev.dotkit.e2e.two.'
plan=$(mktemp)
printf '%s\n' "uninstall pkg:dev.dotkit.e2e.one $one" "uninstall pkg:dev.dotkit.e2e.two $two" >"$plan"
"${dotkit[@]}" "dotkit tidy -y --plan '$plan'"
rm -f "$plan"
for n in one two; do
  ! pkgutil --pkg-info "dev.dotkit.e2e.$n" >/dev/null 2>&1 || fail "pkgutil still knows dev.dotkit.e2e.$n."
done
[[ ! -e $one ]] || fail "$one is still there."
moved=$(find ~/.Trash -maxdepth 1 -name 'dev.dotkit.e2e.one *' | head -n 1)
[[ -f $moved$one ]] || fail "the Trash does not have $one."
[[ $(stat -f %u "$moved$one") == "$(id -u)" ]] || fail "$moved$one is not yours."

echo 'tidy.sh: ok'
