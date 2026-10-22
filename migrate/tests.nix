# The unit tests and the pseudo-terminal tests of the migration wizard, for
# `nix flake check`. CI also runs them directly, for the log.
{
  lib,
  runCommand,
  python3,
  git,
  zsh,
}:

runCommand "dotkit-migrate-tests"
  {
    nativeBuildInputs = [
      python3
      git
      zsh
    ];
    src = lib.fileset.toSource {
      root = ../.;
      fileset = lib.fileset.unions [
        ./dotkit_migrate
        ./tests
        ../nix
        ../config
        ../templates/default
      ];
    };
  }
  ''
    cp -r $src work
    chmod -R u+w work
    cd work/migrate
    export HOME=$TMPDIR/home
    mkdir -p $HOME
    python3 -m unittest discover -s tests -t . -v
    touch $out
  ''
