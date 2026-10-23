"""What the tool migrators share (migrators/tools.py): the to-dos of the tools that are not carried."""

import unittest

from tests import fake
from tests.test_session import used_mac


class ToolsTests(unittest.TestCase):
    def test_tools_not_in_mise_keep_their_intros(self):
        """Default choices: npm is on, but pnpm is never carried; cargo is off."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = fake.session(h, used_mac(h))
        r = p.build(p.defaults(str(h.home / ".dotfiles"))).report
        npm = r.index("They stay where they are. To add one to config/mise.toml:\n")
        cargo = r.index("mise builds cargo tools from source, so they are off by default. They stay where they "
                        "are. To add one to config/mise.toml:\n")
        self.assertLess(npm, r.index("`pnpm` (nvm v20.11.0): dotkit has no global pnpm"))
        self.assertLess(r.index("`pnpm` (nvm v20.11.0): dotkit has no global pnpm"), cargo)
        self.assertLess(cargo, r.index("`bat` (cargo install): `mise use -g cargo:bat`"))
        self.assertEqual(r.count("### Tools that are not in mise"), 1)


if __name__ == "__main__":
    unittest.main()
