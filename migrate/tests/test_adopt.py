"""`dotkit migrate`: Session.adopt and writer.apply_edits on a fake Mac and a small repository."""

import contextlib
import io
import os
import unittest
from functools import cached_property
from unittest import mock

from dotkit_migrate import __main__ as cli
from dotkit_migrate import base, registry, writer
from dotkit_migrate._compat import tomllib
from dotkit_migrate.model import AdoptOptions, AdoptResult
from tests import fake


def adopt(h, runner, repo, declared=None, all_tools=False, env=None, base_root=None):
    ctx = fake.context_of(h, runner, env)
    if base_root is not None:
        ctx.base = base_root
    return registry.Session(ctx, strict=True).adopt(repo, declared or {}, AdoptOptions(all_tools=all_tools))


def files(repo):
    return {str(p.relative_to(repo)): p.read_text() for p in sorted(repo.rglob("*")) if p.is_file()}


class NoJq(base.Base):
    """The dotkit base without jq in nix/packages.txt, so that only your mise config has it."""

    @cached_property
    def packages(self):
        return [n for n in super().packages if n != "jq"]


class AdoptTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.repo = self.h.home / ".dotfiles"
        for patch in (mock.patch("socket.gethostname", return_value="test-mac.local"),):
            patch.start()
            self.addCleanup(patch.stop)

    def test_tools_that_the_repository_has_from_nix_or_mise(self):
        """The review case: brew dust and tokei, while nix/packages.txt has them."""
        h, repo = self.h, self.repo
        h.write(".dotfiles/nix/packages.txt", "dust\ntokei\n")
        h.write(".dotfiles/config/mise.toml", '[tools]\n"github:sharkdp/hyperfine" = "latest"\nbiome = "1"\n')
        h.json(".cargo/.crates2.json", {"installs": {"tokei 12.1.2 (registry+https://x)": {},
                                                     "cargo-watch 8.5.0 (registry+https://x)": {},
                                                     "zoxide 0.9.0 (registry+https://x)": {}}})
        h.json(".nvm/versions/node/v20.0.0/lib/node_modules/@biomejs/biome/package.json",
               {"name": "@biomejs/biome", "bin": "x"})
        runner = fake.brew_runner(formulae=["dust", "tokei", "hyperfine", "wget", "gpg"])
        result = adopt(h, runner, repo)
        self.assertEqual(result.files["nix/brews.txt"], "# Added by dotkit migrate on test-mac.\nwget\n")
        self.assertNotIn("config/mise.toml", result.files)
        self.assertEqual(result.output[-2:], ["cargo and Go tools, not added: mise builds them from source. Use "
                                              "--all to add them:", "  cargo:cargo-watch (from cargo install)"],
                         "tokei is in Nix, zoxide in the base")
        writer.apply_edits(result, repo)
        self.assertNotIn("cargo:cargo-watch", (repo / "config/mise.toml").read_text())
        result = adopt(h, runner, repo, all_tools=True)
        self.assertEqual(result.output, ["Tools -> config/mise.toml:", "  cargo:cargo-watch (from cargo install)"])

    def test_adopt_and_apply(self):
        h, repo = self.h, self.repo
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
        declared = {"taps": [], "masApps": {"Keynote": 409183694}}
        result = adopt(h, runner, repo, declared)
        self.assertEqual(result.output, [
            "Homebrew formulae -> nix/brews.txt:", "  acme/tools/thing",
            "Homebrew casks -> nix/casks.txt:", "  zed",
            "Homebrew taps -> nix/taps.txt:", "  acme/tools", "  corp/tap git@git.example.com:corp/homebrew-tap.git",
            "App Store apps -> nix/mas.txt:", "  497799835 Xcode", "  1234567890 C# Toolkit",
            "Tools -> config/mise.toml:", "  pipx:ruff (from pipx)",
        ], "the base has jq")
        writer.apply_edits(result, repo)
        comment = "# Added by dotkit migrate on test-mac.\n"
        self.assertEqual((repo / "nix/brews.txt").read_text(), "# mine\nwget\n" + comment + "acme/tools/thing\n")
        self.assertEqual((repo / "nix/casks.txt").read_text(), "raycast\n" + comment + "zed\n")
        self.assertEqual((repo / "nix/taps.txt").read_text(),
                         "# mine\n" + comment + "acme/tools\ncorp/tap git@git.example.com:corp/homebrew-tap.git\n")
        self.assertEqual((repo / "nix/mas.txt").read_text(),
                         "# mine\n" + comment + "497799835 Xcode\n1234567890 C# Toolkit\n")
        tools = tomllib.loads((repo / "config/mise.toml").read_text())["tools"]
        self.assertEqual(tools, {"npm:typescript": "latest", "pipx:ruff": "latest"})
        again = adopt(h, runner, repo, {"taps": ["acme/tools", "corp/tap"], "masApps": {
            "Xcode": 497799835, "K": 409183694, "C\\# Toolkit": 1234567890}})
        self.assertEqual((again.files, again.output), ({}, []))

    def test_apply_twice(self):
        """The second run adds nothing: what the first added is in the lists, and the files stay as they are."""
        h, repo = self.h, self.repo
        for rel in ("nix/brews.txt", "nix/casks.txt", "nix/taps.txt", "nix/mas.txt", "config/mise.toml"):
            h.write(f".dotfiles/{rel}", (fake.ROOT / "templates/default" / rel).read_text())
        h.json(".local/pipx/venvs/ruff/pipx_metadata.json",
               {"main_package": {"package": "ruff", "package_or_url": "ruff"}})
        h.json(".cargo/.crates2.json", {"installs": {"cargo-watch 8.5.0 (registry+https://x)": {}}})
        runner = fake.brew_runner(
            formulae=["wget", "acme/tools/thing"], casks=["zed"],
            taps=[{"name": "corp/tap", "remote": "git@git.example.com:corp/homebrew-tap.git"}],
            mas="409183694 Keynote (14.2)\n1234 Keynote (1.0)\n1234567890 C# Toolkit (1.0)\n")
        first = adopt(h, runner, repo, all_tools=True)
        self.assertEqual(sorted(first.files), ["config/mise.toml", "nix/brews.txt", "nix/casks.txt",
                                               "nix/mas.txt", "nix/taps.txt"])
        self.assertIn("App Store apps -> nix/mas.txt:\n  409183694 Keynote\n  1234 Keynote (1234)\n"
                      "  1234567890 C# Toolkit\n", "\n".join(first.output) + "\n")
        self.assertIn("App Store apps, added under another name:\n  `Keynote` (ID 1234): another app has this "
                      "name, so nix/mas.txt lists it as `Keynote (1234)`.", "\n".join(first.output))
        writer.apply_edits(first, repo)
        written = files(repo)
        self.assertTrue(written["nix/mas.txt"].endswith("\n409183694 Keynote\n1234 Keynote (1234)\n"
                                                        "1234567890 C# Toolkit\n"))
        # The evaluated masApps has the names of nix/mas.txt escaped.
        evaluated = {"Keynote": 409183694, "Keynote (1234)": 1234, "C\\# Toolkit": 1234567890}
        for declared in ({}, {"taps": ["corp/tap"], "masApps": evaluated}):
            with self.subTest(declared=declared):
                again = adopt(h, runner, repo, declared, all_tools=True)
                self.assertEqual((again.files, again.output), ({}, []))
                writer.apply_edits(again, repo)
                self.assertEqual(files(repo), written)

    def test_a_name_written_by_hand(self):
        """A key of the evaluated masApps that is not an escaped name of nix/mas.txt was written by hand."""
        h, repo = self.h, self.repo
        h.write(".dotfiles/nix/mas.txt", "1 C# Toolkit\n")
        runner = fake.brew_runner(mas="2 C# Toolkit (1.0)\n3 Back\\slash (1.0)\n4 Pages (1.0)\n")
        # "C\\# Toolkit" is the escaped line of the file. "Back\\slash" was written by hand; nix/mas.txt gives
        # the app Back\\slash the key "Back\\\\slash" (escaped), so the two do not clash.
        result = adopt(h, runner, repo, {"masApps": {"C\\# Toolkit": 1, "Back\\slash": 9}})
        self.assertTrue(result.files["nix/mas.txt"].endswith("\n2 C# Toolkit (2)\n3 Back\\slash\n4 Pages\n"))

    def test_a_name_written_by_hand_as_it_is_escaped(self):
        """homebrew.masApps."Foo \\"Pro\\"" written by hand is the key that nix/mas.txt gives `Foo "Pro"`."""
        h, repo = self.h, self.repo
        runner = fake.brew_runner(mas='2 Foo "Pro" (1.0)\n')
        result = adopt(h, runner, repo, {"masApps": {'Foo \\"Pro\\"': 1}})
        self.assertTrue(result.files["nix/mas.txt"].endswith('\n2 Foo "Pro" (2)\n'))
        result = adopt(h, runner, repo, {"masApps": {'Foo "Pro"': 1}})
        self.assertTrue(result.files["nix/mas.txt"].endswith('\n2 Foo "Pro"\n'), "another key of masApps")

    def test_a_name_and_its_label_taken(self):
        h, repo = self.h, self.repo
        h.write(".dotfiles/nix/mas.txt", "1 Pages\n")
        result = adopt(h, fake.brew_runner(mas="2 Pages (1.0)\n"), repo, {"masApps": {"Pages (2)": 7}})
        self.assertNotIn("nix/mas.txt", result.files)
        self.assertEqual(result.output, ["App Store apps, not added:",
                                         "  `Pages` (ID 2): other apps have this name and `Pages (2)`, so it is not "
                                         "in nix/mas.txt. Add it there under another name: `2 <name>`."])

    def test_your_mise_config_has_jq(self):
        """brew has jq, and your mise config has it: it is not added, in setup and in adopt."""
        h, repo = self.h, self.repo
        h.write(".config/mise/config.toml", '[tools]\njq = "latest"\n')
        h.write(".dotfiles/nix/brews.txt", "")
        runner = fake.brew_runner(formulae=["jq", "wget"])
        no_jq = NoJq(fake.ROOT)
        self.assertNotIn("jq", no_jq.packages)
        result = adopt(h, runner, repo, base_root=no_jq)
        self.assertEqual(result.files["nix/brews.txt"], "# Added by dotkit migrate on test-mac.\nwget\n")
        ctx = fake.context_of(h, runner)
        ctx.base = no_jq
        s = registry.Session(ctx, strict=True)
        self.assertNotIn("brew:jq", {i.id for p in s.pages() for i in p.items})
        plan = s.build(s.defaults(str(repo) + "-new"))
        self.assertTrue(plan.files["nix/brews.txt"].endswith("\nwget\n"))
        self.assertIn("Your mise config has these tools, so they are not in your lists. The Homebrew copy stays "
                      "installed. Remove it when nothing else needs it; brew says when another formula depends on "
                      "it.\n\n- `jq`: your mise config has jq. `brew uninstall jq`\n", plan.report)
        # Without your mise config, jq is carried and added.
        os.remove(h.home / ".config/mise/config.toml")
        self.assertIn("\njq\n", adopt(h, runner, repo, base_root=no_jq).files["nix/brews.txt"])

    def test_pnpm_is_an_npm_rule(self):
        """The pnpm package of npm is not added; a pipx package of that name is."""
        h, repo = self.h, self.repo
        h.json(".nvm/versions/node/v20.0.0/lib/node_modules/pnpm/package.json", {"name": "pnpm", "bin": "x"})
        h.json(".local/pipx/venvs/pnpm/pipx_metadata.json",
               {"main_package": {"package": "pnpm", "package_or_url": "pnpm"}})
        result = adopt(h, fake.brew_runner(), repo)
        self.assertEqual(result.output, ["Tools -> config/mise.toml:", "  pipx:pnpm (from pipx)"])

    def test_a_line_that_the_secret_check_flags(self):
        """pipx:detect-secrets and an App Store app with a token in its name: not added, with to-dos; the rest is."""
        h, repo = self.h, self.repo
        token = fake.TOKENS["@GH@"]
        h.json(".local/pipx/venvs/detect-secrets/pipx_metadata.json",
               {"main_package": {"package": "detect-secrets", "package_or_url": "detect-secrets"}})
        runner = fake.brew_runner(formulae=["wget"], mas=f"5 Notes token={token} (1.0)\n")
        result = adopt(h, runner, repo)
        self.assertEqual(sorted(result.files), ["nix/brews.txt"])
        self.assertEqual(result.output, [
            "Homebrew formulae -> nix/brews.txt:", "  wget",
            "Tools that are not in mise:",
            "  `detect-secrets` (pipx): dotkit's secret check flags its line in config/mise.toml, so it is not "
            "added. Add it by hand: `mise use -g pipx:detect-secrets`.",
            "App Store apps, not added:",
            "  The App Store app with ID 5: dotkit's secret check flags its line in nix/mas.txt, so it is not added. "
            "Add it by hand: `dotkit add mas:5`.",
        ])
        self.assertNotIn(token, "\n".join(result.output))


