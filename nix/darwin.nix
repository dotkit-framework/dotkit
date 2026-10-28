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
  # Homebrew names for the Brewfile, a Ruby file that nix-darwin writes without
  # escaping: stop at a name that could break it.
  brewNames =
    option: files:
    map (
      name:
      if builtins.match "[A-Za-z0-9@+._/-]+" name == null then
        throw "dotkit.lists.${option}: '${name}' is not a Homebrew name."
      else
        name
    ) (names files);
  # Taps and App Store apps have more than a name on a line, so they have their own readers.
  readTaps = import ./read-taps.nix lib;
  readMasApps = import ./read-mas.nix lib;

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
      taps = mkOption {
        type = types.listOf types.path;
        default = [ ];
        description = ''
          Files with Homebrew taps, one per line: owner/repo, and the clone URL
          after it for a tap that is not on GitHub. See nix/read-taps.nix.
        '';
      };
      masApps = mkOption {
        type = types.listOf types.path;
        default = [ ];
        description = ''
          Files with Mac App Store apps, one per line: the App Store ID, then
          the name. See nix/read-mas.nix.
        '';
      };
    };

    guiVariables = mkOption {
      type = types.listOf types.str;
      description = ''
        Home Manager session variables that GUI apps also get, at each login.
        PATH is always included.
      '';
    };

    guiSecrets.enable = mkOption {
      type = types.bool;
      default = true;
      description = ''
        GUI apps also get the variables that ~/.secrets exports, as terminal
        programs do. The values are read from ~/.secrets at each login and
        when the file changes. They stay out of the Nix store.
      '';
    };
  };

  config = {
    nixpkgs.hostPlatform = "aarch64-darwin";

    dotkit = {
      # The base lists. The dotfiles repository adds its own files.
      # See nix/read-list.nix for the format.
      lists = {
        packages = [ ./packages.txt ];
        brews = [ ./brews.txt ];
        casks = [ ./casks.txt ];
      };
      guiVariables = [
        "JAVA_HOME"
        "NPM_CONFIG_GLOBALCONFIG"
      ];
    };

    home-manager.users.${user.name}.home.packages = map package (names cfg.lists.packages);

    environment.etc = {
      # uv reads this system config first, then ~/.config/uv/uv.toml, which is
      # yours and wins.
      "uv/uv.toml".source = ../config/uv.toml;

      # The links of nix/links.nix, for the report of `dotkit apply` and `dotkit doctor`.
      "dotkit/links.json".text = builtins.toJSON (
        config.home-manager.users.${user.name}.dotkit.manifest
        // {
          inherit (config.home-manager) backupFileExtension;
        }
      );
    };

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

    # The same for the variables that ~/.secrets exports. launchd runs the
    # agent again when the file changes. Apps that run already keep their old
    # values until a restart. A variable removed from ~/.secrets stays until
    # the next login.
    launchd.user.agents.gui-secrets = lib.mkIf cfg.guiSecrets.enable {
      script = ''
        secrets=${lib.escapeShellArg "${user.home}/.secrets"}
        [ -r "$secrets" ] || exit 0
        # A clean environment: only what the file exports, and what sh sets itself.
        /usr/bin/env -i HOME=${lib.escapeShellArg user.home} /bin/sh -c '. "$1" >/dev/null 2>&1; /usr/bin/env -0' sh "$secrets" |
          while IFS= read -r -d "" entry; do
            name=''${entry%%=*}
            case $name in PWD | SHLVL | HOME | _) continue ;; esac
            /bin/launchctl setenv "$name" "''${entry#*=}"
          done
      '';
      serviceConfig = {
        RunAtLoad = true;
        WatchPaths = [ "${user.home}/.secrets" ];
      };
    };

    # Ghostty and the Starship config use the Nerd Font.
    fonts.packages = with pkgs; [
      jetbrains-mono
      nerd-fonts.jetbrains-mono
    ];

    homebrew = {
      enable = true;
      global.brewfile = true;

      # Activation installs, but does not upgrade: run `dotkit upgrade brew`.
      # It does not uninstall either, so the formulae and apps that were on
      # the Mac before dotkit stay. `dotkit remove` uninstalls what it
      # removes. With cleanup = "uninstall", activation uninstalls everything
      # that the lists do not have.
      onActivation = {
        cleanup = lib.mkDefault "none";
        autoUpdate = false;
        upgrade = false;
        extraEnv.HOMEBREW_NO_INSTALL_CLEANUP = "1";
      };

      brews = brewNames "brews" cfg.lists.brews;
      casks = brewNames "casks" cfg.lists.casks;
      taps = readTaps cfg.lists.taps;
      masApps = readMasApps cfg.lists.masApps;
    };
  };
}
