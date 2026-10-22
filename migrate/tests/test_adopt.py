import tomllib
import unittest

from dotkit_migrate import adopt, base
from tests import fake


class AdoptTests(unittest.TestCase):
    def test_tools_that_the_repository_has_from_nix_or_mise(self):
        """The review case: brew dust and tokei, while nix/packages.txt has them."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        repo = h.home / ".dotfiles"
        h.write(".dotfiles/nix/packages.txt", "dust\ntokei\n")
        h.write(".dotfiles/config/mise.toml", '[tools]\n"github:sharkdp/hyperfine" = "latest"\nbiome = "1"\n')
        h.json(".cargo/.crates2.json", {"installs": {"tokei 12.1.2 (registry+https://x)": {},
                                                     "cargo-watch 8.5.0 (registry+https://x)": {},
                                                     "zoxide 0.9.0 (registry+https://x)": {}}})
        h.json(".nvm/versions/node/v20.0.0/lib/node_modules/@biomejs/biome/package.json",
               {"name": "@biomejs/biome", "bin": "x"})
        runner = fake.brew_runner(formulae=["dust", "tokei", "hyperfine", "wget", "gpg"])
        s = fake.scanner(h, runner).run()
        b = base.Base(fake.ROOT)
        m = adopt.missing(s, repo, b, {})
        self.assertEqual(m.formulae, ["wget"])
        self.assertEqual(m.mise, [])
        self.assertEqual(m.built, [("cargo:cargo-watch", "cargo install")], "tokei is in Nix, zoxide in the base")
        text = "\n".join(adopt.describe(m))
        self.assertIn("Use --all to add them", text)
        adopt.apply(m, repo)
        self.assertNotIn("cargo:cargo-watch", (repo / "config/mise.toml").read_text())
        m = adopt.missing(s, repo, b, {}, all_tools=True)
        self.assertEqual(m.mise, [("cargo:cargo-watch", "cargo install")])

    def test_missing_and_apply(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        repo = h.home / ".dotfiles"
        h.write(".dotfiles/nix/brews.txt", "# mine\nwget\n")
        h.write(".dotfiles/nix/casks.txt", "raycast")
        h.write(".dotfiles/config/mise.toml", '[tools]\n"npm:typescript" = "latest"\n')
        h.json(".local/pipx/venvs/ruff/pipx_metadata.json",
               {"main_package": {"package": "ruff", "package_or_url": "ruff"}})
        h.json(".nvm/versions/node/v20.0.0/lib/node_modules/typescript/package.json",
               {"name": "typescript", "bin": "x"})
        runner = fake.brew_runner(
            formulae=["wget", "jq", "ripgrep", "acme/tools/thing"], casks=["raycast", "ghostty", "zed"],
            taps=[{"name": "acme/tools", "remote": "https://github.com/acme/homebrew-tools"}],
            mas="497799835 Xcode (16.0)\n409183694 Keynote (14)\n")
        s = fake.scanner(h, runner).run()
        b = base.Base(fake.ROOT)
        m = adopt.missing(s, repo, b, {"taps": [], "masApps": {"Keynote": 409183694}})
        self.assertEqual(m.formulae, ["acme/tools/thing"])
        self.assertEqual(m.casks, ["zed"])
        self.assertEqual(m.taps, ["acme/tools"])
        self.assertEqual(m.mas, [(497799835, "Xcode")])
        self.assertEqual(m.mise, [("pipx:ruff", "pipx")])
        text = "\n".join(adopt.describe(m))
        self.assertIn('  "acme/tools"', text)
        self.assertIn("  Xcode = 497799835;", text)
        self.assertNotIn("jq", text, "the base has jq")
        adopt.apply(m, repo)
        self.assertEqual((repo / "nix/brews.txt").read_text(), "# mine\nwget\nacme/tools/thing\n")
        self.assertEqual((repo / "nix/casks.txt").read_text(), "raycast\nzed\n")
        tools = tomllib.loads((repo / "config/mise.toml").read_text())["tools"]
        self.assertEqual(tools, {"npm:typescript": "latest", "pipx:ruff": "latest"})
        self.assertTrue(adopt.missing(s, repo, b, {"taps": ["acme/tools"], "masApps": {"Xcode": 497799835,
                                                                                         "K": 409183694}}).empty)


if __name__ == "__main__":
    unittest.main()
