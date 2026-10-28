"""The leftovers migrator: what can still take effect after setup (ARCHITECTURE.md, section 10)."""

import os
import unittest
from pathlib import Path

from dotkit.migrate.migrators import built_tools, leftovers, node_tools, python_tools, shell, tools
from dotkit.migrate.migrators.tools import Package
from dotkit.migrate.model import Facts, Identity
from tests import fake

SECTION = "### Leftovers that can still take effect\n"


def tool(h: fake.Home, rel: str) -> None:
    """An executable file in the home directory."""
    h.write(rel, "#!/bin/sh\n", mode=0o755)


class CommandsTests(unittest.TestCase):
    """Package.commands: the command names of each package, from the files the scans read already."""

    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def test_the_commands_of_each_package(self):
        h = self.h
        h.write(".npmrc", "prefix=${HOME}/.npm-global\n")
        h.json(".npm-global/lib/node_modules/typescript/package.json",
               {"name": "typescript", "version": "5.4.0", "bin": {"tsc": "bin/tsc", "tsserver": "bin/tsserver"}})
        h.json(".npm-global/lib/node_modules/@scope/cli/package.json",
               {"name": "@scope/cli", "version": "1.0.0", "bin": "cli.js"})
        h.json(".local/pipx/venvs/ruff/pipx_metadata.json",
               {"main_package": {"package": "ruff", "package_version": "0.4.0", "package_or_url": "ruff",
                                 "apps": ["ruff"]}})
        h.write(".local/share/uv/tools/httpie/uv-receipt.toml",
                '[tool]\nrequirements = [{ name = "httpie" }]\n'
                'entrypoints = [{ name = "http", install-path = "/x/http" },\n'
                '               { name = "https", install-path = "/x/https" }]\n')
        h.json(".cargo/.crates2.json", {"installs": {"bat 0.24.0 (registry+https://x)": {"bins": ["bat"]}}})
        tool(h, "go/bin/hey")
        facts = fake.session(h, fake.brew_runner()).scan()
        commands = {p.name: p.commands for p in node_tools.facts_of(facts).packages}
        self.assertEqual(commands, {"typescript": ["tsc", "tsserver"], "@scope/cli": ["cli"]})
        commands = {p.name: p.commands for p in python_tools.facts_of(facts).packages}
        self.assertEqual(commands, {"ruff": ["ruff"], "httpie": ["http", "https"]})
        b = built_tools.facts_of(facts)
        self.assertEqual([(p.name, p.commands) for p in b.cargo], [("bat", ["bat"])])
        self.assertEqual([(p.name, p.commands) for p in b.go], [("hey", ["hey"])])
        # The tool migrators name them by mise key (Migrator.mise_commands), and the Session merges them.
        self.assertEqual(facts.mise_commands, {
            "npm:@scope/cli": ("cli",), "npm:typescript": ("tsc", "tsserver"), "pipx:httpie": ("http", "https"),
            "pipx:ruff": ("ruff",), "cargo:bat": ("bat",), "go:hey": ("hey",)})
        self.assertEqual(facts.without(["node_tools"]).mise_commands,
                         {k: v for k, v in facts.mise_commands.items() if not k.startswith("npm:")})

    def test_mise_commands_are_merged(self):
        """A key from more than one migrator, or twice from one, has all of its commands once."""
        facts = Facts(Identity("/h", "u"), None, commands_by_key={
            "a": {"npm:x": ("x", "y"), "npm:z": ()}, "b": {"npm:x": ("y", "w")}})
        self.assertEqual(facts.mise_commands, {"npm:x": ("x", "y", "w"), "npm:z": ()})
        self.assertEqual(facts.without(["a"]).mise_commands, {"npm:x": ("y", "w")})
        p = [Package("x", commands=["x"]), Package("x", commands=["x", "y"]), Package("q")]
        self.assertEqual(tools.mise_commands("npm", p), {"npm:x": ("x", "y"), "npm:q": ()})

    def test_crates_toml(self):
        self.h.write(".cargo/.crates.toml", '[v1]\n"tokei 12.1.2 (registry+https://x)" = ["tokei"]\n')
        b = built_tools.facts_of(fake.session(self.h, fake.brew_runner()).scan())
        self.assertEqual([(p.name, p.commands) for p in b.cargo], [("tokei", ["tokei"])])


