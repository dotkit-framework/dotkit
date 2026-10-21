# Settings for the zsh setup of dotkit (nix/modules/zsh in dotkit). Your other
# zsh lines are in config/zsh/zshrc, which needs no rebuild.
{
  dotkit.zsh = {
    # keymap = "vi";
    # title = "%~";

    # Expanded with Space or Enter. The name can have several words.
    abbreviations = {
      # "g" = "git";
      # "git s" = "git status";
    };
  };
}
