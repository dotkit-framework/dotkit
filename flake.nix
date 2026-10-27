{
  description = "dotkit: the shared base of an Apple Silicon Mac setup";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    nix-darwin = {
      url = "github:nix-darwin/nix-darwin/master";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    home-manager = {
      url = "github:nix-community/home-manager/master";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # zsh plugins for nix/modules/zsh. Each URL has a commit, so `nix flake
    # update` does not change them. To update a plugin, review the new commits,
    # put the new commit here, and apply. The comment is the release that
    # `git describe --tags` shows for the commit.
    zsh-completions = {
      # 0.36.0-184
      url = "github:zsh-users/zsh-completions/989b23277ab33e8d5e82df6aed936e846db9d8b8";
      flake = false;
    };
    zsh-history-substring-search = {
      # v1.1.0-14
      url = "github:zsh-users/zsh-history-substring-search/14c8d2e0ffaee98f2df9850b19944f32546fdea5";
      flake = false;
    };
    zsh-syntax-highlighting = {
      # 0.8.0-8
      url = "github:zsh-users/zsh-syntax-highlighting/e6683f20e5c38d3e5447f45845d04ddda75d0f23";
      flake = false;
    };
    zsh-autosuggestions = {
      # v0.7.1-6
      url = "github:zsh-users/zsh-autosuggestions/85919cd1ffa7d2d5412f6d3fe437ebdbeeec4fc5";
      flake = false;
    };
    zsh-abbr = {
      # v6.5.2-4. The zsh-job-queue submodule is not used, see nix/modules/zsh.
      url = "github:olets/zsh-abbr/2de4a08c5e0d9dbe8447e11e0a177b59b5b6d6ea";
      flake = false;
    };
    zsh-autosuggestions-abbreviations-strategy = {
      # v1.1.2
      url = "github:olets/zsh-autosuggestions-abbreviations-strategy/3ce24713e4a49f9a5060d9016d35f3cd048d1c74";
      flake = false;
    };
  };

  outputs =
    inputs@{
      self,
      nixpkgs,
      nix-darwin,
      home-manager,
      ...
    }:
    let
      system = "aarch64-darwin";
      pkgs = nixpkgs.legacyPackages.${system};

      # The base for nix-darwin. The user flake passes `user` (nix/user.nix of
      # the dotfiles repository) in specialArgs. See templates/default.
      darwinModule =
        { lib, user, ... }:
        {
          imports = [
            home-manager.darwinModules.home-manager
            ./nix/darwin.nix
          ];
          _module.args.dotkitInputs = inputs;

          home-manager = {
            useGlobalPkgs = true;
            useUserPackages = true;
            extraSpecialArgs = {
              inherit user;
              dotkitInputs = inputs;
            };
            sharedModules = [ ./nix/home.nix ];
            # Home Manager does not replace a file that it does not manage, for
            # example a ~/.zshrc from before dotkit. It renames such a file to
            # <name>.before-dotkit and links its own. When that name exists
            # too, the apply stops: move one of the two files.
            backupFileExtension = lib.mkDefault "before-dotkit";
          };
        };

      # A Mac from the template, for `nix flake check`.
      example =
        let
          user = rec {
            name = "example";
            home = "/Users/${name}";
            dotfiles = "${home}/.dotfiles";
          };
        in
        nix-darwin.lib.darwinSystem {
          specialArgs = { inherit user; };
          modules = [
            darwinModule
            ./templates/default/nix/darwin.nix
            { home-manager.users.${user.name} = import ./templates/default/nix/home.nix; }
          ];
        };
    in
    {
      darwinModules.default = darwinModule;

      # The migration wizard of scripts/setup and `dotkit migrate`, for
      # `nix run .#migrate`. It runs with the macOS Python of the Xcode Command
      # Line Tools, so it needs nothing from Nixpkgs. See migrate/dotkit-migrate.
      apps.${system}.migrate = {
        type = "app";
        program = "${self}/migrate/dotkit-migrate";
        meta.description = "The migration wizard of dotkit setup";
      };

      templates.default = {
        path = ./templates/default;
        description = "A dotfiles repository on top of dotkit";
      };

      checks.${system} = {
        example = example.system;
        migrate = pkgs.callPackage ./lib/tests.nix { };
      };

      formatter.${system} = pkgs.nixfmt;
    };
}
