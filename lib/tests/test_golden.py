"""Golden tests: the whole plan and the adopt output for three fake Macs, compared with tests/golden/.

They pin today's behavior, so that a refactor that must not change behavior
shows any change as a failing test, and a change on purpose shows up as a
diff of tests/golden/ for review.

The Macs:
  - empty: nothing installed, no files.
  - used-mac: tests/test_session.py's used_mac, with the default choices.
  - kitchen-sink: every source the scan knows, every item of every
    checklist page selected (so every adoptable config is carried), the vi
    keymap, and a few file choices that are not the defaults.
  - used-mac-deselected and kitchen-sink-deselected: the same Macs with
    single items deselected and selected, page by page.
Adopt runs on the kitchen-sink Mac against a small repository, once with
the defaults and once with --all. Its golden has the output lines of
Session.adopt and the text of each file that writer.apply_edits() changed.

The goldens are normalized, so that they do not depend on the machine:
  - in Path values (Plan.repo, the copy sources), the home directory is ~;
  - in text, the home directory is <HOME> and the rest of the temp directory
    is <TMP>. The code never prints these, so a ~ in text means the code
    wrote ~ itself, and an absolute path that should have been ~ shows up;
  - the user is "jane" and the hostname is "golden-mac";
  - today's date in the report is YYYY-MM-DD;
  - the fake tokens of tests/fake.py are their placeholders (@GH@), so that
    no secret scanner flags the golden files. A placeholder may appear only
    under "local" of a plan, the files that Git ignores;
  - check_syntax is a fake: it rejects a text with GOLDEN_SYNTAX_ERROR in it
    (the kitchen-sink ~/.zprofile.local has one function with it), so the real zsh -n
    plays no part;
  - dictionaries are sorted by key; text with line breaks is a list of lines.

To rewrite the goldens after a change on purpose, run:

    cd lib && DOTKIT_MIGRATE_REGOLD=1 /usr/bin/python3 -B -m unittest tests.test_golden

then review the diff of tests/golden/ and commit it with the change.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dotkit.migrate import registry, writer
from dotkit.migrate.model import AdoptOptions, Choices
from tests import fake
from tests.test_session import used_mac

GOLDEN = Path(__file__).resolve().parent / "golden"
REGOLD = os.environ.get("DOTKIT_MIGRATE_REGOLD") == "1"
USER = "jane"
HOSTNAME = "golden-mac"
SYNTAX_ERROR = "GOLDEN_SYNTAX_ERROR"


def check_syntax(shell: str, text: str) -> bool:
    """A stand-in for zsh -n and bash -n: only a text with the marker fails."""
    del shell
    return SYNTAX_ERROR not in text


# --- The fake Macs


def empty_mac(h: fake.Home) -> tuple[fake.FakeRunner, dict[str, str]]:
    return fake.FakeRunner(), {}


def used(h: fake.Home) -> tuple[fake.FakeRunner, dict[str, str]]:
    return used_mac(h), {}


ZSHRC = """\
# Oh My Zsh
export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="robbyrussell"
plugins=(git docker)
source $ZSH/oh-my-zsh.sh

export NVM_DIR="$HOME/.local/share/nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \\. "$NVM_DIR/nvm.sh"
export PYENV_ROOT="$HOME/.pyenv"
eval "$(pyenv init -)"
. "$HOME/.asdf/asdf.sh"
export SDKMAN_DIR="$HOME/.sdkman"
[[ -s "$HOME/.sdkman/bin/sdkman-init.sh" ]] && source "$HOME/.sdkman/bin/sdkman-init.sh"

