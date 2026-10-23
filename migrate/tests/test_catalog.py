"""The rcparse rules that the catalog generates match what the old literals matched, and nothing else."""

import itertools
import re
import unittest

from dotkit_migrate import catalog, rcparse
from dotkit_migrate.migrators import node_tools
from tests import fake

# The literals of rcparse.py before the catalog (commit a75562e).
OLD_FRAMEWORK = (
    r"oh-my-zsh|\$ZSH\b|\$\{ZSH\}|ZSH_THEME|\bomz\b|prezto|zprezto|\bzinit\b|zi\.zsh|antigen|"
    r"antidote|\bzplug\b|zimfw|\.zim\b|powerlevel10k|powerlevel9k|POWERLEVEL9K|\bp10k\b|"
    r"p10k-instant-prompt|\bplugins=\(|zsh_plugins"
)
OLD_VERSION_MANAGER = (
    r"\b(nvm|NVM_DIR|pyenv|PYENV_ROOT|rbenv|rvm|asdf|sdkman|SDKMAN|jenv|volta|VOLTA_HOME|fnm|"
    r"nodenv|goenv|chruby|tfenv|conda|mamba|micromamba)\b"
)
OLD_PATH_VERSION_MANAGERS = (
    r"/\.(pyenv|rbenv|nvm|nodenv|goenv|asdf|jenv|volta|sdkman|rvm|fnm|tfenv)(/|$)|/fnm(/|$)|/(ana|mini)conda\d?/"
)
# The npm scan's globs before the catalog: (glob, label, index of the version).
OLD_NODE_GLOBS = [
    (".nvm/versions/node/*/lib/node_modules", "nvm", -3),
    (".local/share/fnm/node-versions/*/installation/lib/node_modules", "fnm", -4),
    ("Library/Application Support/fnm/node-versions/*/installation/lib/node_modules", "fnm", -4),
    (".asdf/installs/nodejs/*/lib/node_modules", "asdf", -3),
    (".local/share/mise/installs/node/*/lib/node_modules", "mise node", -3),
]
D, F, V = rcparse.DONE, rcparse.FRAMEWORK, rcparse.VERSION_MANAGER
P, C, PR = rcparse.PATHS, rcparse.COMPLETION, rcparse.PROMPT
OLD_SKIP_VARS = {
    "PATH": P, "FPATH": C, "MANPATH": D, "INFOPATH": D,
    "HOMEBREW_PREFIX": D, "HOMEBREW_CELLAR": D, "HOMEBREW_REPOSITORY": D,
    "ZSH": F, "ZSH_THEME": F, "ZSH_CUSTOM": F, "ZSH_CACHE_DIR": F,
    "ZDOTDIR": F, "plugins": F, "DISABLE_AUTO_UPDATE": F,
    "DISABLE_UPDATE_PROMPT": F, "UPDATE_ZSH_DAYS": F, "DISABLE_AUTO_TITLE": F,
    "ENABLE_CORRECTION": F, "COMPLETION_WAITING_DOTS": F, "HIST_STAMPS": F,
    "CASE_SENSITIVE": F, "HYPHEN_INSENSITIVE": F, "DISABLE_MAGIC_FUNCTIONS": F,
    "DISABLE_LS_COLORS": F, "DISABLE_UNTRACKED_FILES_DIRTY": F, "ZINIT_HOME": F,
    "ZINIT": F, "ZIM_HOME": F, "ANTIDOTE_HOME": F,
    "NVM_DIR": V, "PYENV_ROOT": V, "RBENV_ROOT": V,
    "ASDF_DIR": V, "ASDF_DATA_DIR": V, "SDKMAN_DIR": V,
    "VOLTA_HOME": V, "FNM_DIR": V, "JENV_ROOT": V,
    "NODENV_ROOT": V, "GOENV_ROOT": V,
    "JAVA_HOME": D, "PS1": PR, "PROMPT": PR, "RPROMPT": PR, "RPS1": PR, "PS2": PR,
}

