# Config files in the home directory, as links. nix/home.nix lists the base
# files; the dotfiles repository adds its own and can replace any base link.
# The manifest of the links goes into the system (nix/darwin.nix), where the
# dotkit command reads it to report the base files that yours replace.
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

  # Home Manager links a file inside a linked directory through that link, into
  # the source of the directory. Each pair of such targets is an error.
  targets = lib.attrNames links ++ lib.attrNames copies;
  nested = lib.concatMap (
    outer:
    map (inner: { inherit outer inner; }) (lib.filter (inner: lib.hasPrefix "${outer}/" inner) targets)
  ) targets;
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

    baseLinks = mkOption {
      type = types.attrsOf types.str;
      default = { };
      internal = true;
      description = ''
        The base config files: target = file in the dotkit directory.
        dotkit.links has each one at mkDefault priority.
      '';
    };

    layers = mkOption {
      type = types.attrsOf types.str;
      default = { };
      internal = true;
      description = ''
        Base link target = the file of yours that its tool reads after it.
      '';
    };

    manifest = mkOption {
      type = types.attrs;
      readOnly = true;
      internal = true;
      description = "The links for the dotkit command: etc/dotkit/links.json of the system.";
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
    dotkit.links = lib.mapAttrs (_target: file: lib.mkDefault "${cfg.source}/${file}") cfg.baseLinks;

    dotkit.manifest = {
      version = 1;
      dotkit = cfg.source;
      base = lib.mapAttrs (_target: file: "${cfg.source}/${file}") cfg.baseLinks;
      inherit (cfg) layers;
      links = lib.mapAttrs (_target: source: if source == null then null else resolve source) cfg.links;
      inherit copies;
    };

    assertions = map (n: {
      assertion = false;
      message =
        "dotkit.links: ~/${n.inner} is inside ~/${n.outer}, which is a link too. Home Manager would write "
        + "the file into the source of ~/${n.outer}. Link one of them only, or set one to null.";
    }) nested;

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
