# Home Manager settings of this Mac, on top of the dotkit base. Options:
# https://nix-community.github.io/home-manager/options.xhtml
{
  imports = [ ./shell.nix ];

  # Your config files, as links: target in ~ = file in this repository.
  # A target that the dotkit base also links (for example .config/starship.toml)
  # replaces the base file. null removes a base link.
  dotkit.links = {
    ".zshrc.local" = "config/zsh/zshrc";
  };

  # Packages that need a Nix expression. Plain names go in nix/packages.txt.
  # home.packages = [ (pkgs.python3.withPackages (ps: [ ps.requests ])) ];
}
