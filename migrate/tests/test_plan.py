import shutil
import subprocess
import unittest

from dotkit_migrate import migrators, registry, secrets
from dotkit_migrate._compat import tomllib
from dotkit_migrate.migrators import Migrator
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


def other_config(p) -> list[str]:
    """The paths of the items of the "other" page: the config that can be copied into the repository."""
    return [i.id[len("other:"):] for page in p.pages() if page.key == "other" for i in page.items]


def pick(c, *item_ids: str) -> None:
    """Selects items, as the person does on their page."""
    for item_id in item_ids:
        c.picked.setdefault(PAGE_OF[item_id.split(":")[0]], set()).add(item_id)


# The page of each item-id prefix.
PAGE_OF = {"brew": "formulae", "cask": "casks", "tap": "taps", "mas": "mas", "npm": "npm", "pipx": "pipx",
           "cargo": "cargo", "go": "go", "runtime": "runtimes", "shell": "shell", "other": "other"}


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
        self.p = fake.session(self.h, runner)
        self.assertEqual(self.p.scan().warnings, [])
        self.repo = str(self.h.home / ".dotfiles")

    def build(self, picked=None, options=None, **changes):
        """The plan with the defaults, and these changes: picked replaces the selection of a page."""
        c = self.p.defaults(self.repo)
        c.picked.update(picked or {})
        c.options.update(options or {})
        for k, v in changes.items():
            setattr(c, k, v)
        return self.p.build(c)

    def test_defaults(self):
        pages = {page.key: page for page in self.p.pages()}
        self.assertEqual(list(pages), ["formulae", "casks", "taps", "mas", "npm", "pipx", "cargo", "runtimes",
                                       "shell", "files", "other"])
        items = {k: [(i.id, i.default) for i in page.items] for k, page in pages.items()}
        self.assertEqual(items["formulae"], [("brew:wget", True), ("brew:hashicorp/tap/terraform", True)])
        self.assertEqual(items["casks"], [("cask:raycast", True)])
        self.assertEqual(items["taps"], [("tap:hashicorp/tap", True), ("tap:acme/tools", True)])
        self.assertEqual(items["mas"], [("mas:497799835", True), ("mas:1289583905", True)])
        self.assertEqual(items["npm"], [("npm:typescript", True)])
        self.assertEqual(items["pipx"], [("pipx:ruff", True)])
        self.assertEqual(items["cargo"], [("cargo:bat", False)])
        self.assertEqual(items["runtimes"], [("runtime:node", False)])
        self.assertEqual([i.label for i in pages["runtimes"].items], ["node 20"])
        # One item per shell file, not per statement.
        self.assertEqual(items["shell"], [("shell:.zprofile", True), ("shell:.zshrc", True),
                                          ("shell:.bash_profile", True)])
        self.assertEqual([i.label for i in pages["shell"].items],
                         ["~/.zprofile: 1 line", "~/.zshrc: 2 lines and 1 line with a secret",
                          "~/.bash_profile: 1 line"])
        self.assertEqual(items["other"], [("other:.config/nvim", False), ("other:.bashrc", False)])
        self.assertTrue(all(page.kind == "checklist" for k, page in pages.items() if k != "files"))
        self.assertEqual(pages["files"].kind, "choices")
        c = self.p.defaults(self.repo)
        self.assertEqual(c.picked["cargo"], set())
        self.assertEqual(c.picked["formulae"], {"brew:wget", "brew:hashicorp/tap/terraform"})
        files = {i.id: i.default_option for i in pages["files"].items}
        self.assertEqual(files, c.options)
        self.assertEqual({k[len("file:"):]: v for k, v in files.items()}, {
            ".config/ghostty/config": "layer",
            ".config/starship.toml": "replace",
            ".npmrc": "keep",
            ".editorconfig": "dotkit",
            ".gitconfig": "adopt",
            ".config/mise/config.toml": "layer",
        })
        self.assertEqual(other_config(self.p), [".config/nvim", ".bashrc"])

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
        p = fake.session(self.h, fake.brew_runner())
        self.assertNotIn(".config/msmtp", other_config(p))
        c = p.defaults(self.repo)
        pick(c, "other:.config/msmtp")
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

    def test_pages_off(self):
        """Every item of the pages that are on by default deselected: each one is "Not carried, by your choice"."""
        off = {k: set() for k in ("formulae", "casks", "taps", "mas", "npm", "pipx", "shell")}
        p = self.build(picked=off)
        self.assertNotIn("nix/brews.txt", p.files)
        self.assertNotIn("nix/casks.txt", p.files)
        self.assertNotIn("nix/taps.txt", p.files)
        self.assertNotIn("nix/mas.txt", p.files)
        self.assertEqual(tomllib.loads(p.files["config/mise.toml"])["tools"], {"jq": "latest"})
        self.assertNotIn("export EDITOR", p.files["config/zsh/zshrc"])
        self.assertEqual(p.local, {})
        section = p.report[p.report.index("### Not carried, by your choice\n"):]
        section = section[:section.index("\n### ", 1)]
        # The cleanup warning is for formulae and casks only.
        brew, rest = section.split(migrators.NOT_CARRIED)
        self.assertIn(migrators.NOT_CARRIED_BREW, brew)
        self.assertNotIn("cleanup", rest)
        for text in ("- `wget` (formula): `dotkit add brew:wget`\n",
                     "- `raycast` (cask): `dotkit add cask:raycast`\n"):
            self.assertIn(text, brew)
        for text in ("- `acme/tools` (tap): add `acme/tools git@gitlab.com:acme/homebrew-tools.git` to "
                     "nix/taps.txt\n",
                     "- `Xcode` (App Store app): `dotkit add mas:497799835`\n",
                     "- `typescript` (nvm v20.11.0): `mise use -g npm:typescript`\n",
                     "- `ruff` (pipx): `mise use -g pipx:ruff`\n",
                     "- ~/.zprofile: 1 line that could be carried to config/zsh/zprofile. Copy the ones you need.\n",
                     "- ~/.zshrc: 2 lines that could be carried to config/zsh/zshrc. Copy the ones you need. Its "
                     "lines with secrets are not carried: `GITHUB_TOKEN` (line 4). Keep them out of files that Git "
                     "tracks.\n"):
            self.assertIn(text, rest)
        # The secret by name only, and nowhere else.
        self.assertEqual(p.report.count("GITHUB_TOKEN"), 1)
        self.assertNotIn(TOKEN, p.report)
        # Off by default: their own to-dos, as with the defaults.
        self.assertNotIn("bat", section)
        self.assertIn("- `bat` (cargo install): `mise use -g cargo:bat`\n", p.report)
        self.assertNotIn("Homebrew packages that are not in the lists", p.report)

    def test_single_items(self):
        """Items are selected one by one; the rest of their page is carried."""
        p = self.build(picked={"formulae": {"brew:wget"}, "taps": {"tap:acme/tools"}, "mas": {"mas:1289583905"},
                               "shell": {"shell:.zshrc", "shell:.bash_profile"}, "other": {"other:.config/nvim"},
                               "runtimes": {"runtime:node"}})
        self.assertTrue(p.files["nix/brews.txt"].endswith("\nwget\n"))
        self.assertTrue(p.files["nix/taps.txt"].endswith("\nacme/tools git@gitlab.com:acme/homebrew-tools.git\n"))
        self.assertTrue(p.files["nix/mas.txt"].endswith("\n1289583905 Pixelmator Pro\n"))
        self.assertIn("export EDITOR=vim", p.files["config/zsh/zshrc"])
        self.assertNotIn("config/zsh/zprofile", p.files)
        self.assertIn("export BASH_THING=1", p.files["config/bash/bash_profile"])
        self.assertEqual(p.links[".config/nvim"], "config/nvim")
        self.assertNotIn(".bashrc", p.links)
        self.assertEqual(tomllib.loads(p.files["config/mise.toml"])["tools"]["node"], "20")
        for text in ("1 Homebrew formula -> nix/brews.txt", "1 Homebrew tap -> nix/taps.txt",
                     "1 App Store app -> nix/mas.txt", "runtime versions of nvm -> config/mise.toml",
                     "1 other config file or directory -> config/, linked with dotkit.links"):
            self.assertIn(text, p.summary)
        for text in ("- `hashicorp/tap/terraform` (formula): `dotkit add brew:hashicorp/tap/terraform`\n",
                     "- `hashicorp/tap` (tap): add `hashicorp/tap` to nix/taps.txt\n",
                     "- `Xcode` (App Store app): `dotkit add mas:497799835`\n",
                     "- ~/.zprofile: 1 line that could be carried to config/zsh/zprofile. Copy the ones you need.\n",
                     "nvm (~/.nvm): node v20.11.0 (default 20). config/mise.toml has these versions now."):
            self.assertIn(text, p.report)
        self.assertIn("- `acme/tools`: git@gitlab.com:acme/homebrew-tools.git\n", p.report)
        self.assertNotIn("~/.config/nvim\n", p.report.split("### Other config\n")[1])

    def test_taps_and_apps_that_cannot_go_into_the_lists(self):
        runner = fake.brew_runner(taps=[
            {"name": "ok/tap", "remote": "https://github.com/ok/homebrew-tap"},
            {"name": "acme/space", "remote": "https://example.com/a b.git"},
            {"name": "acme/hash", "remote": "https://example.com/a.git#main"},
        ], mas="409183694 Keynote (14.2)\n1234 Keynote (1.0)\n497799835 Xcode (16.0)\n")
        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = fake.session(h, runner)
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

    def test_runtimes_and_cargo_on(self):
        p = self.build(picked={"runtimes": {"runtime:node"}, "cargo": {"cargo:bat"}})
        tools = tomllib.loads(p.files["config/mise.toml"])["tools"]
        self.assertEqual(tools["node"], "20")
        self.assertEqual(tools["cargo:bat"], "latest")

    def test_empty_mac(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = fake.session(h, fake.FakeRunner())
        self.assertEqual(p.pages(), [])
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertEqual(sorted(result.files), ["MIGRATION.md", "nix/user.nix"])
        self.assertIn("Nothing.", result.report)

    def test_repo_path_with_a_space(self):
        p = self.build(repo=str(self.h.home / "my dots"))
        self.assertIn('  dotfiles = "${home}/my dots";\n', p.files["nix/user.nix"])
        self.assertIn('source "$HOME/my dots/local/zshrc.zsh"', p.files["config/zsh/zshrc"])


class SkipTests(unittest.TestCase):
    def test_a_migrator_whose_scan_failed_has_no_pages_and_no_plan(self):
        """The Session does not ask it: its pages and its plan would raise."""
        class Broken(Migrator):
            key = "broken"

            def scan(self, ctx):
                raise RuntimeError("boom")

            def pages(self, facts):
                raise AssertionError("pages of a migrator without facts")

            def plan(self, facts, choices, txn):
                raise AssertionError("plan of a migrator without facts")

        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = registry.Session(fake.context_of(h, fake.brew_runner(formulae=["wget"])),
                             migrators=(Broken(), *registry.MIGRATORS))
        self.assertEqual(p.scan().warnings, ["broken: RuntimeError: boom"])
        result = p.build(p.defaults(str(h.home / ".dotfiles")))
        self.assertIn("- broken: RuntimeError: boom\n", result.report)
        self.assertIn("\nwget\n", result.files["nix/brews.txt"])


class NixFileTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("nix-instantiate"), "no nix-instantiate")
    def test_nix_files_parse(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        p = fake.session(h, used_mac(h))
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
