import shutil
import subprocess
import unittest

from dotkit_migrate import adopt, secrets, writer
from dotkit_migrate._compat import tomllib
from tests import fake

TOKEN = fake.TOKENS["@GH@"]

ZSHRC = f"""\
export ZSH="$HOME/.oh-my-zsh"
source $ZSH/oh-my-zsh.sh
export EDITOR=vim
export GITHUB_TOKEN={TOKEN}
alias gs='git status'
eval "$(starship init zsh)"
if [[ -f ~/.work ]]; then
  source ~/.work
fi
"""


def used_mac(h: fake.Home) -> fake.FakeRunner:
    """A Mac with a bit of everything, like the CI runners and a person's Mac."""
    h.write(".zshrc", ZSHRC)
    h.write(".zprofile", 'eval "$(/opt/homebrew/bin/brew shellenv)"\nexport PATH="$HOME/bin:$PATH"\n')
    h.mkdir("bin")
    h.write(".bash_profile", "export BASH_THING=1\nsource ~/.bashrc\n")
    h.write(".bashrc", "export OLD_BASHRC=1\n")
    h.write(".config/ghostty/config", "font-size = 16\n")
    h.write(".config/starship.toml", 'format = "$all"\n')
    h.write(".npmrc", fake.fill("//registry.npmjs.org/:_authToken=@NPM@\n"))
    h.write(".editorconfig", (fake.ROOT / "config/editorconfig").read_text())
    h.write(".gitconfig", "[core]\n\teditor = vim\n")
    h.write(".config/mise/config.toml", '[tools]\njq = "latest"\n\n[settings]\nexperimental = true\n')
    h.write(".config/nvim/init.lua", "vim.o.number = true\n")
    h.json(".nvm/versions/node/v20.11.0/lib/node_modules/typescript/package.json",
           {"name": "typescript", "version": "5.4.0", "bin": {"tsc": "bin/tsc"}})
    h.write(".nvm/alias/default", "20\n")
    h.json(".nvm/versions/node/v20.11.0/lib/node_modules/pnpm/package.json",
           {"name": "pnpm", "version": "9.0.0", "bin": {"pnpm": "x"}})
    h.json(".local/pipx/venvs/ruff/pipx_metadata.json",
           {"main_package": {"package": "ruff", "package_version": "0.4.0", "package_or_url": "ruff"}})
    h.json(".cargo/.crates2.json", {"installs": {"bat 0.24.0 (registry+https://x)": {}}})
    h.mkdir(".oh-my-zsh")
    return fake.brew_runner(
        formulae=["wget", "ripgrep", "hashicorp/tap/terraform", "node"],
        casks=["ghostty", "raycast"],
        taps=[{"name": "homebrew/core"},
              {"name": "hashicorp/tap", "remote": "https://github.com/hashicorp/homebrew-tap"},
              {"name": "acme/tools", "remote": "git@gitlab.com:acme/homebrew-tools.git"}],
        mas="497799835 Xcode (16.0)\n1289583905 Pixelmator Pro (3.6)\n",
    )


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        runner = used_mac(self.h)
        self.scan = fake.scanner(self.h, runner).run()
        self.assertEqual(self.scan.warnings, [])
        self.p = fake.planner(self.scan)
        self.repo = str(self.h.home / ".dotfiles")

    def build(self, **changes):
        c = self.p.defaults(self.repo)
        for k, v in changes.items():
            if k == "groups" or k == "files":
                getattr(c, k).update(v)
            else:
                setattr(c, k, v)
        return self.p.build(c)

    def test_defaults(self):
        groups = {g.key: (g.default, g.items) for g in self.p.groups()}
        self.assertEqual(groups["formulae"], (True, ["wget", "hashicorp/tap/terraform"]))
        self.assertEqual(groups["casks"], (True, ["raycast"]))
        self.assertEqual(groups["taps"], (True, ["hashicorp/tap", "acme/tools"]))
        self.assertEqual(groups["mas"], (True, ["Xcode", "Pixelmator Pro"]))
        self.assertEqual(groups["npm"], (True, ["typescript"]))
        self.assertEqual(groups["pipx"], (True, ["ruff"]))
        self.assertEqual(groups["cargo"], (False, ["bat"]))
        self.assertEqual(groups["runtimes"], (False, ["node 20"]))
        self.assertTrue(groups["shell"][0])
        files = {f.target: f.default for f in self.p.file_items()}
        self.assertEqual(files, {
            ".config/ghostty/config": "layer",
            ".config/starship.toml": "replace",
            ".npmrc": "keep",
            ".editorconfig": "dotkit",
            ".gitconfig": "adopt",
            ".config/mise/config.toml": "layer",
        })
        self.assertEqual(self.p.adoptable(), [".config/nvim", ".bashrc"])

    def test_default_plan(self):
        p = self.build()
        f = p.files
        self.assertIn("\nwget\nhashicorp/tap/terraform\n", f["nix/brews.txt"])
        self.assertNotIn("ripgrep", f["nix/brews.txt"])
        self.assertTrue(f["nix/casks.txt"].endswith("\nraycast\n"))
        self.assertNotIn("nix/darwin.nix", f, "taps and App Store apps are in their list files")
        self.assertTrue(f["nix/taps.txt"].endswith(
            "\nhashicorp/tap\nacme/tools git@gitlab.com:acme/homebrew-tools.git\n"))
        self.assertTrue(f["nix/mas.txt"].endswith("\n497799835 Xcode\n1289583905 Pixelmator Pro\n"))
        self.assertTrue(f["nix/mas.txt"].startswith((fake.ROOT / "templates/default/nix/mas.txt").read_text()))
        mise = tomllib.loads(f["config/mise.toml"])
        self.assertEqual(mise["tools"], {"jq": "latest", "npm:typescript": "latest", "pipx:ruff": "latest"})
        self.assertEqual(mise["settings"], {"experimental": True})
        self.assertEqual(p.links, {
            ".zprofile.local": "config/zsh/zprofile",
            ".bash_profile.local": "config/bash/bash_profile",
            ".config/ghostty/config.local": "config/ghostty.conf",
            ".config/starship.toml": "config/starship.toml",
            ".npmrc": None,
            ".gitconfig": "config/gitconfig",
        })
        self.assertIn('    ".npmrc" = null;\n', f["nix/home.nix"])
        self.assertIn("export EDITOR=vim\nalias gs='git status'\n", f["config/zsh/zshrc"])
        self.assertIn('path=("$HOME/bin" $path)', f["config/zsh/zprofile"])
        self.assertIn("export BASH_THING=1", f["config/bash/bash_profile"])
        self.assertEqual(p.local, {"local/zshrc.zsh": f"# From ~/.zshrc.\nexport GITHUB_TOKEN={TOKEN}\n"})
        self.assertIn('source "$HOME/.dotfiles/local/zshrc.zsh"', f["config/zsh/zshrc"])
        self.assertIn("/local/", f[".gitignore"])
        self.assertEqual(p.copies["config/ghostty.conf"], self.h.home / ".config/ghostty/config")
        self.assertNotIn("nix/shell.nix", f)
        self.assertIn('name = "', f["nix/user.nix"])
        self.assertIn('dotfiles = "${home}/.dotfiles";', f["nix/user.nix"])

    def test_report(self):
        r = self.build().report
        for text in ("~/.zshrc:1 `export ZSH=", "~/.zshrc:6 `eval \"$(starship init zsh)\"`", "~/.zshrc:7-9 `if",
                     "`ripgrep`: dotkit has ripgrep (dotkit, from Nix). `brew uninstall ripgrep`",
                     "`node`: dotkit has node (dotkit, from mise). `brew uninstall node`",
                     "`ghostty`: dotkit has ghostty (dotkit cask). `brew uninstall --cask ghostty`",
                     "`GITHUB_TOKEN` -> local/zshrc.zsh", "~/.npmrc stays in place",
                     "`npm:typescript`, was 5.4.0 in nvm v20.11.0. Old copy: `npm uninstall -g typescript`",
                     "`pnpm` (nvm v20.11.0): dotkit has no global pnpm", "`mise use -g cargo:bat`",
                     "nvm (~/.nvm)", "`mise use -g node@20`", "Oh My Zsh: `rm -rf ~/.oh-my-zsh`",
                     "~/.bashrc: It stays in place", "~/.config/nvim", "`acme/tools`: git@gitlab.com"):
            self.assertIn(text, r)

    def test_secrets_intro_is_not_masked(self):
        r = self.build().report
        self.assertIn("These lines have a secret, so they are not in Git. They are in a file in local/ of this "
                      "repository, which Git ignores, and the repository file reads it. On a new Mac, copy the "
                      "file by hand, or keep its lines in a manager for passwords.\n", r)

    def test_old_taps_keep_their_intro(self):
        runner = fake.brew_runner(taps=[
            {"name": "acme/tools", "remote": "git@gitlab.com:acme/homebrew-tools.git"},
            {"name": "homebrew/cask-fonts"},
        ])
        p = fake.planner(fake.scanner(self.h, runner).run())
        r = p.build(p.defaults(self.repo)).report
        custom = r.index("These taps are not on GitHub, so the list has their URL. Check it.")
        merged = r.index("Homebrew merged these taps into its own. Remove them.")
        self.assertLess(custom, r.index("`acme/tools`: git@gitlab.com"))
        self.assertLess(r.index("`acme/tools`: git@gitlab.com"), merged)
        self.assertLess(merged, r.index("`homebrew/cask-fonts`: `brew untap homebrew/cask-fonts`"))
        self.assertEqual(r.count("### Homebrew taps"), 1)

    def test_tools_not_in_mise_keep_their_intros(self):
        # Default choices: npm is on, but pnpm is never carried; cargo is off.
        r = self.build().report
        npm = r.index("They stay where they are. To add one to config/mise.toml:\n")
        cargo = r.index("mise builds cargo tools from source, so they are off by default. They stay where they "
                        "are. To add one to config/mise.toml:\n")
        self.assertLess(npm, r.index("`pnpm` (nvm v20.11.0): dotkit has no global pnpm"))
        self.assertLess(r.index("`pnpm` (nvm v20.11.0): dotkit has no global pnpm"), cargo)
        self.assertLess(cargo, r.index("`bat` (cargo install): `mise use -g cargo:bat`"))
        self.assertEqual(r.count("### Tools that are not in mise"), 1)

    def test_no_secret_in_the_repository(self):
        p = self.build()
        for rel, text in p.files.items():
            self.assertEqual(secrets.scan_text(text), [], rel)
        for rel, src in p.copies.items():
            self.assertEqual(secrets.scan_file(src), [], rel)
        self.assertTrue(all(rel.startswith("local/") for rel in p.local))

    def test_review_secrets_end_to_end(self):
        alias = "alias k='kubectl --token=abcdef0123456789abcdef'"
        pwd = "export MYSQL_PWD=hunter2hunter2"
        function = ('request() {\n  curl -H "X-Api-Key: ' + "\\\n" +
                    'hunter2hunter2" https://example.com\n}')
        continued_alias = ("alias request='" + 'curl -H "X-Api-Key: ' + "\\\n" +
                           'hunter2hunter2" https://example.com' + "'")
        heredoc = 'cat <<EOF\npassword hunter2hunter2\nEOF'
        self.h.write(".zshrc", "\n".join((alias, pwd, function, continued_alias, heredoc,
                                         'export AWS_PROFILE=MyCompanyProd2024Admin')) + "\n")
        self.h.write(".config/msmtp/config", "account default\npassword hunter2hunter2\n")
        p = fake.planner(fake.scanner(self.h, fake.brew_runner()).run())
        self.assertNotIn(".config/msmtp", p.adoptable())
        c = p.defaults(self.repo)
        c.adopt.add(".config/msmtp")
        result = p.build(c)
        local = result.local["local/zshrc.zsh"]
        for statement in (alias, pwd, function, continued_alias):
            self.assertIn(statement, local)
        for text in result.files.values():
            self.assertNotIn("hunter2", text)
            self.assertNotIn("abcdef0123456789abcdef", text)
        self.assertIn("MyCompanyProd2024Admin", result.files["config/zsh/zshrc"])
        self.assertNotIn("config/msmtp", result.copies)
        self.assertIn("not shown: it has a secret", result.report)
        self.assertEqual(subprocess.run(["/bin/zsh", "-n", "-c", local], capture_output=True).returncode, 0)

    def test_sshpass_function_stays_in_ignored_local_file(self):
        reference = 'sshpass -p "$FIRST_PASS" ssh host1 true'
        literal = 'sshpass -p hunter2hunter2 ssh host2 true'
        for i, calls in enumerate(((reference, literal), (literal, reference))):
            with self.subTest(calls=calls):
                function = 'deploy() { ' + ' && '.join(calls) + '; }'
                self.h.write(".zshrc", function + "\nexport EDITOR=vim\n")
                p = fake.planner(fake.scanner(self.h, fake.brew_runner()).run())
                c = p.defaults(str(self.h.home / f"repo-{i}"))
                result = p.build(c)
                self.assertIn("local/zshrc.zsh", result.local)
                self.assertEqual(result.local["local/zshrc.zsh"], "# From ~/.zshrc.\n" + function + "\n")
                self.assertIn("export EDITOR=vim", result.files["config/zsh/zshrc"])
                for text in result.files.values():
                    self.assertNotIn("deploy()", text)
                    self.assertNotIn("hunter2", text)
                self.assertNotIn("hunter2", result.report)
                writer.write_new(result, fake.ROOT / "templates/default")
                ignored = subprocess.run(
                    ["git", "-C", str(result.repo), "check-ignore", "local/zshrc.zsh"],
                    capture_output=True, text=True, check=True)
                self.assertEqual(ignored.stdout.strip(), "local/zshrc.zsh")
                self.assertIn(function, (result.repo / "local/zshrc.zsh").read_text())

    def test_compact_json_credentials_cannot_be_adopted(self):
        configs = {
            "token": '{"token":"hunter2hunter2"}',
            "neighbor": '{"commit":"@HEX40@","token":"hunter2hunter2"}',
            "reverse": '{"token":"hunter2hunter2","revision":"@HEX40@"}',
        }
        for name, text in configs.items():
            self.h.write(f".config/{name}/config.json", fake.fill(text) + "\n")
        self.h.write(".config/clean/lazy-lock.json", fake.fill('{"lazy.nvim":{"commit":"@HEX40@"}}\n'))
        p = fake.planner(fake.scanner(self.h, fake.brew_runner()).run())
        for name in configs:
            with self.subTest(name=name):
                self.assertNotIn(f".config/{name}", p.adoptable())
        self.assertIn(".config/clean", p.adoptable())
        c = p.defaults(self.repo)
        # A forced choice must not bypass the assessment or the final guard.
        c.adopt = {f".config/{name}" for name in configs} | {".config/clean"}
        result = p.build(c)
        for name in configs:
            self.assertNotIn(f"config/{name}", result.copies)
            self.assertNotIn(f".config/{name}", result.links)
        self.assertIn("config/clean", result.copies)
        self.assertNotIn("hunter2", result.report)
        for text in result.files.values():
            self.assertNotIn("hunter2", text)
        writer.write_new(result, fake.ROOT / "templates/default")
        for name in configs:
            self.assertFalse((result.repo / "config" / name).exists())
        self.assertIn(fake.TOKENS["@HEX40@"], (result.repo / "config/clean/lazy-lock.json").read_text())

    def test_cargo_git_revision_report_survives(self):
        source = fake.fill("git+https://github.com/x/tool#@HEX40@")
        self.h.json(".cargo/.crates2.json", {"installs": {f"tool 0.1.0 ({source})": {}}})
        p = fake.planner(fake.scanner(self.h, fake.brew_runner()).run())
        result = p.build(p.defaults(self.repo))
        self.assertIn(source, result.report)

    def test_groups_off(self):
        off = {k: False for k in ("formulae", "casks", "taps", "mas", "npm", "pipx", "shell")}
        p = self.build(groups=off)
        self.assertNotIn("nix/brews.txt", p.files)
        self.assertNotIn("nix/casks.txt", p.files)
        self.assertNotIn("nix/taps.txt", p.files)
        self.assertNotIn("nix/mas.txt", p.files)
        self.assertEqual(tomllib.loads(p.files["config/mise.toml"])["tools"], {"jq": "latest"})
        self.assertNotIn("export EDITOR", p.files["config/zsh/zshrc"])
        self.assertIn("`wget`: `dotkit add brew:wget`", p.report)
        self.assertIn("`typescript` (nvm v20.11.0): `mise use -g npm:typescript`", p.report)
        self.assertIn("You chose not to carry shell lines", p.report)
        self.assertEqual(p.local, {})
        self.assertIn("`GITHUB_TOKEN` has a secret. You chose not to carry shell lines", p.report)
        self.assertIn("`acme/tools git@gitlab.com:acme/homebrew-tools.git`", p.report)
        self.assertIn("`Xcode`: `dotkit add mas:497799835`", p.report)

    def test_taps_and_apps_that_cannot_go_into_the_lists(self):
        runner = fake.brew_runner(taps=[
            {"name": "ok/tap", "remote": "https://github.com/ok/homebrew-tap"},
            {"name": "acme/space", "remote": "https://example.com/a b.git"},
            {"name": "acme/hash", "remote": "https://example.com/a.git#main"},
        ], mas="409183694 Keynote (14.2)\n1234 Keynote (1.0)\n497799835 Xcode (16.0)\n")
        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = fake.planner(fake.scanner(h, runner).run())
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertTrue(result.files["nix/taps.txt"].endswith(", see MIGRATION.md.\nok/tap\n"))
        self.assertNotIn("acme/", result.files["nix/taps.txt"])
        for name, url, why in (("acme/space", "https://example.com/a b.git", "it has a space"),
                               ("acme/hash", "https://example.com/a.git#main", "it has a `#`")):
            self.assertIn(f"- `{name}`: its URL cannot go into nix/taps.txt ({why}). Add it by hand to "
                          f"`homebrew.taps` in nix/darwin.nix as "
                          f'`{{ name = "{name}"; clone_target = "{url}"; }}`.\n', result.report)
        # Two apps with one name: the second gets a label.
        self.assertTrue(result.files["nix/mas.txt"].endswith(
            ", see MIGRATION.md.\n409183694 Keynote\n1234 Keynote (1234)\n497799835 Xcode\n"))
        self.assertIn("- `Keynote` (ID 1234): another app has this name, so nix/mas.txt lists it as "
                      "`Keynote (1234)`.\n", result.report)
        self.assertIn("1 Homebrew tap -> nix/taps.txt", result.summary)
        self.assertIn("3 App Store apps -> nix/mas.txt", result.summary)

    def test_a_tap_url_with_a_secret_is_not_shown(self):
        url = fake.fill("https://me:@GH@@git.example.com/homebrew tap.git")
        runner = fake.brew_runner(taps=[{"name": "corp/tap", "remote": url}])
        h = fake.Home()
        self.addCleanup(h.cleanup)
        s = fake.scanner(h, runner).run()
        p = fake.planner(s)
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertNotIn("nix/taps.txt", result.files)
        self.assertIn("- `corp/tap`: its URL has a secret (", result.report)
        self.assertIn("), so it is not in nix/taps.txt. Take the credential out of the URL (for example with an "
                      "SSH URL or a git credential helper), then add the tap to nix/taps.txt. Do not put the URL "
                      "with the credential into a file that Git tracks.\n", result.report)
        m = adopt.missing(s, h.home / ".dotfiles", fake.base.Base(fake.ROOT), {})
        out = "\n".join(adopt.describe(m))
        self.assertIn("`corp/tap`: its URL has a secret", out)
        for text in (result.report, "\n".join(result.summary), out):
            self.assertNotIn(fake.TOKENS["@GH@"], text)
            self.assertNotIn("git.example.com", text)

    def test_a_tap_name_with_a_space_is_not_written(self):
        """The name rule comes first: `a/b c` is not the tap a/b with the URL c."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        runner = fake.brew_runner(taps=[{"name": "a/b c"},
                                        {"name": "ok/tap", "remote": "https://github.com/ok/homebrew-tap"}])
        s = fake.scanner(h, runner).run()
        self.assertFalse(s.taps[0].custom)
        p = fake.planner(s)
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertTrue(result.files["nix/taps.txt"].endswith(", see MIGRATION.md.\nok/tap\n"))
        self.assertIn("- `a/b c`: it cannot go into nix/taps.txt: its name is not owner/repo of letters, digits, "
                      "`_`, `.`, and `-`.\n", result.report)
        m = adopt.missing(s, h.home / ".dotfiles", fake.base.Base(fake.ROOT), {})
        self.assertEqual(m.taps, ["ok/tap"])

    def test_a_mise_config_that_is_not_carried_does_not_hide_tools(self):
        """A mise config with a secret stays in place, also when the choice says layer, so its tools are added."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/mise/config.toml",
                fake.fill('[tools]\n"npm:typescript" = "latest"\n\n[env]\nGITHUB_TOKEN = "@GH@"\n'))
        h.json(".nvm/versions/node/v20.11.0/lib/node_modules/typescript/package.json",
               {"name": "typescript", "version": "5.4.0", "bin": {"tsc": "bin/tsc"}})
        p = fake.planner(fake.scanner(h, fake.brew_runner()).run())
        c = p.defaults(str(h.home / ".dotfiles"))
        self.assertEqual(c.files[".config/mise/config.toml"], "keep")
        c.files[".config/mise/config.toml"] = "layer"
        result = p.build(c)
        self.assertEqual(tomllib.loads(result.files["config/mise.toml"])["tools"], {"npm:typescript": "latest"})
        self.assertIsNone(result.links[".config/mise/config.toml"])
        self.assertNotIn(fake.TOKENS["@GH@"], result.files["config/mise.toml"])

    def test_other_config_whose_place_is_taken(self):
        """A copy goes only to a free repository path; the other file stays in place, with a to-do."""
        cases = {
            "an earlier copy": ({".npmrc": "prefix=${HOME}/.local\n", ".config/npmrc": "color=false\n"},
                                {".npmrc": "replace"}, ".config/npmrc", "config/npmrc", "~/.npmrc"),
            "a merged file": ({".config/git/config": "[pull]\n\trebase = true\n", ".gitconfig": "[core]\n",
                               ".config/gitconfig": "[x]\n"},
                              {}, ".config/gitconfig", "config/gitconfig", "~/.config/git/config and ~/.gitconfig"),
        }
        for why, (files, choices, rel, dest, has) in cases.items():
            with self.subTest(why):
                h = fake.Home()
                self.addCleanup(h.cleanup)
                for name, text in files.items():
                    h.write(name, text)
                p = fake.planner(fake.scanner(h, fake.brew_runner()).run())
                self.assertIn(rel, p.adoptable())
                c = p.defaults(str(h.home / ".dotfiles"))
                c.files.update(choices)
                c.adopt.add(rel)
                result = p.build(c)
                self.assertNotIn(rel, result.links)
                if why == "an earlier copy":
                    self.assertEqual(result.copies[dest], h.home / ".npmrc")
                    self.assertEqual(result.links[".npmrc"], dest)
                else:
                    self.assertNotIn(dest, result.copies)
                    self.assertTrue(result.files[dest].startswith("# From ~/.config/git/config.\n"))
                    self.assertEqual(result.links[".gitconfig"], dest)
                self.assertIn(f"- ~/{rel}: its place in the repository, {dest}, has {has} already. It stays in "
                              "place. To keep it in the repository, copy it under another name and link it in "
                              "nix/home.nix.\n", result.report)
                self.assertNotIn(f"~/{rel} -> {dest}", result.report)

    def test_other_config_that_would_contain_a_document_path(self):
        """~/.config/bash would go to config/bash, which holds config/bash/bash_profile: it stays in place."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".bash_profile", "export BASH_THING=1\n")
        h.write(".config/bash/aliases", "alias ll='ls -la'\n")
        h.write(".config/bash2/aliases", "alias la='ls -A'\n")
        p = fake.planner(fake.scanner(h, fake.brew_runner()).run())
        self.assertIn(".config/bash", p.adoptable())
        c = p.defaults(str(h.home / ".dotfiles"))
        c.adopt |= {".config/bash", ".config/bash2"}
        result = p.build(c)
        self.assertIn("export BASH_THING=1", result.files["config/bash/bash_profile"])
        self.assertNotIn("config/bash", result.copies)
        self.assertNotIn(".config/bash", result.links)
        self.assertIn("- ~/.config/bash: its place in the repository, config/bash, would contain "
                      "config/bash/bash_profile; it stays in place. To keep it in the repository, copy it under "
                      "another name and link it in nix/home.nix.\n", result.report)
        self.assertNotIn("~/.config/bash -> config/bash\n", result.report)
        # Path parts, not string prefixes: config/bash2 is not inside config/bash.
        self.assertEqual(result.copies["config/bash2"], h.home / ".config/bash2")
        self.assertEqual(result.links[".config/bash2"], "config/bash2")

    def test_other_config_inside_a_copied_directory(self):
        """~/.config/home goes to config/home first, so ~/.vimrc has no place at config/home/vimrc."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/home/settings", "x = 1\n")
        h.write(".vimrc", "set number\n")
        p = fake.planner(fake.scanner(h, fake.brew_runner()).run())
        self.assertEqual(p.adoptable(), [".config/home", ".vimrc"])
        c = p.defaults(str(h.home / ".dotfiles"))
        c.adopt |= {".config/home", ".vimrc"}
        result = p.build(c)
        self.assertEqual(result.copies, {"config/home": h.home / ".config/home"})
        self.assertNotIn(".vimrc", result.links)
        self.assertIn("- ~/.vimrc: its place in the repository, config/home/vimrc, is inside config/home, which "
                      "has ~/.config/home already. It stays in place. To keep it in the repository, copy it under "
                      "another name and link it in nix/home.nix.\n", result.report)

    def test_other_config_paths_compare_like_apfs(self):
        """Without case: ~/.config/Zsh would contain config/zsh/...; ~/.config/Home holds config/home/vimrc."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/Zsh/aliases.zsh", "alias ll='ls -la'\n")
        h.write(".config/Home/settings", "x = 1\n")
        h.write(".vimrc", "set number\n")
        p = fake.planner(fake.scanner(h, fake.brew_runner()).run())
        self.assertEqual(p.adoptable(), [".config/Home", ".config/Zsh", ".vimrc"])
        c = p.defaults(str(h.home / ".dotfiles"))
        c.adopt |= set(p.adoptable())
        result = p.build(c)
        self.assertEqual(result.copies, {"config/Home": h.home / ".config/Home"})
        self.assertEqual({t: s for t, s in result.links.items() if s and s.startswith("config/")},
                         {".config/Home": "config/Home"})
        self.assertIn("- ~/.config/Zsh: its place in the repository, config/Zsh, would contain "
                      "config/zsh/zprofile; it stays in place. To keep it in the repository, copy it under "
                      "another name and link it in nix/home.nix.\n", result.report)
        self.assertIn("- ~/.vimrc: its place in the repository, config/home/vimrc, is inside config/Home, which "
                      "has ~/.config/Home already. It stays in place. To keep it in the repository, copy it under "
                      "another name and link it in nix/home.nix.\n", result.report)
        writer.write_new(result, fake.ROOT / "templates/default")
        self.assertEqual((result.repo / "config/zsh/zshrc").read_text(),
                         (fake.ROOT / "templates/default/config/zsh/zshrc").read_text())
        self.assertEqual(sorted(x.name for x in (result.repo / "config").iterdir()), ["Home", "mise.toml", "zsh"])

    def test_runtimes_and_cargo_on(self):
        p = self.build(groups={"runtimes": True, "cargo": True})
        tools = tomllib.loads(p.files["config/mise.toml"])["tools"]
        self.assertEqual(tools["node"], "20")
        self.assertEqual(tools["cargo:bat"], "latest")

    def test_file_choices(self):
        p = self.build(files={".config/ghostty/config": "replace", ".config/starship.toml": "dotkit",
                              ".npmrc": "keep", ".gitconfig": "keep", ".config/mise/config.toml": "keep"},
                       keymap="vi")
        self.assertEqual(p.links[".config/ghostty/config"], "config/ghostty.conf")
        self.assertNotIn(".config/starship.toml", p.links)
        self.assertNotIn(".gitconfig", p.links)
        self.assertIsNone(p.links[".config/mise/config.toml"])
        self.assertIn("~/.config/starship.toml is replaced by the dotkit file", p.report)
        self.assertIn('    keymap = "vi";', p.files["nix/shell.nix"])

    def test_a_file_with_a_secret_cannot_go_into_the_repository(self):
        npmrc = next(f for f in self.p.file_items() if f.target == ".npmrc")
        self.assertEqual(npmrc.options, ["keep", "dotkit"])
        # Also when a choice says so: the build keeps the file in place.
        p = self.build(files={".npmrc": "replace"})
        self.assertIsNone(p.links[".npmrc"])
        self.assertNotIn("config/npmrc", p.copies)

    def test_adopt_other_config(self):
        c = self.p.defaults(self.repo)
        c.adopt = {".config/nvim", ".bashrc"}
        p = self.p.build(c)
        self.assertEqual(p.links[".config/nvim"], "config/nvim")
        self.assertEqual(p.links[".bashrc"], "config/home/bashrc")
        self.assertEqual(p.copies["config/nvim"], self.h.home / ".config/nvim")

    def test_failed_syntax_check_reports_the_lines(self):
        self.h.write(".zshrc", "f() {\n  echo hi\n}\nexport A=1\n")
        s = fake.scanner(self.h, fake.brew_runner()).run()
        p = fake.planner(s, lambda shell, text: "f()" not in text).build(
            fake.planner(s).defaults(self.repo))
        self.assertIn("export A=1", p.files["config/zsh/zshrc"])
        self.assertNotIn("echo hi", p.files["config/zsh/zshrc"])
        self.assertIn("zsh -n could not check it", p.report)

    def test_empty_mac(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        s = fake.scanner(h, fake.FakeRunner()).run()
        p = fake.planner(s)
        self.assertEqual(p.groups(), [])
        self.assertEqual(p.file_items(), [])
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertEqual(sorted(result.files), ["MIGRATION.md", "nix/user.nix"])
        self.assertIn("Nothing.", result.report)

    def test_repo_path_with_a_space(self):
        p = self.build(repo=str(self.h.home / "my dots"))
        self.assertIn('  dotfiles = "${home}/my dots";\n', p.files["nix/user.nix"])
        self.assertIn('source "$HOME/my dots/local/zshrc.zsh"', p.files["config/zsh/zshrc"])


class NixFileTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("nix-instantiate"), "no nix-instantiate")
    def test_nix_files_parse(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        s = fake.scanner(h, used_mac(h)).run()
        p = fake.planner(s)
        result = p.build(p.defaults(str(h.home / "my dots")))
        for rel in ("nix/home.nix", "nix/user.nix"):
            out = subprocess.run(["nix-instantiate", "--parse", "-E", result.files[rel]], capture_output=True,
                                 text=True)
            self.assertEqual(out.returncode, 0, f"{rel}: {out.stderr}")
        nixfmt = shutil.which("nixfmt")
        if nixfmt:
            for rel in ("nix/home.nix", "nix/user.nix"):
                out = subprocess.run([nixfmt, "--check"], input=result.files[rel], capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, f"{rel} is not nixfmt style: {out.stderr}")


if __name__ == "__main__":
    unittest.main()