# Text that matches one alternative of the old framework literal, and near misses.
FRAMEWORK_SAMPLES = (
    "oh-my-zsh", "oh-my-zs", "$ZSH", "$ZSHX", "$ZSH_CUSTOM", "${ZSH}", "${ZSH_CUSTOM}", "ZSH_THEME", "ZSH_THEM",
    "omz", "omzx", "xomz", "_omz", "omz_", "prezto", "zprezto", "prezt", "zinit", "zinitx", "xzinit", "zinit_",
    "zi.zsh", "zixzsh", "antigen", "antigenx", "antidote", "antidot", "zplug", "zplugx", "xzplug", "zimfw",
    ".zim", ".zimx", ".zim/", "xzim", "powerlevel10k", "powerlevel9k", "POWERLEVEL9K", "POWERLEVEL10K",
    "p10k", "p10kx", "xp10k", "_p10k", "p10k-instant-prompt", "plugins=(", "myplugins=(", "plugins=x",
    "plugins+=(", "zsh_plugins", "zshplugins", "ZSH", "zsh", "theme",
)
# Words of the old version manager literal, and near misses around each.
VERSION_MANAGER_WORDS = (
    "nvm", "NVM_DIR", "pyenv", "PYENV_ROOT", "rbenv", "rvm", "asdf", "sdkman", "SDKMAN", "jenv", "volta",
    "VOLTA_HOME", "fnm", "nodenv", "goenv", "chruby", "tfenv", "conda", "mamba", "micromamba",
)
CONTEXTS = ("{}", "source {}", "echo {} done", "a{}", "{}b", "_{}", "{}_", "-{}-", ".{}", "${}", "/{}/", "{}9",
            "eval \"$({} init -)\"", "{}{}")
PATH_SAMPLES = tuple(
    f"/Users/me/{name}{tail}"
    for name, tail in itertools.product(
        (".pyenv", ".rbenv", ".nvm", ".nodenv", ".goenv", ".asdf", ".jenv", ".volta", ".sdkman", ".rvm", ".fnm",
         ".tfenv", "fnm", ".chruby", "pyenv", "rvm", "xfnm", "fnmx", ".fnmx", ".pyenvx", "x.pyenv", "anaconda",
         "anaconda3", "miniconda", "miniconda3", "miniconda12", "conda", "xanaconda3", "mamba", ".conda"),
        ("", "/", "/bin", "/shims", "x", "x/bin"),
    )
) + ("/opt/anaconda3/bin", "/opt/homebrew/bin", "/fnm", "/.nvm", "/.nvm/", "fnm/bin", ".nvm/bin")


def _alternatives(literal: str) -> set:
    return set(literal.split("|"))