bindkey -v
setopt AUTO_CD
export EDITOR=nvim
export PATH="$HOME/.npm-global/bin:$PATH"
export PATH="$HOME/bin:$PATH"
export PATH="$HOME/missing/bin:$PATH"
# Long listing.
alias ll='ls -la'
deploy() {
  curl -H "Authorization: Bearer @GH@" https://deploy.example.com
}
greet() {
  echo "hello $1"
}
PS1='%~ %# '
eval "$(starship init zsh)"
"""

# App Store apps: spaces, a #, Unicode, a quote, a name twice, and a name that the secret check flags.
MAS = (
    "497799835 Xcode (16.0)\n"
    "1289583905 Pixelmator Pro (3.6)\n"
    "1234567890 C# Toolkit (1.0)\n"
    "1274495053 Notes \u270f\ufe0f (2.0)\n"
    "1500000001 Caf\u00e9 \"Pro\" (2.1)\n"
    "409183694 Keynote (14.2)\n"
    "409183695 Keynote (1.0)\n"
    # Its line in nix/mas.txt, "1600000002 Token: Keeper", looks like a secret to the check.
    "1600000002 Token: Keeper (4.0)\n"
)


def kitchen_sink(h: fake.Home) -> tuple[fake.FakeRunner, dict[str, str]]:
    """A Mac with every source the scan knows."""
    # Shell files: a layer file that is read, one that is not, and other shell files.
    h.write(".zshrc", fake.fill(ZSHRC))
    h.write(".zshrc.local", "alias never_read=true\n")
    h.write(".zshenv", "export LANG=en_US.UTF-8\n")
    # ~/bin goes to config/zsh/zprofile, before mise starts: ~/.zshrc putting it in front again is no new
    # directory, but it gets the item about zsh started from another shell (leftovers.AGAIN).
    h.write(".zprofile", 'eval "$(/opt/homebrew/bin/brew shellenv)"\nexport PATH="$HOME/bin:$PATH"\n'
            'source ~/.zprofile.local\n')
    h.mkdir("bin")
    # The fake check_syntax rejects this file, so only its function is left out.
    h.write(".zprofile.local", f"export ZPROFILE_LOCAL=1\ngolden_broken() {{\n  echo {SYNTAX_ERROR}\n}}\n")
    h.write(".bash_profile", "export BASH_SILENCE_DEPRECATION_WARNING=1\n[ -f ~/.bashrc ] && . ~/.bashrc\n")
    # ~/.bashrc still runs, after mise starts.
    h.write(".bashrc", "export OLD_BASHRC=1\nsource ~/.nvm/nvm.sh\n")
    h.write(".zlogin", "echo welcome\n")
    h.write(".zlogout", "clear\n")
    h.write(".profile", "export PROFILE=1\n")
    # The carried PATH line of ~/.zshrc adds this directory: its tsc, a link into typescript in the npm
    # prefix, comes before mise's npm:typescript.
    h.write(".npm-global/lib/node_modules/typescript/bin/tsc", "#!/bin/sh\n", mode=0o755)
    h.mkdir(".npm-global/bin")
    os.symlink("../lib/node_modules/typescript/bin/tsc", h.home / ".npm-global/bin/tsc")
    # Old copies in dotkit.toolPath: GUI apps run them before the mise shims of pipx:ruff and of node. ruff
    # links into its pipx venv; Homebrew's node into its Cellar.
    h.write(".local/pipx/venvs/ruff/bin/ruff", "#!/bin/sh\n", mode=0o755)
    h.mkdir(".local/bin")
    os.symlink(h.home / ".local/pipx/venvs/ruff/bin/ruff", h.home / ".local/bin/ruff")
    node = h.prefix / "Cellar/node/22.1.0/bin/node"
    node.parent.mkdir(parents=True)
    node.write_text("#!/bin/sh\n")
    node.chmod(0o755)
    (h.prefix / "bin").mkdir()
    os.symlink("../Cellar/node/22.1.0/bin/node", h.prefix / "bin/node")
    # Frameworks.
    h.mkdir(".oh-my-zsh")
    h.write(".p10k.zsh", "typeset -g POWERLEVEL9K_MODE=nerdfont-complete\n")
    # Version managers: nvm (in NVM_DIR), fnm (in two places), pyenv, asdf, sdkman.
    h.json(".local/share/nvm/versions/node/v22.1.0/lib/node_modules/typescript/package.json",
           {"name": "typescript", "version": "5.4.0", "bin": {"tsc": "bin/tsc"}})
    h.json(".local/share/nvm/versions/node/v22.1.0/lib/node_modules/pnpm/package.json",
           {"name": "pnpm", "version": "9.0.0", "bin": {"pnpm": "x"}})
    h.mkdir(".local/share/nvm/versions/node/v20.11.0")
    h.write(".local/share/nvm/alias/default", "22\n")
    h.mkdir(".local/share/fnm/node-versions/v21.7.0/installation")
    # A second, stale fnm location: fnm uses the first, and so does the scan; this tool is not in the plan.
    h.json("Library/Application Support/fnm/node-versions/v16.20.0/installation/lib/node_modules/stale-fnm-tool/"
           "package.json", {"name": "stale-fnm-tool", "version": "1.0.0", "bin": {"stale": "x"}})
    h.mkdir(".pyenv/versions/3.11.8")
    h.mkdir(".pyenv/versions/3.12.2")
    h.write(".pyenv/version", "3.12.2\n")
    h.mkdir(".asdf/installs/nodejs/18.19.0")
    h.mkdir(".asdf/installs/golang/1.22.0")
    h.mkdir(".asdf/installs/ruby/3.3.0")
    h.write(".tool-versions", "nodejs 18.19.0\nruby 3.3.0\n")
    h.mkdir(".sdkman/candidates/java/21.0.2-tem")
    h.mkdir(".sdkman/candidates/java/17.0.10-zulu")
    os.symlink("21.0.2-tem", h.home / ".sdkman/candidates/java/current")
    # npm in an npm prefix, and bun.
    h.write(".npmrc", fake.fill("prefix=${HOME}/.npm-global\n@acme:registry=https://npm.pkg.github.com\n"
                                "//npm.pkg.github.com/:_authToken=@GH@\n"))
    h.json(".npm-global/lib/node_modules/eslint/package.json",
           {"name": "eslint", "version": "9.0.0", "bin": {"eslint": "bin/eslint.js"}})
    h.json(".bun/install/global/package.json", {"dependencies": {"prettier": "^3.2.0"}})
    # pipx and uv tools.
    h.json(".local/pipx/venvs/black/pipx_metadata.json",
           {"main_package": {"package": "black", "package_version": "24.2.0", "package_or_url": "black"}})
    h.json(".local/pipx/venvs/ruff/pipx_metadata.json",
           {"main_package": {"package": "ruff", "package_version": "0.4.0", "package_or_url": "ruff",
                             "apps": ["ruff"]}})
    # Its line in config/mise.toml, "pipx:detect-secrets" = "latest", looks like a secret to the check.
    h.json(".local/pipx/venvs/detect-secrets/pipx_metadata.json",
           {"main_package": {"package": "detect-secrets", "package_version": "1.5.0",
                             "package_or_url": "detect-secrets"}})
    h.write(".local/share/uv/tools/httpie/uv-receipt.toml", '[tool]\nrequirements = [{ name = "httpie" }]\n')
    h.write(".local/share/uv/tools/datasette/uv-receipt.toml",
            '[tool]\nrequirements = [{ name = "datasette" }, { name = "datasette-vega" }]\n')
    h.write(".local/share/uv/tools/mytool/uv-receipt.toml",
            '[tool]\nrequirements = [{ name = "mytool", git = "https://github.com/me/mytool" }]\n')
    # cargo: two crates from the registry, one from Git; cargo is installed.
    h.json(".cargo/.crates2.json", {"installs": {
        "bat 0.24.0 (registry+https://github.com/rust-lang/crates.io-index)": {},
        "cargo-watch 8.5.2 (sparse+https://index.crates.io/)": {},
        "mytool 0.1.0 (git+https://github.com/me/mytool#abc123)": {},
    }})
    h.write(".cargo/bin/cargo", "", mode=0o755)
    # Go: one binary with module information, one without; go is not installed.
    hey = h.home / "go/bin/hey"
    hey.parent.mkdir(parents=True)
    hey.write_bytes(b"\x00binary\npath\tgithub.com/rakyll/hey\nmod\tgithub.com/rakyll/hey\tv0.1.4\th1:x=\n")
    hey.chmod(0o755)
    h.write("go/bin/mystery", "#!/bin/sh\n", mode=0o755)
    # mise: your config, and files that mise also reads.
    h.write(".config/mise/config.toml",
            '[tools]\njq = "latest"\n"npm:prettier" = "latest"\npython = "3.12"\n\n[settings]\nexperimental = true\n'
            'idiomatic_version_file_enable_tools = ["rust"]\n')
    h.write(".config/mise/conf.d/work.toml", '[tools]\nterraform = "1.7"\n')
    # Config files: Ghostty in a folded Stow directory, Git in two files, and others.
    h.write("dotfiles/ghostty/config", "font-size = 14\n")
    h.write("dotfiles/ghostty/themes/mine", "background = 000000\n")
    h.mkdir(".config")
    os.symlink("../dotfiles/ghostty", h.home / ".config/ghostty")  # Stow makes relative links.
    h.write("Library/Application Support/com.mitchellh.ghostty/config", "theme = mine\n")
    h.write(".config/git/config", "[pull]\n\trebase = true\n")
    h.write(".gitconfig", fake.fill("[core]\n\teditor = nvim\n[url \"https://x-access-token:@GH@@github.com/\"]\n"
                                    "\tinsteadOf = https://github.com/\n"))
    # ~/.secrets that has a variable already.
    h.write(".secrets", "export OLD_TOKEN=1\n", mode=0o600)
    # A link made by hand, with an absolute path.
    starship = h.write("dotfiles/starship.toml", 'format = "$all"\n')
    os.symlink(starship, h.home / ".config/starship.toml")
    h.write(".config/uv/uv.toml", 'python-preference = "managed"\n')
    h.write(".gemrc", "gem: --no-document\n")
    h.write(".editorconfig", (fake.ROOT / "config/editorconfig").read_text())
    # Other config: adoptable, with a secret, with state, and login files.
    h.write(".config/nvim/init.lua", "vim.o.number = true\n")
    h.write(".config/htop/htoprc", "fields=0 48 17\n")
    h.write(".config/someapp/cache/blob", "x\n")
    h.write(".config/tokenapp/config.json", fake.fill('{"token": "@NPM@"}\n'))
    h.write(".config/gh/hosts.yml", fake.fill("github.com:\n  oauth_token: @GHO@\n"))
    h.write(".vimrc", "set number\n")
    h.write(".netrc", "machine example.com login me password hunter2hunter2\n")
    # pi: config, a login, state, and its old place.
    h.write(".pi/agent/settings.json", '{"theme": "dark"}\n')
    h.write(".pi/agent/extensions/hello.ts", "export default () => {};\n")
    h.write(".pi/agent/auth.json", fake.fill('{"anthropic": {"key": "@GH@"}}\n'))
    h.write(".pi/agent/sessions/one.jsonl", "{}\n")
    h.write(".config/pi/settings.json", '{"theme": "light"}\n')
    runner = fake.brew_runner(
        formulae=["wget", "ripgrep", "node", "zsh", "hashicorp/tap/terraform", "acme/tools/thing"],
        casks=["ghostty", "raycast", "zed", "font-jetbrains-mono"],
        taps=[{"name": "homebrew/core"},
              {"name": "homebrew/cask-fonts"},
              {"name": "hashicorp/tap", "remote": "https://github.com/hashicorp/homebrew-tap"},
              {"name": "acme/tools", "remote": "git@gitlab.com:acme/homebrew-tools.git"},
              # A URL that nix/taps.txt cannot hold.
              {"name": "corp/tap", "remote": "https://git.example.com/homebrew tap.git"}],
        mas=MAS,
    )
    return runner, {"SHELL": "/bin/bash", "NVM_DIR": str(h.home / ".local/share/nvm")}


def kitchen_sink_choices(p: registry.Session, repo: str) -> Choices:
    """Every item of every checklist page selected, and two file choices that are not the defaults."""
    c = p.defaults(repo)
    c.keymap = "vi"
    c.picked = {page.key: {i.id for i in page.items} for page in p.pages() if page.kind == "checklist"}
    c.options.update({"file:.config/uv/uv.toml": "dotkit", "file:.gemrc": "keep"})
    return c


def used_mac_deselected(p: registry.Session, repo: str) -> Choices:
    """The defaults with single items deselected on the pages that are on, and selected on the pages that are off."""
    c = p.defaults(repo)
    c.picked["formulae"].discard("brew:hashicorp/tap/terraform")
    c.picked["casks"].discard("cask:raycast")
    c.picked["taps"].discard("tap:acme/tools")
    c.picked["mas"].discard("mas:497799835")
    c.picked["npm"].discard("npm:typescript")
    c.picked["shell"].discard("shell:.zshrc")
    c.picked["runtimes"].add("runtime:node")
    c.picked["other"].add("other:.bashrc")
    return c


def kitchen_sink_deselected(p: registry.Session, repo: str) -> Choices:
    """Every item selected, then some left out: part of a version manager, one of two apps with one name, and more."""
    c = kitchen_sink_choices(p, repo)
    # Without runtime:node, the Homebrew node in the prefix shadows the base's node.
    for page, item in (("runtimes", "runtime:go"), ("runtimes", "runtime:node"), ("mas", "mas:409183694"),
                       ("formulae", "brew:wget"), ("taps", "tap:hashicorp/tap"), ("pipx", "pipx:httpie"), ("go", "go:github.com/rakyll/hey"),
                       ("shell", "shell:.zprofile.local"), ("other", "other:.config/nvim")):
        c.picked[page].remove(item)
    return c


# A small repository for adopt: lists with and without a final line break.
ADOPT_REPO = {
    "nix/packages.txt": "dust\n",
    "nix/brews.txt": "# mine\nwget\n",
    "nix/casks.txt": "raycast",
    "nix/taps.txt": "hashicorp/tap\n",
    "nix/mas.txt": "497799835 Xcode",
    "config/mise.toml": '[tools]\n"npm:typescript" = "latest"\n\n[settings]\nexperimental = true\n',
}
ADOPT_DECLARED = {"taps": ["hashicorp/tap"], "masApps": {"Xcode": 497799835}}


# --- Normalizing


def to_json(value, home: Path):
    """A value as plain JSON data: dataclasses as objects, paths as strings with ~, text as lines."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json(getattr(value, f.name), home) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): to_json(v, home) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(v, home) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(to_json(v, home) for v in value)
    if isinstance(value, Path):
        p = str(value)
        return "~" + p[len(str(home)):] if p == str(home) or p.startswith(str(home) + "/") else p
    if isinstance(value, str) and "\n" in value:
        return value.split("\n")
    return value


