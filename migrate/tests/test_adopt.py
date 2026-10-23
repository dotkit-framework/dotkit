import unittest

from dotkit_migrate import adopt, base
from dotkit_migrate._compat import tomllib
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
        s = fake.session(h, runner).scan()
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
        h.write(".dotfiles/nix/taps.txt", "# mine\n")
        h.write(".dotfiles/nix/mas.txt", "# mine")
        h.write(".dotfiles/config/mise.toml", '[tools]\n"npm:typescript" = "latest"\n')
        h.json(".local/pipx/venvs/ruff/pipx_metadata.json",
               {"main_package": {"package": "ruff", "package_or_url": "ruff"}})
        h.json(".nvm/versions/node/v20.0.0/lib/node_modules/typescript/package.json",
               {"name": "typescript", "bin": "x"})
        runner = fake.brew_runner(
            formulae=["wget", "jq", "ripgrep", "acme/tools/thing"], casks=["raycast", "ghostty", "zed"],
            taps=[{"name": "acme/tools", "remote": "https://github.com/acme/homebrew-tools"},
                  {"name": "corp/tap", "remote": "git@git.example.com:corp/homebrew-tap.git"}],
            mas="497799835 Xcode (16.0)\n409183694 Keynote (14)\n1234567890 C# Toolkit (1.0)\n")
        s = fake.session(h, runner).scan()
        b = base.Base(fake.ROOT)
        m = adopt.missing(s, repo, b, {"taps": [], "masApps": {"Keynote": 409183694}})
        self.assertEqual(m.formulae, ["acme/tools/thing"])
        self.assertEqual(m.casks, ["zed"])
        self.assertEqual(m.taps, ["acme/tools", "corp/tap git@git.example.com:corp/homebrew-tap.git"])
        self.assertEqual(m.mas, [(497799835, "Xcode"), (1234567890, "C# Toolkit")])
        self.assertEqual(m.mise, [("pipx:ruff", "pipx")])
        text = "\n".join(adopt.describe(m))
        self.assertIn("Homebrew taps -> nix/taps.txt:\n  acme/tools\n  corp/tap git@", text)
        self.assertIn("App Store apps -> nix/mas.txt:\n  497799835 Xcode\n  1234567890 C# Toolkit\n", text)
        self.assertNotIn("jq", text, "the base has jq")
        adopt.apply(m, repo)
        self.assertEqual((repo / "nix/brews.txt").read_text(), "# mine\nwget\nacme/tools/thing\n")
        self.assertEqual((repo / "nix/casks.txt").read_text(), "raycast\nzed\n")
        self.assertEqual((repo / "nix/taps.txt").read_text(),
                         "# mine\nacme/tools\ncorp/tap git@git.example.com:corp/homebrew-tap.git\n")
        self.assertEqual((repo / "nix/mas.txt").read_text(), "# mine\n497799835 Xcode\n1234567890 C# Toolkit\n")
        tools = tomllib.loads((repo / "config/mise.toml").read_text())["tools"]
        self.assertEqual(tools, {"npm:typescript": "latest", "pipx:ruff": "latest"})
        self.assertTrue(adopt.missing(s, repo, b, {"taps": ["acme/tools", "corp/tap"], "masApps": {
            "Xcode": 497799835, "K": 409183694, "C# Toolkit": 1234567890}}).empty)


    def test_taps_and_apps_twice(self):
        """A second run adds nothing, also for an app that got a label because another app has its name."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        repo = h.home / ".dotfiles"
        for rel in ("nix/taps.txt", "nix/mas.txt"):
            h.write(f".dotfiles/{rel}", (fake.ROOT / "templates/default" / rel).read_text())
        runner = fake.brew_runner(
            taps=[{"name": "corp/tap", "remote": "git@git.example.com:corp/homebrew-tap.git"}],
            mas="409183694 Keynote (14.2)\n1234 Keynote (1.0)\n1234567890 C# Toolkit (1.0)\n")
        s = fake.session(h, runner).scan()
        b = base.Base(fake.ROOT)
        m = adopt.missing(s, repo, b, {})
        self.assertEqual(m.taps, ["corp/tap git@git.example.com:corp/homebrew-tap.git"])
        self.assertEqual(m.mas, [(409183694, "Keynote"), (1234, "Keynote (1234)"), (1234567890, "C# Toolkit")])
        self.assertEqual(m.mas_notes, ["`Keynote` (ID 1234): another app has this name, so nix/mas.txt lists it "
                                       "as `Keynote (1234)`."])
        self.assertEqual(m.mas_todos, [])
        text = "\n".join(adopt.describe(m))
        self.assertIn("App Store apps -> nix/mas.txt:\n  409183694 Keynote\n  1234 Keynote (1234)\n"
                      "  1234567890 C# Toolkit\n  `Keynote` (ID 1234): another app has this name", text)
        adopt.apply(m, repo)
        files = {rel: (repo / rel).read_text() for rel in ("nix/taps.txt", "nix/mas.txt")}
        self.assertTrue(files["nix/mas.txt"].endswith("\n409183694 Keynote\n1234 Keynote (1234)\n"
                                                      "1234567890 C# Toolkit\n"))
        # The evaluated masApps has the names of nix/mas.txt escaped.
        evaluated = {"Keynote": 409183694, "Keynote (1234)": 1234, "C\\# Toolkit": 1234567890}
        for declared in ({}, {"taps": ["corp/tap"], "masApps": evaluated}):
            again = adopt.missing(s, repo, b, declared)
            self.assertTrue(again.empty, again)
            adopt.apply(again, repo)
            self.assertEqual({rel: (repo / rel).read_text() for rel in files}, files)

    def test_a_name_written_by_hand(self):
        """A key of the evaluated masApps that is not an escaped name of nix/mas.txt was written by hand."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        repo = h.home / ".dotfiles"
        h.write(".dotfiles/nix/mas.txt", "1 C# Toolkit\n")
        s = fake.session(h, fake.brew_runner(mas="2 C# Toolkit (1.0)\n3 Back\\slash (1.0)\n4 Pages (1.0)\n")).scan()
        # "C\\# Toolkit" is the escaped line of the file; "Back\\slash" was written by hand, raw.
        m = adopt.missing(s, repo, base.Base(fake.ROOT), {"masApps": {"C\\# Toolkit": 1, "Back\\slash": 9}})
        self.assertEqual(m.mas, [(2, "C# Toolkit (2)"), (3, "Back\\slash (3)"), (4, "Pages")])



if __name__ == "__main__":
    unittest.main()
