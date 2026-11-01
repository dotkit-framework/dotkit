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


def failed_assertions(settings: str) -> list:
    """The messages of the assertions that fail. The build stops with them."""
    out = eval_mac(home_module(settings), "assertions", "--json", "--apply",
                   "as: map (a: a.message) (builtins.filter (a: !a.assertion) as)", under="config")
    if out.returncode != 0:
        raise AssertionError(f"nix eval failed: {out.stderr}")
    return json.loads(out.stdout)


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

    def test_the_default_configuration_has_no_errors(self):
        self.assertEqual(failed_assertions(""), [])

    def test_a_link_and_a_copy_of_one_target(self):
        messages = failed_assertions('dotkit.links.".config/demo" = "config/demo"; '
                                     'dotkit.copies.".config/demo" = "config/demo";')
        self.assertEqual(messages, [
            "example profile: dotkit.links and dotkit.copies both have ~/.config/demo. Home Manager would link "
            "it, and then the copy would replace the link. Set dotkit.links.\".config/demo\" = null; (the base "
            "may link it), or remove the copy.",
        ])
        # A null link is no link.
        self.assertEqual(failed_assertions('dotkit.links.".gemrc" = null; dotkit.copies.".gemrc" = "gemrc";'), [])

    def test_targets_that_are_not_canonical(self):
        for option, target in (("links", "./.config/demo/child"), ("links", ".config/demo/"),
                               ("copies", ".config//demo/x"), ("copies", "/Users/example/.x"),
                               ("links", ".config/../.x"), ("links", ".config/./x"), ("copies", ""),
                               ("links", "~/.x")):
            with self.subTest(option=option, target=target):
                value = "null" if option == "links" and target.endswith("/") else '"config/x"'
                messages = failed_assertions(f'dotkit.links.".config/demo" = "config/demo"; '
                                             f'dotkit.{option}.{json.dumps(target)} = {value};')
                self.assertIn(f'example profile: dotkit.{option}: "{target}" is not a canonical target. Write it '
                              "relative to the home directory, with no ~/, ./, .., or // and no / at the start or "
                              'end, for example ".config/foo".', messages)


if __name__ == "__main__":
    unittest.main()