class Normalizer:
    def __init__(self, h: fake.Home, dates: list[str]):
        self.pairs: list[tuple[str, str]] = []
        # The longer form first: the real path of a temp directory can be /private/var/... for /var/....
        for root in sorted({str(h.root), os.path.realpath(h.root)}, key=len, reverse=True):
            # The Homebrew prefix of fake.Home starts like the home directory.
            self.pairs += [(root + "/homebrew", "<TMP>/homebrew"), (root + "/home", "<HOME>"), (root, "<TMP>")]
        self.pairs += [(d, "YYYY-MM-DD") for d in dates]
        self.pairs += [(v, k) for k, v in fake.TOKENS.items()]

    def __call__(self, value):
        if isinstance(value, str):
            for old, new in self.pairs:
                value = value.replace(old, new)
            return value
        if isinstance(value, dict):
            return {self(k): self(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self(v) for v in value]
        return value


def dump(data) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# --- The tests

PLANS = {
    "plan-empty": (empty_mac, None),
    "plan-used-mac": (used, None),
    "plan-kitchen-sink": (kitchen_sink, kitchen_sink_choices),
    "plan-used-mac-deselected": (used, used_mac_deselected),
    "plan-kitchen-sink-deselected": (kitchen_sink, kitchen_sink_deselected),
}
ADOPTS = {
    "adopt-kitchen-sink": False,
    "adopt-kitchen-sink-all": True,
}


class GoldenTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        for patch in (mock.patch("getpass.getuser", return_value=USER),
                      mock.patch("socket.gethostname", return_value=HOSTNAME + ".local")):
            patch.start()
            self.addCleanup(patch.stop)

    def mac(self, make):
        """A new fake Mac and its Session, after the scan."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        runner, env = make(h)
        p = fake.session(h, runner, env, check=check_syntax)
        facts = p.scan()
        self.assertEqual(facts.warnings, [])
        self.assertEqual((facts.identity.user, facts.identity.hostname), (USER, HOSTNAME))
        return h, p

    def check(self, name: str, data) -> None:
        text = dump(data)
        for leak in ("dotkit-migrate-test-", tempfile.gettempdir()):
            self.assertNotIn(leak, text, "the normalization missed it")
        # The tokens: only in the lines for ~/.secrets, outside the repository.
        tracked = dump({k: v for k, v in data.items() if k != "secrets"})
        for placeholder in fake.TOKENS:
            self.assertNotIn(placeholder, tracked, "a secret outside ~/.secrets")
        path = GOLDEN / f"{name}.json"
        if REGOLD:
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return
        self.assertTrue(path.is_file(), f"{path} is missing. Run with DOTKIT_MIGRATE_REGOLD=1 to write it.")
        self.assertEqual(text, path.read_text(encoding="utf-8"),
                         f"{name} differs from its golden. If the change is on purpose, see this module's "
                         "docstring.")

    def test_plans(self):
        for name, (make, choose) in PLANS.items():
            with self.subTest(name=name):
                h, p = self.mac(make)
                repo = str(h.home / ".dotfiles")
                c = choose(p, repo) if choose else p.defaults(repo)
                dates = [datetime.date.today().isoformat()]
                result = p.build(c)
                dates.append(datetime.date.today().isoformat())
                self.check(name, Normalizer(h, dates)(to_json(result, h.home)))

    def test_adopt(self):
        for name, all_tools in ADOPTS.items():
            with self.subTest(name=name):
                h, p = self.mac(kitchen_sink)
                repo = h.home / ".dotfiles"
                for rel, text in ADOPT_REPO.items():
                    h.write(f".dotfiles/{rel}", text)
                result = p.adopt(repo, ADOPT_DECLARED, AdoptOptions(all_tools=all_tools))
                self.assertEqual(result.failures, [])
                before = self.files(repo)
                writer.apply_edits(result, repo)
                after = self.files(repo)
                # A deleted file is null.
                edited = {rel: after.get(rel) for rel in before.keys() | after.keys()
                          if before.get(rel) != after.get(rel)}
                data = {"output": result.output, "edited": to_json(edited, h.home)}
                self.check(name, Normalizer(h, [])(data))

    @unittest.skipIf(REGOLD, "the goldens are being written")
    def test_no_stale_goldens(self):
        """Each golden file belongs to a case above."""
        names = sorted(p.stem for p in GOLDEN.glob("*.json"))
        self.assertEqual(names, sorted([*PLANS, *ADOPTS]))

    @staticmethod
    def files(repo: Path) -> dict[str, str]:
        return {str(p.relative_to(repo)): p.read_text(encoding="utf-8")
                for p in sorted(repo.rglob("*")) if p.is_file()}


if __name__ == "__main__":
    unittest.main()
