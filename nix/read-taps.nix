# Reads Homebrew tap files for homebrew.taps: one tap per line, `owner/repo`,
# or `owner/repo <clone URL>` for a tap that is not on GitHub. `#` starts a
# comment, as in nix/read-list.nix; blank lines are ignored. When two lines
# have the same tap, the first one wins.
#
# nix-darwin writes each tap into the Brewfile, a Ruby file, without escaping.
# So a name must be owner/repo of letters, digits, `_`, `.`, and `-`, and a URL
# must not have `"` or `\`. A line that breaks this stops the evaluation.
lib: files:
let
  tap =
    file: n: line:
    let
      words = lib.filter (w: lib.isString w && w != "") (builtins.split "[[:space:]]+" line);
      name = lib.head words;
      url = lib.elemAt words 1;
      bad =
        lib.length words > 2
        || builtins.match "[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+" name == null
        || (lib.length words == 2 && builtins.match "[^\"\\\\]+" url == null);
    in
    if bad then
      throw "${toString file} line ${toString n}: '${line}' is not a tap. Use owner/repo, or owner/repo and its clone URL, without \" or \\."
    else if lib.length words == 1 then
      { inherit name; }
    else
      {
        inherit name;
        clone_target = url;
      };

  lines =
    file:
    lib.concatLists (
      lib.imap1 (
        n: raw:
        let
          line = lib.trim (lib.head (lib.splitString "#" raw));
        in
        lib.optional (line != "") (tap file n line)
      ) (lib.splitString "\n" (builtins.readFile file))
    );

  taps = lib.concatMap lines files;
  # Each tap as its first line has it.
  first = lib.unique (map (t: lib.findFirst (x: x.name == t.name) t taps) taps);
in
map (t: if t ? clone_target then t else t.name) first
