"""The declared tool names, for the zsh completion (`dotkit __complete tools-user|tools-all`).

It imports only the readers, so that the completion starts fast.
"""

from __future__ import annotations

from typing import List

from ..core import lists, mise_toml
from ..core.env import Env


def declared(env: Env, everything: bool) -> List[str]:
    """The names of the lists of the dotfiles repository, and with everything those of the dotkit base too:
    list names, mas:ID, and mise keys. Sorted, each once."""
    roots = [env.repo] + ([env.dotkit] if everything else [])
    out = set()
    for root in roots:
        for rel in ("nix/packages.txt", "nix/brews.txt", "nix/casks.txt"):
            out.update(lists.names(root / rel))
        out.update("mas:" + app_id for app_id, _ in lists.mas_apps(root / "nix/mas.txt"))
        out.update(mise_toml.tools(root / "config/mise.toml"))
    return sorted(out)