class CatalogRegexTests(unittest.TestCase):
    def setUp(self):
        self.rules = dict(rcparse._RULES)

    def test_rule_order_and_flags(self):
        self.assertEqual([c for c, _ in rcparse._RULES], [
            rcparse.DONE, rcparse.FRAMEWORK, rcparse.VERSION_MANAGER, rcparse.PROMPT, rcparse.COMPLETION,
            rcparse.OPTIONS, rcparse.SOURCED])
        self.assertEqual(self.rules[rcparse.FRAMEWORK].flags, re.compile(OLD_FRAMEWORK).flags)
        self.assertEqual(self.rules[rcparse.VERSION_MANAGER].flags, re.compile(OLD_VERSION_MANAGER).flags)
        self.assertEqual(rcparse._PATH_VERSION_MANAGERS.flags, re.compile(OLD_PATH_VERSION_MANAGERS).flags)

    def test_skip_vars(self):
        self.assertEqual(rcparse.SKIP_VARS, OLD_SKIP_VARS)

    def test_framework_words(self):
        self.assertEqual({w for s in catalog.FRAMEWORKS for w in s.words}, _alternatives(OLD_FRAMEWORK))
        old, new = re.compile(OLD_FRAMEWORK), self.rules[rcparse.FRAMEWORK]
        for sample, ctx in itertools.product(FRAMEWORK_SAMPLES, CONTEXTS):
            text = ctx.format(sample, sample)
            with self.subTest(text=text):
                self.assertEqual(bool(new.search(text)), bool(old.search(text)))

    def test_version_manager_words(self):
        words = {w for s in catalog.VERSION_MANAGERS for w in s.words}
        self.assertEqual(words, _alternatives(OLD_VERSION_MANAGER[3:-3]))
        old, new = re.compile(OLD_VERSION_MANAGER), self.rules[rcparse.VERSION_MANAGER]
        samples = VERSION_MANAGER_WORDS + tuple(w.upper() for w in VERSION_MANAGER_WORDS) + \
            tuple(w.lower() for w in VERSION_MANAGER_WORDS) + ("miniconda", "anaconda3", "nvmrc", ".nvmrc")
        hits = 0
        for sample, ctx in itertools.product(samples, CONTEXTS):
            text = ctx.format(sample, sample)
            with self.subTest(text=text):
                self.assertEqual(bool(new.search(text)), bool(old.search(text)))
                hits += bool(old.search(text))
        self.assertGreater(hits, 100)

    def test_path_markers(self):
        markers = {m for s in catalog.VERSION_MANAGERS for m in s.path_markers}
        old_dot = {r"\." + n for n in OLD_PATH_VERSION_MANAGERS[4:].split(")")[0].split("|")}
        self.assertEqual(markers, old_dot | {"fnm", r"(?:ana|mini)conda\d?(?=/)"})
        old, new = re.compile(OLD_PATH_VERSION_MANAGERS), rcparse._PATH_VERSION_MANAGERS
        hits = 0
        for path in PATH_SAMPLES:
            with self.subTest(path=path):
                self.assertEqual(bool(new.search(path)), bool(old.search(path)))
                hits += bool(old.search(path))
        self.assertGreater(hits, 40)

    def test_node_globs(self):
        """The globs are relative to the manager's directory: in the usual places, the old globs, with one
        fnm location (the first in its dirs) since step 9."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        for d in (".nvm", ".local/share/fnm", "Library/Application Support/fnm", ".asdf"):
            h.mkdir(d)
        ctx = fake.context_of(h, fake.FakeRunner())
        got = [(str(root.relative_to(h.home) / pattern), label, index)
               for root, pattern, label, index in node_tools.node_version_globs(ctx)]
        self.assertEqual(got, [g for g in OLD_NODE_GLOBS if not g[0].startswith("Library/")])

    def test_env_vars(self):
        self.assertEqual({s.name: s.env_var for s in catalog.VERSION_MANAGERS if s.env_var}, {
            "nvm": "NVM_DIR", "fnm": "FNM_DIR", "volta": "VOLTA_HOME", "pyenv": "PYENV_ROOT",
            "rbenv": "RBENV_ROOT", "asdf": "ASDF_DATA_DIR", "sdkman": "SDKMAN_DIR", "jenv": "JENV_ROOT"})

    def test_names_unique(self):
        for specs in (catalog.VERSION_MANAGERS, catalog.FRAMEWORKS):
            names = [s.name for s in specs]
            self.assertEqual(len(names), len(set(names)))
        owners = [v for s in catalog.FRAMEWORKS for v in s.vars] + \
            [v for s in catalog.VERSION_MANAGERS for v in s.extra_vars]
        self.assertEqual(len(owners), len(set(owners)))

    def test_detected_specs_can_be_scanned(self):
        for spec in catalog.VERSION_MANAGERS:
            with self.subTest(spec=spec.name):
                if spec.detect:
                    self.assertTrue(spec.dirs and spec.versions)
                    self.assertIn(spec.env_var, spec.extra_vars, "its variable is not carried either")
                    self.assertEqual(spec.default is None, spec.tool == "")
                else:
                    self.assertEqual((spec.dirs, spec.tool, spec.default), ((), "", None))


if __name__ == "__main__":
    unittest.main()
