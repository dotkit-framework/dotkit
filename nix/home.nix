# The dotkit base for Home Manager: session variables, the zsh setup, direnv,
# and the base config files. nix/darwin.nix is the nix-darwin part.
{
  config,
  lib,
  pkgs,
  user,
  ...
}:

let
  cfg = config.dotkit;
  # Base links point into the dotkit directory, at mkDefault priority, so the
  # dotfiles repository can replace each one (see nix/links.nix).
  base = lib.mapAttrs (_target: file: lib.mkDefault "${cfg.source}/${file}");
in
{
  imports = [
    ./links.nix
    ./modules/zsh
  ];

  options.dotkit.toolPath = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    description = ''
      Global tool directories, in priority order. config/zsh/zprofile and
      config/bash/bash_profile put them before the inherited PATH, and
      nix/darwin.nix gives them to GUI apps.
      Entries without a priority go after ~/.local/bin and before Homebrew.
    '';
  };

  config = {
    home = {
      stateVersion = lib.mkDefault "26.05";

      # `dotkit`, from any directory: runs ./dotkit of the dotfiles repository.
      # With the zsh completion; nix-darwin puts the profile's site-functions
      # in fpath.
      packages = [
        (pkgs.symlinkJoin {
          name = "dotkit";
          paths = [
            (pkgs.writeShellScriptBin "dotkit" ''
              if [[ ! -x ${lib.escapeShellArg "${user.dotfiles}/dotkit"} ]]; then
                printf 'dotkit: %s is missing. Check dotfiles in nix/user.nix.\n' ${lib.escapeShellArg "${user.dotfiles}/dotkit"} >&2
                exit 1
              fi
              exec ${lib.escapeShellArg "${user.dotfiles}/dotkit"} "$@"
            '')
            (pkgs.writeTextDir "share/zsh/site-functions/_dotkit" (builtins.readFile ./completions/_dotkit))
          ];
        })

        # The GNU coreutils that macOS does not have. Scripts and coding agents
        # use timeout often. Only these: the others would hide the BSD tools
        # (sed, stat, date, ...) that Xcode and CocoaPods scripts expect.
        (pkgs.runCommand "coreutils-missing-on-macos" { } ''
          mkdir -p $out/bin
          for tool in timeout nproc tac shuf numfmt; do
            ln -s ${pkgs.coreutils}/bin/$tool $out/bin/$tool
          done
        '')
      ];

      sessionVariables = {
        # A Nix dev shell or a project mise.toml with java replaces it in the project.
        JAVA_HOME = pkgs.zulu17.home;
        DOTKIT_TOOL_PATH = lib.concatStringsSep ":" cfg.toolPath;

        # New releases wait 7 days, as in config/npmrc, pnpm.yaml, uv.toml,
        # bunfig.toml, and mise.toml. pip 26.1 or newer reads the duration.
        PIP_UPLOADED_PRIOR_TO = "P7D";
        # Bundler 4.0.13 or newer, in days. It wins over `cooldown:` in a
        # Gemfile; for a private source, use `bundle install --cooldown 0`.
        BUNDLE_COOLDOWN = "7";
      };
    };

    # Projects opt in with `direnv allow`. nix/modules/zsh adds the zsh hook,
    # because Home Manager does not generate the zsh files.
    programs.direnv = {
      enable = true;
      nix-direnv = {
        enable = true;
        # Use the system Nix from nix/darwin.nix, not a second copy of pkgs.nix.
        package = pkgs.nix-direnv.override { nix = pkgs.nixVersions.latest; };
      };
    };

    dotkit = {
      toolPath = lib.mkMerge [
        [
          "/etc/profiles/per-user/${user.name}/bin"
          "/run/current-system/sw/bin"
          "${user.home}/.local/bin"
        ]
        (lib.mkAfter [
          # Homebrew casks link their CLIs here, for example subl.
          "/opt/homebrew/bin"
          "/opt/homebrew/sbin"
        ])
      ];

      zsh = {
        enable = true;
        # Expands to `dotkit` as you type, for example `dk apply`.
        abbreviations.dk = lib.mkDefault "dotkit";
        integrations = {
          fzf.enable = true;
          zoxide.enable = true;
          starship.enable = true;
          mise.enable = true;
          direnv.enable = true;
        };
      };

      links =
        base {
          ".bunfig.toml" = "config/bunfig.toml";
          ".editorconfig" = "config/editorconfig";
          ".gemrc" = "config/gemrc";
          ".npmrc" = "config/npmrc";

          ".zshenv" = "config/zsh/zshenv";
          ".zprofile" = "config/zsh/zprofile";
          ".zshrc" = "config/zsh/zshrc";
          # For `bash -l`, for example scripts with #!/bin/bash -l.
          ".bash_profile" = "config/bash/bash_profile";

          # Git reads this file first and ~/.gitconfig after it, so ~/.gitconfig wins.
          ".config/git/config" = "config/gitconfig";
          # Git reads this file when core.excludesFile is not set.
          ".config/git/ignore" = "config/gitignore";
          # Ghostty then reads ~/.config/ghostty/config.local, which wins.
          ".config/ghostty/config" = "config/ghostty.conf";
          ".config/herdr/config.toml" = "config/herdr.toml";
          ".config/herdr/git-status.py" = "scripts/herdr/git-status.py";
          ".config/herdr/git-status.sh" = "scripts/herdr/git-status.sh";
          ".config/herdr/process-name.bash" = "scripts/herdr/process-name.bash";
          ".config/herdr/process-name.sh" = "scripts/herdr/process-name.sh";
          ".config/herdr/process-name.zsh" = "scripts/herdr/process-name.zsh";
          # mise reads conf.d and ~/.config/mise/config.toml. `mise use -g` writes the latter.
          ".config/mise/conf.d/dotkit.toml" = "config/mise.toml";
          ".config/starship.toml" = "config/starship.toml";
          ".config/uv/uv.toml" = "config/uv.toml";

          # pnpm reads its global config here on macOS unless XDG_CONFIG_HOME is set.
          "Library/Preferences/pnpm/config.yaml" = "config/pnpm.yaml";
        }
        // {
          # The dotkit command and ./dotkit of the dotfiles repository run the
          # dotkit script of this directory.
          ".local/share/dotkit" = lib.mkDefault cfg.source;

          # Files of the dotfiles repository that `dotkit add` edits. `mise use -g` writes
          # through this link.
          ".config/mise/config.toml" = lib.mkDefault "config/mise.toml";
        };
    };

    # Native GUI apps are managed by nix-darwin and Homebrew.
    targets.darwin.copyApps.enable = false;
    targets.darwin.linkApps.enable = false;
  };
}
