# Reads App Store files for homebrew.masApps: one app per line, `<ID> <name>`,
# as `mas list` shows them. The name is the rest of the line, and can have
# spaces, `#`, and Unicode. A line whose first character is `#` is a comment;
# blank lines are ignored. When two lines have the same name, the first one
# wins.
#
# nix-darwin writes each name into the Brewfile, a Ruby file, as `mas "<name>"`
# without escaping. So the names here come escaped for a Ruby string in double
# quotes: `\` -> `\\`, `"` -> `\"`, and `#` -> `\#` (no `#{...}` runs).
lib: files:
let
  app =
    file: n: line:
    let
      m = builtins.match "([0-9]+)[[:space:]]+(.+)" line;
    in
    if m == null then
      throw "${toString file} line ${toString n}: '${line}' is not an App Store app. Use the ID, a space, and the name."
    else
      {
        name = lib.elemAt m 1;
        value = lib.toIntBase10 (lib.head m);
      };

  apps =
    file:
    lib.concatLists (
      lib.imap1 (
        n: raw:
        let
          line = lib.trim raw;
        in
        lib.optional (line != "" && !lib.hasPrefix "#" line) (app file n line)
      ) (lib.splitString "\n" (builtins.readFile file))
    );

  ruby = builtins.replaceStrings [ "\\" "\"" "#" ] [ "\\\\" "\\\"" "\\#" ];
in
# listToAttrs keeps the first value of a name; the escape comes after it.
lib.mapAttrs' (name: lib.nameValuePair (ruby name)) (
  builtins.listToAttrs (lib.concatMap apps files)
)
