import os
import shutil
import stat
import subprocess
import unittest

from dotkit_migrate import adopt, plan, secrets, write
from dotkit_migrate._compat import tomllib
from dotkit_migrate.report import Report
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

    def test_report_deduplicates_per_block(self):
        r = Report()
        r.todo("Homebrew taps", "First intro.", ["`x`"])
        r.todo("Homebrew taps", "Second intro.", ["`x`"])
        r.todo("Homebrew taps", "First intro.", ["`x`", "`x`"])
        text = r.render(self.scan, "~/.dotfiles")
        self.assertIn("First intro.\n\n- `x`\n\nSecond intro.\n\n- `x`\n", text)
        self.assertEqual(text.count("- `x`"), 2)
        self.assertEqual(r.count, 2)
        r.todo("Logins", "Nothing to list.", [])
        self.assertEqual(r.count, 3)
        # Repeats in one call collapse too.
        r.todo("Config files", "One intro.", ["`y`", "`y`"])
        self.assertEqual(r.sections["Config files"].items, ["`y`"])

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
                write.write(result, fake.ROOT / "templates/default")
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
        write.write(result, fake.ROOT / "templates/default")
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

    def test_guard(self):
        """A copy or a generated line with a secret is dropped, whatever put it there."""
        self.h.write(".config/tool/config", fake.fill("token = @GH@\n"))
        c = self.p.defaults(self.repo)
        b = plan._Build(self.p, c)
        b.plan.copies["config/tool"] = self.h.home / ".config/tool"
        b.plan.links[".config/tool"] = "config/tool"
        os.mkfifo(self.h.mkdir(".config/pipes") / "fifo")
        b.plan.copies["config/pipes"] = self.h.home / ".config/pipes"
        b.mise_text = "# x\n[tools]\n"
        b.mise_lines = [fake.fill('"npm:x" = "latest" # @GH@')]
        # Lines that an earlier rule might miss: the last check catches them on its own.
        b.plan.files["config/zsh/zshrc"] = fake.fill(
            "export EDITOR=vim\n"
            "export WEIRD_NAME=@RANDOM@\n"
            "alias gh2='GITHUB_TOKEN=abcdef123456 gh'\n"
            "  curl -u admin:hunter2 https://example.com\n"
            "# @HEX40@\n")
        b.render()
        zshrc = b.plan.files["config/zsh/zshrc"]
        self.assertTrue(zshrc.startswith("export EDITOR=vim\n"))
        for value in (fake.TOKENS["@RANDOM@"], "abcdef123456", "hunter2", fake.TOKENS["@HEX40@"]):
            self.assertNotIn(value, zshrc)
        self.assertNotIn("config/tool", b.plan.copies)
        self.assertNotIn("config/pipes", b.plan.copies, "a file that cannot be checked counts as a secret")
        self.assertNotIn(".config/tool", b.plan.links)
        self.assertNotIn(fake.TOKENS["@GH@"], b.plan.files.get("config/mise.toml", ""))
        self.assertIn("has a secret, so it is not in the repository", b.plan.report)

    def test_final_guard_keeps_secret_shell_statements_whole(self):
        b = plan._Build(self.p, self.p.defaults(self.repo))
        function = ('request() {\n  echo before\n  curl -H "X-Api-Key: ' + "\\\n" +
                    'hunter2hunter2" https://example.com\n  echo after\n}')
        b.plan.files["config/zsh/zshrc"] = function + "\nexport EDITOR=vim\n"
        b.mise_text = "[tools]\n"
        b.render()
        tracked = b.plan.files["config/zsh/zshrc"]
        self.assertEqual(b.plan.local["local/zshrc.zsh"], function + "\n")
        self.assertNotIn("request()", tracked)
        self.assertNotIn("echo before", tracked)
        self.assertNotIn("echo after", tracked)
        self.assertIn("export EDITOR=vim", tracked)
        self.assertIn("/local/", b.plan.files[".gitignore"])
        for text in (tracked, b.plan.local["local/zshrc.zsh"]):
            for shell in ("/bin/zsh", "/bin/bash"):
                self.assertEqual(subprocess.run([shell, "-n", "-c", text], capture_output=True).returncode, 0)
        self.assertNotIn("hunter2", b.plan.report)

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

    def test_summary(self):
        summary = "\n".join(self.build().summary)
        self.assertIn("Renamed to <name>.before-dotkit by the first apply: ~/.bash_profile, ~/.config/ghostty/config,", summary)
        self.assertNotIn("~/.npmrc,", summary.split("Renamed")[1])
        self.assertIn("Stay in place, not linked: ~/.npmrc", summary)

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

    def test_repo_error(self):
        home = str(self.h.home)
        self.assertIsNone(plan.repo_error(f"{home}/.dotfiles", home))
        self.assertIn("exists", plan.repo_error(f"{home}/.config", home))
        self.assertIn("Use a directory in", plan.repo_error("/tmp/x", home))
        self.assertIn("does not exist", plan.repo_error(f"{home}/a/b", home))
        for bad in ('~/dot "files', "~/a$b", "~/a`b", "~/a\\b", "~/a\nb"):
            self.assertIn("Use a path without", plan.repo_error(bad, home), bad)
        self.assertIsNone(plan.repo_error("~/my dots", home))

    def test_repo_path_with_a_space(self):
        p = self.build(repo=str(self.h.home / "my dots"))
        self.assertIn('  dotfiles = "${home}/my dots";\n', p.files["nix/user.nix"])
        self.assertIn('source "$HOME/my dots/local/zshrc.zsh"', p.files["config/zsh/zshrc"])

    def test_insert_tools(self):
        self.assertEqual(plan.insert_tools("[tools]\n", ['"a" = "1"']), '[tools]\n"a" = "1"\n')
        self.assertEqual(plan.insert_tools('[tools]\nx = "1"\n\n[settings]\ny = 1\n', ['"a" = "1"']),
                         '[tools]\nx = "1"\n"a" = "1"\n\n[settings]\ny = 1\n')
        self.assertEqual(plan.insert_tools("[env]\nA = '1'\n", ['"a" = "1"']),
                         "[env]\nA = '1'\n\n[tools]\n\"a\" = \"1\"\n")


