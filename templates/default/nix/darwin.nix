# nix-darwin settings of this Mac, on top of the dotkit base. Options:
# https://nix-darwin.github.io/nix-darwin/manual/
{
  # Your lists, after the dotkit base lists. `dotkit add` and `dotkit remove` edit them.
  dotkit.lists = {
    packages = [ ./packages.txt ];
    brews = [ ./brews.txt ];
    casks = [ ./casks.txt ];
  };

  # Homebrew taps for formulae and casks from other repositories: "owner/repo".
  # A formula from a tap is "owner/repo/name" in nix/brews.txt.
  homebrew.taps = [ ];

  # Mac App Store apps: name = App Store ID.
  homebrew.masApps = { };
}
