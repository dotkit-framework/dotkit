# The unit tests and the pseudo-terminal tests of lib/ (the migration wizard),
# for `nix flake check`, with the Python of Nixpkgs: newer than the macOS
# Python 3.9 that the wizard runs with. CI also runs them directly with both, for
# the log.
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
        ./dotkit
        ./run.py
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
    cd work/lib
    export HOME=$TMPDIR/home
    mkdir -p $HOME
    python3 -m unittest discover -s tests -t . -v
    touch $out
  ''
