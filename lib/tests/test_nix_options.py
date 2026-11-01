"""Options of the dotkit module that a dotfiles repository sets, evaluated by Nix.

Like tests.test_nix_lists, they need nix and are skipped without it.
"""

from __future__ import annotations

import json
import unittest

from tests.test_nix_lists import NIX, eval_mac

HOME = 'home-manager.users."example"'


def home_module(settings: str) -> str:
    return f'{{ {HOME} = {{ {settings} }}; }}'


@unittest.skipUnless(NIX, "no nix")
class NixOptionTests(unittest.TestCase):
    def test_an_integration_can_be_disabled(self):
        """nix/shell.nix can turn off an integration that the base turns on."""
        out = eval_mac(home_module("dotkit.zsh.integrations.starship.enable = false;"),
                       "integrations", "--json", "--apply", "i: builtins.mapAttrs (_: t: t.enable) i",
                       under=f"config.{HOME}.dotkit.zsh")
        self.assertEqual(out.returncode, 0, out.stderr)
        enabled = json.loads(out.stdout)
        self.assertIs(enabled["starship"], False)
        self.assertIs(enabled["fzf"], True)


if __name__ == "__main__":
    unittest.main()
