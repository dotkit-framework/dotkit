# The dotkit base for nix-darwin: Nix, the login shell, Touch ID for sudo,
# fonts, Homebrew, and the package lists. nix/home.nix is the Home Manager part.
{
  config,
  pkgs,
  lib,
  user,
  ...
}:

let
  cfg = config.dotkit;
  inherit (lib) mkOption types;

  readList = import ./read-list.nix lib;
  # Names from all list files of one kind, in order, without repeats.
  names = files: lib.unique (lib.concatMap readList files);

  package =
    name:
    lib.attrByPath (lib.splitString "." name)
      (throw "dotkit.lists.packages: Nixpkgs has no package '${name}'.")
      pkgs;

  hmVariables = config.home-manager.users.${user.name}.home.sessionVariables;

  # GUI processes do not read the shell profile. Give them the same tools and SDKs.
  # They do not run `mise activate`, so the mise shims supply the global runtimes.
  guiEnvironment = lib.genAttrs cfg.guiVariables (name: hmVariables.${name}) // {
    PATH = "${hmVariables.DOTKIT_TOOL_PATH}:${user.home}/.local/share/mise/shims:/usr/bin:/bin:/usr/sbin:/sbin";
  };
in
{
  options.dotkit = {
    lists = {
      packages = mkOption {
        type = types.listOf types.path;
        description = "Files with Nixpkgs attribute names for home.packages, one per line.";
      };
      brews = mkOption {
        type = types.listOf types.path;
        description = "Files with Homebrew formulae, one per line.";
      };
      casks = mkOption {
        type = types.listOf types.path;
        description = "Files with Homebrew casks, one per line.";
      };
    };

    guiVariables = mkOption {
      type = types.listOf types.str;
      description = ''
        Home Manager session variables that GUI apps also get, at each login.
        PATH is always included.
      '';
    };
  };

  config = {
    nixpkgs.hostPlatform = "aarch64-darwin";

    # The base lists. The dotfiles repository adds its own files.
    # See nix/read-list.nix for the format.
    dotkit.lists = {
      packages = [ ./packages.txt ];
      brews = [ ./brews.txt ];
      casks = [ ./casks.txt ];
    };
    dotkit.guiVariables = [ "JAVA_HOME" ];

    home-manager.users.${user.name}.home.packages = map package (names cfg.lists.packages);

    system = {
      primaryUser = user.name;
      stateVersion = lib.mkDefault 6;
      # The check looks for <darwin-config>, which a flake configuration does not have.
      checks.verifyNixPath = false;
    };
    users.users.${user.name}.home = user.home;

    # Touch ID for sudo. reattach makes it work in terminal multiplexers too.
    security.pam.services.sudo_local = {
      touchIdAuth = true;
      reattach = true;
    };

    nix = {
      enable = true;
      package = pkgs.nixVersions.latest;
      channel.enable = false;
      settings.experimental-features = [
        "nix-command"
        "flakes"
      ];

      # Weekly, as root. Integrated Home Manager generations belong to the system
      # profile. Separate user profiles need manual pruning.
      gc = {
        automatic = true;
        options = "--delete-older-than 14d";
      };
      optimise.automatic = true;
    };

    # nix/modules/zsh runs compinit and Starship draws the prompt. /etc/zshrc
    # keeps only the Nix completion paths in fpath.
    programs.zsh = {
      enable = true;
      enableGlobalCompInit = false;
      enableBashCompletion = false;
      promptInit = "";
    };

    # nix-darwin replaces a file in /etc only when it knows its content, and
    # keeps it as <name>.before-nix-darwin. The Nix installer of scripts/setup
    # (2.35) puts its lines at the start of /etc/bashrc and /etc/zshrc.
    # nix-darwin does not know the results yet, so without these hashes the
    # first activation stops.
    environment.etc = {
      bashrc.knownSha256Hashes = [ "8b5e3466922d1ae34bc145e21c7e53e7329a7a7b58b148b436bd954d5e651ac3" ];
      zshrc.knownSha256Hashes = [
        "af60f7af4a5b4c1b0efe950e3e3f3ee8b136834ecb46fd7dba76f4b66adbc3e1" # macOS 15
        "cf0f7b7775b4c058d6085d9e7e57d58c307ca43730f8e4d921a9ef4e530e7e16" # macOS 26
      ];
    };

    # The login shell is the Nix zsh, the version that nix/modules/zsh compiles
    # its files with. environment.shells adds it to /etc/shells. nix-darwin sets
    # users.users.<name>.shell only for knownUsers, which must not include the
    # admin user, so set it here.
    # postActivation runs before /run/current-system points to this generation.
    # On a new Mac the link does not exist yet, and a later failure would leave
    # a login shell that does not exist. Change the shell only when the path
    # works now and in this generation; on a new Mac, the second switch sets it.
    # If the Nix volume does not mount, use `/bin/zsh -l` as the Terminal command.
    environment.shells = [ pkgs.zsh ];
    system.activationScripts.postActivation.text = ''
      loginShell=/run/current-system/sw${pkgs.zsh.shellPath}
      if [[ ! -x $loginShell || ! -x $systemConfig/sw${pkgs.zsh.shellPath} ]]; then
        echo "not changing the login shell of ${user.name}: $loginShell is not there yet. Switch again." >&2
      elif [[ $(dscl . -read ${lib.escapeShellArg "/Users/${user.name}"} UserShell) != "UserShell: $loginShell" ]]; then
        echo "setting the login shell of ${user.name} to $loginShell..." >&2
        dscl . -create ${lib.escapeShellArg "/Users/${user.name}"} UserShell "$loginShell"
      fi
    '';

    # launchd.user.envVariables sets these only during activation, and a reboot clears
    # them. This agent sets them again at each login.
    launchd.user.agents.gui-environment = {
      script = lib.concatLines (
        lib.mapAttrsToList (
          name: value: "/bin/launchctl setenv ${name} ${lib.escapeShellArg value}"
        ) guiEnvironment
      );
      serviceConfig.RunAtLoad = true;
    };

    # Ghostty and the Starship config use the Nerd Font.
    fonts.packages = with pkgs; [
      jetbrains-mono
      nerd-fonts.jetbrains-mono
    ];

    homebrew = {
      enable = true;
      global.brewfile = true;

      # Activation installs and removes, but does not upgrade. Run `dotkit upgrade brew` to upgrade.
      onActivation = {
        cleanup = "uninstall";
        autoUpdate = false;
        upgrade = false;
        extraEnv.HOMEBREW_NO_INSTALL_CLEANUP = "1";
      };

      brews = names cfg.lists.brews;
      casks = names cfg.lists.casks;
    };
  };
}