def link(h: fake.Home, rel: str, target: str) -> None:
    """A link in the home directory to an executable file, which is made too; target is relative to the link."""
    p = h.home / rel
    t = (p.parent / target).resolve() if not os.path.isabs(target) else Path(target)
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text("#!/bin/sh\n")
    t.chmod(0o755)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not os.path.lexists(p):
        os.symlink(target, p)


RUFF = {"main_package": {"package": "ruff", "package_version": "0.4.0", "package_or_url": "ruff", "apps": ["ruff"]}}


class LeftoversTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.repo = str(self.h.home / ".dotfiles")

    def report(self, runner=None, picked=None, dropped=()) -> str:
        p = fake.session(self.h, runner or fake.brew_runner())
        c = p.defaults(self.repo)
        for page, item in picked or ():
            c.picked.setdefault(page, set()).add(item)
        for page, item in dropped:
            c.picked[page].discard(item)
        return p.build(c).report

    def section(self, report: str) -> str:
        if SECTION not in report:
            return ""
        return report.split(SECTION, 1)[1].split("\n### ", 1)[0]

    def npm_global(self, zshrc: str = 'export EDITOR=vim\nexport PATH="$HOME/.npm-global/bin:$PATH"\n') -> None:
        """typescript in ~/.npm-global, its tsc a link in the bin directory, and the PATH line in ~/.zshrc."""
        h = self.h
        h.write(".npmrc", "prefix=${HOME}/.npm-global\n")
        h.json(".npm-global/lib/node_modules/typescript/package.json",
               {"name": "typescript", "version": "5.4.0", "bin": {"tsc": "bin/tsc"}})
        link(h, ".npm-global/bin/tsc", "../lib/node_modules/typescript/bin/tsc")
        h.write(".zshrc", zshrc)

    def test_a_carried_new_directory_shadows_mise_in_interactive_shells(self):
        self.npm_global()
        s = self.section(self.report())
        self.assertIn(f"\n{leftovers.NEW_DIRS}\n\n- ~/.zshrc:2 adds ~/.npm-global/bin\n", s)
        self.assertIn(f"\n{leftovers.INTERACTIVE}\n\n- `tsc` in ~/.npm-global/bin (npm:typescript), from ~/.zshrc:2: "
                      "in interactive shells, it comes before mise's. Old copy: `npm uninstall -g typescript`.\n", s)
        self.assertNotIn(leftovers.GUI, s)
        # The shell scan lists the executables of the carried directory, with their link targets.
        facts = fake.session(self.h, fake.brew_runner()).scan()
        self.assertEqual(shell.facts_of(facts).executables, {str(self.h.home / ".npm-global/bin"): {
            "tsc": str(self.h.home / ".npm-global/lib/node_modules/typescript/bin/tsc")}})

    def test_the_fixes_in_order(self):
        text = leftovers.INTERACTIVE
        steps = ("drop the line", "move it to config/zsh/zprofile (~/.zprofile.local)", "uninstall the old copy",
                 "as a last resort, set `activate_aggressive = true`")
        self.assertEqual([text.index(x) for x in steps], sorted(text.index(x) for x in steps))
        self.assertIn("does not change GUI apps", text)
        # Section 11: only zsh started from another shell was checked, so only the blocks of carried zshrc and
        # zshenv lines name it, each time with the directories of the parent shell; nix develop only as the
        # example of where a project's tools come from; `exec zsh` only for the zshrc lines; no intro names bash or
        # a terminal multiplexer. The fix comes last.
        nested = (leftovers.NEW_DIRS, leftovers.AGAIN, leftovers.ZSHENV)
        for intro in nested:
            self.assertIn("starts from another shell", intro)
            self.assertIn("the directories that the parent shell put in front of PATH", intro)
            self.assertIn(leftovers._PARENT, intro)
            self.assertEqual(intro.count("nix develop"), 1)
        self.assertNotIn("exec zsh", leftovers.ZSHENV)
        self.assertTrue(leftovers.NEW_DIRS.endswith("or move it to config/zsh/zprofile (~/.zprofile.local), which "
                                                    "runs before mise starts."))
        self.assertTrue(leftovers.AGAIN.endswith("Drop that directory from the line: the earlier file adds it "
                                                 "already."))
        self.assertTrue(leftovers.ZSHENV.endswith("Move the line to config/zsh/zprofile (~/.zprofile.local)."))
        for intro in (leftovers.INTERACTIVE, leftovers.GUI, leftovers.STILL_RUNS):
            for word in ("another shell", "direnv", "nix develop"):
                self.assertNotIn(word, intro)
        for intro in nested + (leftovers.INTERACTIVE, leftovers.GUI, leftovers.STILL_RUNS):
            for word in ("nested", "tmux", "multiplexer", "bash -i"):
                self.assertNotIn(word, intro)

    def test_a_new_directory_that_shadows_nothing_now(self):
        """npm:typescript deselected: the line is in its own block, without a warning."""
        self.npm_global()
        s = self.section(self.report(dropped=[("npm", "npm:typescript")]))
        self.assertIn(f"\n{leftovers.NEW_DIRS}\n\n- ~/.zshrc:2 adds ~/.npm-global/bin\n", s)
        self.assertNotIn(leftovers.INTERACTIVE, s)

    def test_an_appended_directory_goes_behind_mise(self):
        """A directory that a line appends goes behind mise's tools: no new-directory item, no warning."""
        for line in ("path+=(~/.npm-global/bin)\n", 'export PATH="$PATH:$HOME/.npm-global/bin"\n'):
            with self.subTest(line=line):
                self.npm_global(line)
                self.assertEqual(self.section(self.report()), "")

    def test_a_deselected_shell_file_carries_no_line(self):
        self.npm_global()
        self.assertEqual(self.section(self.report(dropped=[("shell", "shell:.zshrc")])), "")

    def profile_bin(self) -> None:
        """~/bin with a tsc that shadows mise's npm:typescript."""
        h = self.h
        h.mkdir("bin")
        link(h, "bin/tsc", "../.npm-global/lib/node_modules/typescript/bin/tsc")
        h.json(".nvm/versions/node/v20.11.0/lib/node_modules/typescript/package.json",
               {"name": "typescript", "version": "5.4.0", "bin": {"tsc": "bin/tsc"}})

    def test_a_directory_of_the_profile_put_in_front_again(self):
        """~/bin, carried into config/zsh/zprofile from ~/.zprofile, is on PATH before mise starts. ~/.zshrc
        putting it in front again is no new directory and shadows nothing in the first shell, but it moves the
        directories of a parent shell to the end of PATH in a nested zsh: its own block."""
        h = self.h
        self.profile_bin()
        h.write(".zprofile", 'export PATH="$HOME/bin:$PATH"\n')
        h.write(".zshrc", 'export PATH="$HOME/bin:$PATH"\n')
        s = self.section(self.report())
        self.assertEqual(s, f"\n{leftovers.AGAIN}\n\n- ~/.zshrc:1 adds ~/bin, which config/zsh/zprofile adds "
                            "already\n")
        # Without the line in ~/.zprofile, it is new.
        h.write(".zprofile", "export EDITOR=vim\n")
        s = self.section(self.report())
        self.assertIn("- ~/.zshrc:1 adds ~/bin\n", s)
        self.assertNotIn(leftovers.AGAIN, s)

    def test_a_trailing_slash_in_either_line(self):
        h = self.h
        self.profile_bin()
        for profile, zshrc in (("$HOME/bin/", "$HOME/bin"), ("$HOME/bin", "$HOME/bin/")):
            with self.subTest(profile=profile, zshrc=zshrc):
                h.write(".zprofile", f'export PATH="{profile}:$PATH"\n')
                h.write(".zshrc", f'export PATH="{zshrc}:$PATH"\n')
                s = self.section(self.report())
                self.assertIn(f"\n{leftovers.AGAIN}\n\n- ~/.zshrc:1 adds ~/bin", s)
                self.assertNotIn(leftovers.NEW_DIRS, s)

    def test_a_line_with_a_new_and_an_earlier_directory(self):
        h = self.h
        self.profile_bin()
        h.mkdir("new")
        h.write(".zprofile", 'export PATH="$HOME/bin:$PATH"\n')
        h.write(".zshrc", "path=(~/new ~/bin $path)\n")
        s = self.section(self.report())
        self.assertEqual(s, f"\n{leftovers.NEW_DIRS}\n\n- ~/.zshrc:1 adds ~/new\n\n{leftovers.AGAIN}\n\n"
                            "- ~/.zshrc:1 adds ~/bin, which config/zsh/zprofile adds already\n")

    def test_a_zshenv_line_that_puts_a_directory_in_front(self):
        """Every zsh runs config/zsh/zshenv, so its front directory comes before those of a parent shell, with or
        without a line in ~/.zshrc. A ~/.zshrc line that puts it in front again also gets the AGAIN item."""
        h = self.h
        self.profile_bin()
        h.write(".zshenv", 'export PATH="$HOME/bin:$PATH"\n')
        zshenv = f"\n{leftovers.ZSHENV}\n\n- ~/.zshenv:1 puts ~/bin in front of PATH\n"
        h.write(".zshrc", "export EDITOR=vim\n")
        self.assertEqual(self.section(self.report()), zshenv)
        h.write(".zshrc", "path=(~/bin $path)\n")
        self.assertEqual(self.section(self.report()), f"\n{leftovers.AGAIN}\n\n- ~/.zshrc:1 adds ~/bin, which "
                         f"config/zsh/zshenv adds already\n{zshenv}")
        # In ~/.zprofile too: the item names both files.
        h.write(".zprofile", 'export PATH="$HOME/bin:$PATH"\n')
        self.assertEqual(self.section(self.report()), f"\n{leftovers.AGAIN}\n\n- ~/.zshrc:1 adds ~/bin, which "
                         f"config/zsh/zprofile and config/zsh/zshenv add already\n{zshenv}")

    def test_a_zshenv_line_that_appends(self):
        """A directory that ~/.zshenv appends is on PATH before mise starts too: no ZSHENV item, but the ~/.zshrc
        line that puts it in front gets the AGAIN item."""
        h = self.h
        self.profile_bin()
        h.write(".zshenv", "path+=(~/bin)\n")
        h.write(".zshrc", "export EDITOR=vim\n")
        self.assertEqual(self.section(self.report()), "")
        h.write(".zshrc", "path=(~/bin $path)\n")
        self.assertEqual(self.section(self.report()), f"\n{leftovers.AGAIN}\n\n- ~/.zshrc:1 adds ~/bin, which "
                         "config/zsh/zshenv adds already\n")

    def test_a_directory_of_the_profile_only_or_appended_again(self):
        """Without a line in ~/.zshrc, or with one that appends ~/bin, the directories of a parent shell stay in
        front: no item."""
        h = self.h
        self.profile_bin()
        h.write(".zprofile", 'export PATH="$HOME/bin:$PATH"\n')
        for zshrc in ("export EDITOR=vim\n", "path+=(~/bin)\n", 'export PATH="$PATH:$HOME/bin"\n'):
            with self.subTest(zshrc=zshrc):
                h.write(".zshrc", zshrc)
                self.assertEqual(self.section(self.report()), "")

    def test_a_directory_of_bash_is_new_in_zsh(self):
        """~/.bash_profile runs before mise starts in bash, not in zsh: the line of ~/.zshrc is new."""
        self.npm_global()
        self.h.write(".bash_profile", 'export PATH="$HOME/.npm-global/bin:$PATH"\n')
        s = self.section(self.report())
        self.assertIn("- ~/.zshrc:2 adds ~/.npm-global/bin\n", s)
        self.assertIn("from ~/.zshrc:2: in interactive shells", s)

    def test_a_line_that_adds_homebrew_again_gives_no_warning(self):
        """rcparse does not carry /opt/homebrew/bin, and it is in dotkit.toolPath: not new."""
        h = self.h
        h.json(".local/pipx/venvs/ruff/pipx_metadata.json", RUFF)
        h.write(".zshrc", 'export PATH="/opt/homebrew/bin:$PATH"\npath=(/opt/homebrew/sbin $path)\n')
        self.assertEqual(self.section(self.report()), "")
        home = str(h.home)
        for d in ("/opt/homebrew/bin", "/opt/homebrew/bin/", f"{home}/.local/bin", "/etc/profiles/per-user/jane/bin",
                  "/usr/bin", "/nix/store/abc/bin"):
            self.assertFalse(leftovers.is_new(d, home, "jane", set()), d)
        self.assertTrue(leftovers.is_new(f"{home}/.npm-global/bin", home, "jane", set()))
        self.assertFalse(leftovers.is_new(f"{home}/.npm-global/bin", home, "jane", {f"{home}/.npm-global/bin"}))

    def test_an_old_copy_in_local_bin_shadows_mise_in_gui_apps(self):
        h = self.h
        h.json(".local/pipx/venvs/ruff/pipx_metadata.json", RUFF)
        link(h, ".local/bin/ruff", str(h.home / ".local/pipx/venvs/ruff/bin/ruff"))
        tool(h, ".local/bin/other")
        s = self.section(self.report())
        self.assertIn(f"\n{leftovers.GUI}\n\n- `ruff` in ~/.local/bin (pipx:ruff): in GUI apps and launchd agents, "
                      "this copy comes before its mise shim; interactive shells use mise's. Old copy: "
                      "`pipx uninstall ruff`.\n", s)
        self.assertNotIn("other", s)
        # Deselected, it stays out of mise: nothing shadows it.
        self.assertNotIn(leftovers.GUI, self.report(dropped=[("pipx", "pipx:ruff")]))

    def test_a_file_that_is_not_a_link(self):
        """Without a link, the item says how to find where it comes from, and gives no command."""
        h = self.h
        h.json(".local/pipx/venvs/ruff/pipx_metadata.json", RUFF)
        tool(h, ".local/bin/ruff")
        s = self.section(self.report())
        self.assertIn("interactive shells use mise's. `ls -l ~/.local/bin/ruff` shows where it comes from.\n", s)
        self.assertNotIn("pipx uninstall", s)

    def test_homebrew_copies_of_the_base_tools(self):
        """The base's node and uv: Homebrew's node and an installer's ~/.local/bin/uv come before their shims."""
        h = self.h
        bin_dir = h.prefix / "bin"
        bin_dir.mkdir()
        for name, target in (("node", "../Cellar/node/22.1.0/bin/node"),
                             ("npm", "../lib/node_modules/npm/bin/npm-cli.js"),
                             ("gh", "../Cellar/gh/2.0/bin/gh")):
            t = (bin_dir / target).resolve()
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text("#!/bin/sh\n")
            t.chmod(0o755)
            os.symlink(target, bin_dir / name)
        tool(h, ".local/bin/uv")
        s = self.section(self.report(fake.brew_runner(formulae=["gh"])))
        self.assertIn(f"- `node` and `npm` in {h.prefix}/bin (node, of the dotkit base): in GUI apps and launchd "
                      "agents, these copies come before their mise shims; interactive shells use mise's. Old copy: "
                      f"`brew uninstall node`. `ls -l {h.prefix}/bin/npm` shows where it comes from.\n", s)
        self.assertIn("- `uv` in ~/.local/bin (uv, of the dotkit base): in GUI apps and launchd agents, this copy "
                      "comes before its mise shim; interactive shells use mise's. `ls -l ~/.local/bin/uv` shows "
                      "where it comes from.\n", s)
        self.assertNotIn("`gh`", s)
        # node moved to mise in this run: it is that tool now.
        h.mkdir(".nvm/versions/node/v20.11.0")
        s = self.section(self.report(picked=[("runtimes", "runtime:node")]))
        self.assertIn(f"- `node` and `npm` in {h.prefix}/bin (node): in GUI apps", s)

    def test_a_homebrew_package_that_the_lists_keep(self):
        """python@3.13 stays in nix/brews.txt: `brew uninstall` would be undone by the next apply."""
        bin_dir = self.h.prefix / "bin"
        bin_dir.mkdir()
        target = "../Cellar/python@3.13/3.13.1/bin/python3"
        t = (bin_dir / target).resolve()
        t.parent.mkdir(parents=True)
        t.write_text("#!/bin/sh\n")
        t.chmod(0o755)
        os.symlink(target, bin_dir / "python3")
        runner = fake.brew_runner(formulae=["python@3.13"])
        r = self.report(runner)
        self.assertIn(f"- `{bin_dir}/python3` comes from `python@3.13`, which is in nix/brews.txt. GUI apps and "
                      "launchd agents run it instead of mise's python3. To use mise's: `dotkit remove "
                      "brew:python@3.13`, which takes it out of the list and uninstalls it (brew says when another "
                      "formula depends on it). Or keep it, if you need this python3.\n", self.section(r))
        self.assertNotIn("`brew uninstall python@3.13`", r)
        # Deselected, it is not in the list: brew uninstall removes it for good.
        r = self.report(runner, dropped=[("formulae", "brew:python@3.13")])
        self.assertIn(f"- `python3` in {bin_dir} (python, of the dotkit base): in GUI apps and launchd agents, this "
                      "copy comes before its mise shim; interactive shells use mise's. Old copy: "
                      "`brew uninstall python@3.13`.\n", self.section(r))
        self.assertNotIn("dotkit remove", r)

    def test_a_kept_cask_in_a_carried_directory(self):
        """The same check for an interactive-shell item, and a cask: `dotkit remove cask:<name>`."""
        h = self.h
        h.mkdir("tools/bin")
        link(h, "tools/bin/node", "../../Caskroom/nodeapp/1.0/node")
        h.write(".zshrc", 'export PATH="$HOME/tools/bin:$PATH"\n')
        s = self.section(self.report(fake.brew_runner(casks=["nodeapp"])))
        self.assertIn("- `~/tools/bin/node` comes from `nodeapp`, which is in nix/casks.txt. Interactive shells, "
                      "through ~/.zshrc:1, run it instead of mise's node. To use mise's: `dotkit remove "
                      "cask:nodeapp`, which takes it out of the list and uninstalls it. Or keep it, if you need this "
                      "node.\n", s)
        self.assertNotIn("brew uninstall", s)

    def test_removal_from_the_link_target(self):
        for target, command in (("/opt/homebrew/Cellar/node/22.1.0/bin/node", "`brew uninstall node`"),
                                ("/opt/homebrew/Caskroom/zed/0.1/zed", "`brew uninstall --cask zed`"),
                                ("/Users/jane/.local/pipx/venvs/ruff/bin/ruff", "`pipx uninstall ruff`"),
                                ("/Users/jane/.local/share/uv/tools/httpie/bin/http", "`uv tool uninstall httpie`"),
                                ("/Users/jane/.local/lib/node_modules/@scope/cli/cli.js",
                                 "`npm uninstall -g @scope/cli`"),
                                ("/Users/jane/.local/lib/node_modules/typescript/bin/tsc",
                                 "`npm uninstall -g typescript`"),
                                ("/opt/homebrew/lib/node_modules/npm/bin/npm-cli.js", ""),
                                ("/Users/jane/bin/ruff", ""), ("", "")):
            with self.subTest(target=target):
                self.assertEqual(leftovers.removal(target), command)

    def test_shell_files_that_still_run(self):
        h = self.h
        h.write(".bashrc", "export OLD_BASHRC=1\nsource ~/.nvm/nvm.sh\n")
        h.write(".zlogin", 'eval "$(pyenv init -)"\necho welcome\n')
        h.write(".profile", 'export PATH="$HOME/tools/bin:$PATH"\nexport PATH="$HOME/missing/bin:$PATH"\n')
        h.mkdir("tools/bin")
        h.write(".zlogout", "source $ZSH/oh-my-zsh.sh\n")
        s = self.section(self.report())
        self.assertIn(leftovers.STILL_RUNS, s)
        self.assertIn("- ~/.bashrc:2 still runs `source ~/.nvm/nvm.sh` (nvm)\n"
                      "- ~/.zlogin:1 still runs `eval \"$(pyenv init -)\"` (pyenv)\n"
                      "- ~/.zlogout:1 still runs `source $ZSH/oh-my-zsh.sh` (Oh My Zsh)\n"
                      "- ~/.profile:1 still runs a PATH line that adds ~/tools/bin\n", s)
        self.assertNotIn("OLD_BASHRC", s)
        self.assertNotIn("missing", s)

    def test_a_directory_that_runs_before_in_its_own_shell(self):
        """~/.bashrc and ~/.bash_profile add ~/tools/bin: not new in bash. ~/.zlogin adds it too: new in zsh."""
        h = self.h
        h.mkdir("tools/bin")
        h.write(".bash_profile", 'export PATH="$HOME/tools/bin:$PATH"\n')
        h.write(".bashrc", 'export PATH="$HOME/tools/bin:$PATH"\n')
        h.write(".zlogin", 'export PATH="$HOME/tools/bin:$PATH"\n')
        s = self.section(self.report())
        self.assertIn("- ~/.zlogin:1 still runs a PATH line that adds ~/tools/bin\n", s)
        self.assertNotIn("- ~/.bashrc", s)

    def test_a_secret_in_a_file_that_still_runs_is_not_shown(self):
        self.h.write(".bashrc", fake.fill('nvm use --token "@GH@"\n'))
        r = self.report()
        self.assertIn("- ~/.bashrc:1 still runs (not shown: it has a secret) (nvm)\n", self.section(r))
        self.assertNotIn(fake.TOKENS["@GH@"], r)

    def test_deselected_homebrew_packages_go_stale(self):
        """No block of their own: "Not carried, by your choice" says that they go stale."""
        runner = fake.brew_runner(formulae=["wget", "jq"], casks=["zed"])
        r = self.report(runner, dropped=[("formulae", "brew:wget"), ("casks", "cask:zed")])
        self.assertEqual(self.section(r), "")
        self.assertIn("### Not carried, by your choice\n\nYou left these out of the lists. They stay on this Mac, and "
                      "dotkit does not manage them. `dotkit upgrade brew` upgrades only the packages in the lists, so "
                      "these go stale. With `homebrew.onActivation.cleanup = \"uninstall\"` in nix/darwin.nix, "
                      "activation would uninstall them.\n\n- `wget` (formula): `dotkit add brew:wget`\n", r)

    def test_an_empty_home(self):
        self.assertNotIn(SECTION, self.report(fake.FakeRunner()))

    def test_the_link_of_a_carried_directory(self):
        """A directory that is a link: the shell scan lists the files of its target."""
        self.npm_global()
        os.rename(self.h.home / ".npm-global/bin", self.h.home / ".npm-global/real-bin")
        os.symlink(self.h.home / ".npm-global/real-bin", self.h.home / ".npm-global/bin")
        self.assertIn("in interactive shells, it comes before", self.section(self.report()))

    def test_each_syntax_check_runs_once_per_build(self):
        """shell and leftovers both ask for the carried lines: the Session checks each text once."""
        self.npm_global()
        self.h.write(".bash_profile", "export A=1\n")
        calls = []

        def check(sh, text):
            calls.append((sh, text))
            return True

        p = fake.session(self.h, fake.brew_runner(), check=check)
        p.build(p.defaults(self.repo))
        self.assertEqual(len(calls), len(set(calls)))
        self.assertEqual({sh for sh, _ in calls}, {"zsh", "bash"})
        p.build(p.defaults(self.repo))
        self.assertEqual(len(calls), 2 * len(set(calls)), "a cache per build, not per Session")


class ReferenceCheckTests(unittest.TestCase):
    def test_next_to_every_removal(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.mkdir(".oh-my-zsh")
        h.mkdir(".pyenv/versions/3.12.2")
        tool(h, "go/bin/hey")
        h.json(".cargo/.crates2.json", {"installs": {}})
        p = fake.session(h, fake.brew_runner(extra={}))
        r = p.build(p.defaults(str(h.home / ".dotfiles"))).report
        for path in ("~/.oh-my-zsh", "~/.pyenv"):
            self.assertIn(f"`rm -rf {path}`, check that nothing points into it: LaunchAgents, IDE interpreter "
                          f"settings, git hooks, crontab (`grep -rl {path} ~/Library/LaunchAgents`)", r)
        self.assertEqual(r.count("`rm -rf"), r.count("check that nothing points into it"))


if __name__ == "__main__":
    unittest.main()
