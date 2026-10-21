# Config files in the home directory, as links. nix/home.nix links the base
# files; the dotfiles repository adds its own and can replace any base link.
{
  config,
  lib,
  user,
  dotkitInputs,
  ...
}:

let
  cfg = config.dotkit;
  inherit (lib) mkOption types;

  # A relative source is in the dotfiles repository.
  resolve = source: if lib.hasPrefix "/" source then source else "${user.dotfiles}/${source}";
  live = source: config.lib.file.mkOutOfStoreSymlink source;

  links = lib.mapAttrs (_target: resolve) (
    lib.filterAttrs (_target: source: source != null) cfg.links
  );
  copies = lib.mapAttrs (_target: resolve) cfg.copies;
in
{
  options.dotkit = {
    liveDir = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "/Users/me/.dotkit";
      description = ''
        A dotkit checkout. When set, the base config files link to it, so an
        edit applies without a rebuild. When null, they link to the dotkit
        release in the Nix store, which only changes with an apply.
      '';
    };

    source = mkOption {
      type = types.str;
      readOnly = true;
      internal = true;
      default = if cfg.liveDir != null then cfg.liveDir else "${dotkitInputs.self}";
      description = "The dotkit directory that the base links point into.";
    };

    links = mkOption {
      type = types.attrsOf (types.nullOr types.str);
      default = { };
      example = {
        ".config/starship.toml" = "config/starship.toml";
        ".npmrc" = null;
      };
      description = ''
        Links in the home directory: target = source. A relative source is a
        path in the dotfiles repository. The base sets its links with
        mkDefault, so a value here replaces a base file, and null removes it.
        Link a directory only if it holds config and no state or secrets.
      '';
    };

    copies = mkOption {
      type = types.attrsOf types.str;
      default = { };
      description = ''
        Files to copy instead of link, for apps that reject a link. A
        differing local copy is kept as .bak. A relative source is a path in
        the dotfiles repository.
      '';
    };
  };

  config = {
    home.file = lib.mapAttrs (_target: source: { source = live source; }) links;

    home.activation = {
      # Out-of-store links do not fail on a missing source. Stop before linking instead.
      checkLiveSources = lib.hm.dag.entryBefore [ "checkLinkTargets" ] ''
        for source in ${lib.escapeShellArgs (lib.attrValues (links // copies))}; do
          if [[ ! -e $source ]]; then
            errorEcho "Missing live config source: $source"
            exit 1
          fi
        done
      '';

      copyLiveSources = lib.hm.dag.entryAfter [ "linkGeneration" ] ''
        copyLiveSource() {
          local source=$1 target=$2
          cmp -s "$source" "$target" && return
          if [[ -f $target ]]; then
            run cp -p "$target" "$target.bak"
            warnEcho "Backed up $target to $target.bak"
          fi
          run mkdir -p "$(dirname "$target")"
          run install -m 600 "$source" "$target"
        }
        ${lib.concatLines (
          lib.mapAttrsToList (
            target: source:
            "copyLiveSource ${
              lib.escapeShellArgs [
                source
                "${config.home.homeDirectory}/${target}"
              ]
            }"
          ) copies
        )}
      '';
    };
  };
}
