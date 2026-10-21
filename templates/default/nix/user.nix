# Your macOS account and this repository. dotkit's scripts/setup writes it.
rec {
  name = "example";
  home = "/Users/${name}";
  dotfiles = "${home}/.dotfiles";
}
