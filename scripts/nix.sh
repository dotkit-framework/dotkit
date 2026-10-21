# Nix commands for the flake of the dotfiles repository. dotkit and
# scripts/dotkit-pkg source this file after they set `repo`. It must work with
# the macOS Bash 3.2.
#
# DOTKIT_DIR selects a dotkit checkout instead of the release in nix/flake.lock
# (--override-input). Nix reads the files that Git tracks, with their
# uncommitted changes: `git add` a new file first.

# shellcheck disable=SC2154 # The sourcing script sets repo.
flake="path:$repo/nix"
result="$repo/.build/system"

dotkit_dir=''
override=()
if [[ -n ${DOTKIT_DIR:-} ]]; then
  dotkit_dir=$(cd -- "$DOTKIT_DIR" && pwd -P) || { printf 'dotkit: DOTKIT_DIR is not a directory: %s\n' "$DOTKIT_DIR" >&2; exit 2; }
  override=(--override-input dotkit "git+file://$dotkit_dir" --no-write-lock-file --option warn-dirty false)
fi

# Copies stdin to stdout without the warning that Nix prints for each command
# with --override-input: "not writing modified lock file", and the list of
# changed inputs after it.
hide_lock_warning() {
  awk '
    /^warning: not writing (modified )?lock file of flake/ { skip = 1; next }
    skip && /^(• |  →|    )/ { next }
    { skip = 0; print; fflush() }
  '
}

# Runs nix with the arguments, for a command that reads the flake. With
# DOTKIT_DIR, adds the override and hides the lock file warning. Stdout stays
# stdout; the exit status is the status of nix.
nix_flake() {
  if ((${#override[@]} == 0)); then
    nix "$@"
    return
  fi
  { nix "$@" "${override[@]}" 2>&1 1>&3 3>&- | hide_lock_warning >&2; } 3>&1
}

# Builds the system and links it to $result. With DOTKIT_DIR, the evaluation
# goes through nix_flake, and nix builds the derivation without the flake: so
# no lock file warning, and nix keeps its progress bar, which needs a terminal
# on stderr.
build_system() {
  if ((${#override[@]} == 0)); then
    nix build "$flake#darwinConfigurations.mac.system" --out-link "$result"
    return
  fi
  local drv
  drv=$(nix_flake eval --raw "$flake#darwinConfigurations.mac.system.drvPath")
  nix build "$drv^*" --out-link "$result"
}