class CommandTests(unittest.TestCase):
    """`dotkit-migrate adopt`: the exit code, the lines, and the files."""

    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.repo = self.h.home / ".dotfiles"
        self.h.write(".dotfiles/nix/brews.txt", "")
        self.runner = fake.brew_runner(formulae=["wget"])

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["adopt", "--repo", str(self.repo), "-y", *args],
                            make_context=lambda _home, _base: fake.context_of(self.h, self.runner))
        return code, out.getvalue(), err.getvalue()

    def test_a_tool_that_the_secret_check_flags(self):
        self.h.json(".local/pipx/venvs/detect-secrets/pipx_metadata.json",
                    {"main_package": {"package": "detect-secrets", "package_or_url": "detect-secrets"}})
        code, out, err = self.main()
        self.assertEqual(code, 0, err)
        self.assertTrue((self.repo / "nix/brews.txt").read_text().endswith("\nwget\n"))
        self.assertFalse((self.repo / "config/mise.toml").exists())
        self.assertIn("Tools that are not in mise:\n  `detect-secrets` (pipx): dotkit's secret check flags its line in "
                      "config/mise.toml, so it is not added. Add it by hand: `mise use -g pipx:detect-secrets`.\n", out)

    def test_a_file_that_changed_since_the_scan(self):
        adopt_of = registry.Session.adopt

        def adopt_then_edit(session, *args):
            result = adopt_of(session, *args)
            (self.repo / "nix/brews.txt").write_text("mine\n")  # while the prompt waits
            return result

        with mock.patch.object(registry.Session, "adopt", adopt_then_edit):
            code, _, err = self.main()
        self.assertEqual(code, 2)
        self.assertEqual(err.splitlines()[-1], "dotkit migrate: nix/brews.txt changed since the scan; run dotkit "
                                               "migrate again. Nothing was changed.")
        self.assertEqual((self.repo / "nix/brews.txt").read_text(), "mine\n")

    def test_a_link_outside_the_repository(self):
        outside = self.h.write("elsewhere/brews.txt", "")
        os.remove(self.repo / "nix/brews.txt")
        os.symlink(outside, self.repo / "nix/brews.txt")
        code, _, err = self.main()
        self.assertEqual(code, 2)
        self.assertEqual(err.splitlines()[-1], f"dotkit migrate: nix/brews.txt links to {os.path.realpath(outside)}, "
                                               "outside the repository. Nothing was changed.")
        self.assertEqual(outside.read_text(), "")
        self.assertTrue((self.repo / "nix/brews.txt").is_symlink())


class ApplyEditsTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.repo = self.h.home / ".dotfiles"
        self.h.write(".dotfiles/nix/brews.txt", "wget\n")
        self.h.write(".dotfiles/nix/casks.txt", "zed\n")
        (self.repo / "nix/casks.txt").chmod(0o600)
        self.result = AdoptResult(self.repo, {"nix/brews.txt": "wget\njq\n", "nix/casks.txt": "zed\nx\n",
                                              "nix/taps.txt": "acme/tools\n"},
                                  {"nix/brews.txt": "wget\n", "nix/casks.txt": "zed\n", "nix/taps.txt": None})

    def test_all_files(self):
        writer.apply_edits(self.result, self.repo)
        self.assertEqual(files(self.repo), self.result.files)
        self.assertEqual((self.repo / "nix/casks.txt").stat().st_mode & 0o777, 0o600, "the mode stays")

    def test_a_fault_in_the_second_replace_restores_the_first_file(self):
        before = files(self.repo)
        real = os.replace
        calls = []

        def replace(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            real(src, dst)

        with mock.patch("os.replace", replace), self.assertRaises(OSError):
            writer.apply_edits(self.result, self.repo)
        self.assertEqual(len(calls), 2)
        self.assertEqual(files(self.repo), before, "the first file has its old text, and no temp file stays")

    def test_a_fault_restores_a_new_file_by_removing_it(self):
        self.result.files = {"nix/taps.txt": "acme/tools\n", "nix/brews.txt": "wget\njq\n"}
        before = files(self.repo)
        real = os.replace
        calls = []

        def replace(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            real(src, dst)

        with mock.patch("os.replace", replace), self.assertRaises(OSError):
            writer.apply_edits(self.result, self.repo)
        self.assertEqual(files(self.repo), before)

    def test_a_secret_writes_nothing(self):
        before = files(self.repo)
        self.result.files["nix/casks.txt"] = fake.fill("zed\n# token=@GH@\n")
        with self.assertRaises(writer.SecretInRepository) as e:
            writer.apply_edits(self.result, self.repo)
        self.assertIn("nix/casks.txt (line 2: ", str(e.exception))
        self.assertNotIn(fake.TOKENS["@GH@"], str(e.exception))
        self.assertEqual(files(self.repo), before)

    def test_a_secret_that_was_there_already(self):
        """dotkit migrate must not add a secret; one that the file had is not its own. Lines count as a multiset."""
        line = fake.fill("# token=@GH@\n")
        self.h.write(".dotfiles/nix/casks.txt", "zed\n" + line)
        self.result.files = {"nix/casks.txt": "zed\n" + line + "x\n"}
        self.result.before = {"nix/casks.txt": "zed\n" + line}
        writer.apply_edits(self.result, self.repo)
        self.assertEqual((self.repo / "nix/casks.txt").read_text(), "zed\n" + line + "x\n")
        # The same line once more is new.
        self.result.files = {"nix/casks.txt": "zed\n" + line + "x\n" + line}
        self.result.before = {"nix/casks.txt": "zed\n" + line + "x\n"}
        with self.assertRaises(writer.SecretInRepository):
            writer.apply_edits(self.result, self.repo)
        self.assertEqual((self.repo / "nix/casks.txt").read_text(), "zed\n" + line + "x\n")

    def test_a_file_that_changed_since_the_scan(self):
        before = files(self.repo)
        (self.repo / "nix/casks.txt").write_text("zed\nmine\n")
        before["nix/casks.txt"] = "zed\nmine\n"
        with self.assertRaisesRegex(writer.AdoptStopped, r"^nix/casks.txt changed since the scan; run dotkit "
                                                         r"migrate again$"):
            writer.apply_edits(self.result, self.repo)
        self.assertEqual(files(self.repo), before, "nothing was staged or written")

    def test_a_link_inside_the_repository(self):
        """The target gets the lines, and the link stays a link."""
        target = self.h.write(".dotfiles/lists/brews.txt", "wget\n")
        os.remove(self.repo / "nix/brews.txt")
        os.symlink("../lists/brews.txt", self.repo / "nix/brews.txt")
        writer.apply_edits(self.result, self.repo)
        self.assertTrue((self.repo / "nix/brews.txt").is_symlink())
        self.assertEqual(os.readlink(self.repo / "nix/brews.txt"), "../lists/brews.txt")
        self.assertEqual(target.read_text(), "wget\njq\n")
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["brews.txt"], "no temp file stays")

    def test_a_link_outside_the_repository(self):
        outside = self.h.write("elsewhere/brews.txt", "wget\n")
        os.remove(self.repo / "nix/brews.txt")
        os.symlink(outside, self.repo / "nix/brews.txt")
        before = files(self.repo)
        with self.assertRaisesRegex(writer.AdoptStopped, rf"^nix/brews.txt links to {os.path.realpath(outside)}, "
                                                         r"outside the repository$"):
            writer.apply_edits(self.result, self.repo)
        self.assertEqual(files(self.repo), before)
        self.assertEqual(outside.read_text(), "wget\n")


if __name__ == "__main__":
    unittest.main()
