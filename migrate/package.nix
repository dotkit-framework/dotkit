# The migration wizard of scripts/setup, and `dotkit migrate`: Python from
# Nixpkgs, with the standard library only. The source has the dotkit lists,
# config files, and template too, so the wizard knows what the base has.
#
# A plain derivation: it only writes one script, so it needs no stdenv.
# Python, bash, and coreutils come from the binary cache.
{
  lib,
  stdenvNoCC,
  bash,
  coreutils,
  python3,
}:

let
  # The layout of the dotkit directory: dotkit_migrate/base.py finds the lists
  # two directories up.
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ./dotkit_migrate
      ../nix/packages.txt
      ../nix/brews.txt
      ../nix/casks.txt
      ../config
      ../templates/default
    ];
  };
  script = ''
    #!${bash}/bin/bash
    export PYTHONPATH=${src}/migrate PYTHONDONTWRITEBYTECODE=1
    exec ${python3.interpreter} -m dotkit_migrate "$@"
  '';
in
derivation {
  name = "dotkit-migrate";
  inherit (stdenvNoCC.hostPlatform) system;
  inherit script;
  builder = "${bash}/bin/bash";
  args = [
    "-c"
    ''
      ${coreutils}/bin/mkdir -p "$out/bin"
      printf '%s' "$script" >"$out/bin/dotkit-migrate"
      ${coreutils}/bin/chmod +x "$out/bin/dotkit-migrate"
    ''
  ];
}
// {
  meta = {
    description = "The migration wizard of dotkit setup";
    mainProgram = "dotkit-migrate";
  };
}
