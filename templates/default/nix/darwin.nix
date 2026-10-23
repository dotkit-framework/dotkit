# nix-darwin settings of this Mac, on top of the dotkit base. Options:
# https://nix-darwin.github.io/nix-darwin/manual/
{
  # Your lists, after the dotkit base lists. `dotkit add` and `dotkit remove` edit them.
  dotkit.lists = {
    packages = [ ./packages.txt ];
    brews = [ ./brews.txt ];
    casks = [ ./casks.txt ];
    taps = [ ./taps.txt ];
    masApps = [ ./mas.txt ];
  };
}
