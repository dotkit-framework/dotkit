{
  description = "My Mac, on top of dotkit";

  inputs = {
    # `dotkit upgrade` updates it. dotkit pins Nixpkgs, nix-darwin, and Home Manager.
    dotkit.url = "github:dotkit-framework/dotkit";
    # For tools that look for the Nixpkgs of this flake, for example nixd.
    nixpkgs.follows = "dotkit/nixpkgs";
  };

  outputs =
    { dotkit, ... }:
    let
      user = import ./user.nix;
    in
    {
      darwinConfigurations.mac = dotkit.inputs.nix-darwin.lib.darwinSystem {
        specialArgs = { inherit user; };
        modules = [
          dotkit.darwinModules.default
          ./darwin.nix
          { home-manager.users.${user.name} = import ./home.nix; }
        ];
      };

      inherit (dotkit) formatter;
    };
}