class WriteTests(unittest.TestCase):
    def test_write(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        s = fake.scanner(h, used_mac(h)).run()
        p = fake.planner(s)
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        write.write(result, fake.ROOT / "templates/default")
        repo = h.home / ".dotfiles"
        self.assertTrue((repo / ".git").is_dir())
        self.assertFalse((repo.parent / ".dotfiles.partial").exists())
        self.assertTrue(os.access(repo / "dotkit", os.X_OK))
        self.assertEqual((repo / "config/ghostty.conf").read_text(), "font-size = 16\n")
        self.assertTrue((repo / "MIGRATION.md").read_text().startswith("# Migration from"))
        mode = stat.S_IMODE((repo / "local/zshrc.zsh").stat().st_mode)
        self.assertEqual(mode, 0o600)
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
                                capture_output=True, text=True, check=True).stdout
        self.assertNotIn("local/", status, "Git ignores the secrets")
        self.assertIn("MIGRATION.md", status)

    def test_nested_finder_metadata_is_not_copied(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".config/nvim/init.lua", "vim.o.number = true\n")
        h.json(".config/nvim/lazy-lock.json", {"lazy.nvim": {"commit": fake.TOKENS["@HEX40@"]}})
        h.write(".config/nvim/nested/settings.lua", "return {}\n")
        for rel in (".config/nvim/.DS_Store", ".config/nvim/nested/.DS_Store"):
            h.write(rel).write_bytes(b"metadata\0binary")
        p = fake.planner(fake.scanner(h, fake.brew_runner()).run())
        self.assertIn(".config/nvim", p.adoptable())
        c = p.defaults(str(h.home / ".dotfiles"))
        c.adopt.add(".config/nvim")
        result = p.build(c)
        self.assertIn("config/nvim", result.copies, "the final guard agrees with assessment")
        write.write(result, fake.ROOT / "templates/default")
        copied = result.repo / "config/nvim"
        self.assertEqual(list(copied.rglob(".DS_Store")), [])
        self.assertTrue((copied / "nested/settings.lua").is_file())
        self.assertIn(fake.TOKENS["@HEX40@"], (copied / "lazy-lock.json").read_text())

    def test_links_in_the_way(self):
        """A link of another dotfiles manager is listed for setup, which renames it."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        target = h.write("old-dotfiles/zshrc", "export STOW=1\n")
        os.symlink(target, h.home / ".zshrc")
        os.symlink("/nix/store/abc-home-manager-files/.zprofile", h.home / ".zprofile")
        s = fake.scanner(h, fake.brew_runner()).run()
        p = fake.planner(s)
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertEqual(result.move_links, [".zshrc"])
        self.assertIn("export STOW=1", result.files["config/zsh/zshrc"])
        self.assertIn("~/.zshrc is a link to ~/old-dotfiles/zshrc", result.report)
        write.write(result, fake.ROOT / "templates/default")
        self.assertEqual(os.readlink(h.home / ".zshrc"), str(target), "setup renames it later, not write")

    def test_folded_stow_directories(self):
        """A linked directory above a target is moved aside; its other files are listed."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        stow = h.write("dotfiles/ghostty/config", "font-size = 16\n").parent
        h.write("dotfiles/ghostty/themes/mine", "background = 000000\n")
        h.mkdir(".config")
        os.symlink(stow, h.home / ".config/ghostty")
        git = h.write("dotfiles/git/config", "[core]\n\teditor = vim\n").parent
        h.write("dotfiles/git/ignore", ".DS_Store\n")
        os.symlink(git, h.home / ".config/git")
        s = fake.scanner(h, fake.brew_runner()).run()
        p = fake.planner(s)
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertEqual(result.move_links, [".config/ghostty", ".config/git"])
        self.assertEqual(result.blockers, [])
        self.assertIn("~/.config/ghostty is a link to ~/dotfiles/ghostty", result.report)
        self.assertIn("no longer at ~/.config/ghostty: themes/mine", result.report)
        self.assertEqual(result.copies["config/ghostty.conf"], h.home / ".config/ghostty/config")
        self.assertNotIn("~/.config/ghostty/config,", "\n".join(result.summary).split("Links of yours")[0])
        write.write(result, fake.ROOT / "templates/default")
        listed = (h.home / ".dotfiles/.build/links-in-way").read_text()
        self.assertEqual(listed, ".config/ghostty\n.config/git\n")

    def test_linked_config_directory_stops(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        target = h.mkdir("dotfiles/config")
        os.symlink(target, h.home / ".config")
        s = fake.scanner(h, fake.brew_runner()).run()
        p = fake.planner(s)
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertEqual(len(result.blockers), 1)
        self.assertIn("~/.config is a link to ~/dotfiles/config", result.blockers[0])
        self.assertEqual(result.move_links, [])
        self.assertTrue(result.summary[0].startswith("Setup cannot continue"))

    def test_write_leaves_other_directories_alone(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        s = fake.scanner(h, used_mac(h)).run()
        p = fake.planner(s)
        result = p.build(p.defaults(str(h.home / "code")))
        other = h.write("code.partial/keep.txt", "mine\n")
        write.write(result, fake.ROOT / "templates/default")
        self.assertEqual(other.read_text(), "mine\n")
        self.assertEqual(sorted(x.name for x in h.home.iterdir() if x.name.startswith("code")),
                         ["code", "code.partial"])
        with self.assertRaises(FileExistsError):
            write.write(result, fake.ROOT / "templates/default")
        self.assertEqual(stat.S_IMODE((h.home / "code").stat().st_mode), 0o755)

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
