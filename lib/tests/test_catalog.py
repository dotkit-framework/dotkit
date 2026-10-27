"""The rcparse rules that the catalog generates.

The old entries (by name) give what the literals of the old rcparse matched,
and nothing else. Every entry, old or new, is checked generically: its words
and PATH markers compile and match an example built from them, its variables
are names, a detected spec has dirs, and env_var values are unique. A new
catalog entry needs no change here (ARCHITECTURE.md, section 10).
"""

import itertools
import re
import unittest

from dotkit.migrate import catalog, rcparse
from dotkit.migrate.migrators import node_tools
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


# The entries of the catalog when it replaced the literals above. Only they are compared with the literals.
OLD_FRAMEWORKS = ("Oh My Zsh", "Prezto", "zinit", "Powerlevel10k", "antigen", "antidote", "Zim", "zplug")
OLD_VERSION_MANAGERS = ("nvm", "fnm", "volta", "pyenv", "rbenv", "asdf", "sdkman", "jenv", "rvm", "nodenv", "goenv",
                        "chruby", "tfenv", "conda", "mamba", "micromamba")


def old(specs, names):
    return [s for s in specs if s.name in names]


def new(specs, names):
    return [s for s in specs if s.name not in names]


# How rcparse uses the words and markers of one entry: framework words are patterns, version-manager words are
# text, and a PATH marker follows a "/" and comes before a "/" or the end.
def framework_pattern(spec) -> str:
    return "|".join(spec.words)


def version_manager_pattern(spec) -> str:
    return r"\b(?:{})\b".format("|".join(re.escape(w) for w in spec.words))


def path_pattern(spec) -> str:
    return "/(?:{})(?:/|$)".format("|".join(spec.path_markers))


def example(pattern: str) -> str:
    """A text that a simple pattern of the catalog matches: a lookahead left out, the first branch of a
    (?:...|...) group, \\b as nothing, \\d as 0, x? as x, and an escaped character as itself."""
    text = re.sub(r"\(\?[=!][^()]*\)", "", pattern)
    text = re.sub(r"\(\?:([^()|]*)(?:\|[^()]*)?\)", r"\1", text)
    text = text.replace(r"\b", "").replace(r"\d", "0")
    text = re.sub(r"(?<!\\)\?", "", text)
    return re.sub(r"\\(.)", r"\1", text)


def matches(text: str, old_literal: str, specs, pattern) -> bool:
    """What the rule should match: what the old literal matched, or what a new entry matches."""
    return bool(re.search(old_literal, text)) or any(re.search(pattern(s), text) for s in specs)


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

    def test_old_entries_are_there(self):
        self.assertEqual([s.name for s in old(catalog.FRAMEWORKS, OLD_FRAMEWORKS)], list(OLD_FRAMEWORKS))
        self.assertEqual([s.name for s in old(catalog.VERSION_MANAGERS, OLD_VERSION_MANAGERS)],
                         list(OLD_VERSION_MANAGERS))

    def test_skip_vars(self):
        added = {**{v: F for s in new(catalog.FRAMEWORKS, OLD_FRAMEWORKS) for v in s.vars},
                 **{v: V for s in new(catalog.VERSION_MANAGERS, OLD_VERSION_MANAGERS) for v in s.extra_vars}}
        self.assertFalse(set(added) & set(OLD_SKIP_VARS), "a new entry takes a variable of an old one")
        self.assertEqual(rcparse.SKIP_VARS, {**OLD_SKIP_VARS, **added})

    def test_framework_words(self):
        self.assertEqual({w for s in old(catalog.FRAMEWORKS, OLD_FRAMEWORKS) for w in s.words},
                         _alternatives(OLD_FRAMEWORK))
        rule, added = self.rules[rcparse.FRAMEWORK], new(catalog.FRAMEWORKS, OLD_FRAMEWORKS)
        for sample, ctx in itertools.product(FRAMEWORK_SAMPLES, CONTEXTS):
            text = ctx.format(sample, sample)
            with self.subTest(text=text):
                self.assertEqual(bool(rule.search(text)), matches(text, OLD_FRAMEWORK, added, framework_pattern))

    def test_version_manager_words(self):
        words = {w for s in old(catalog.VERSION_MANAGERS, OLD_VERSION_MANAGERS) for w in s.words}
        self.assertEqual(words, _alternatives(OLD_VERSION_MANAGER[3:-3]))
        rule, added = self.rules[rcparse.VERSION_MANAGER], new(catalog.VERSION_MANAGERS, OLD_VERSION_MANAGERS)
        samples = VERSION_MANAGER_WORDS + tuple(w.upper() for w in VERSION_MANAGER_WORDS) + \
            tuple(w.lower() for w in VERSION_MANAGER_WORDS) + ("miniconda", "anaconda3", "nvmrc", ".nvmrc")
        hits = 0
        for sample, ctx in itertools.product(samples, CONTEXTS):
            text = ctx.format(sample, sample)
            with self.subTest(text=text):
                self.assertEqual(bool(rule.search(text)),
                                 matches(text, OLD_VERSION_MANAGER, added, version_manager_pattern))
                hits += bool(re.search(OLD_VERSION_MANAGER, text))
        self.assertGreater(hits, 100)

    def test_path_markers(self):
        markers = {m for s in old(catalog.VERSION_MANAGERS, OLD_VERSION_MANAGERS) for m in s.path_markers}
        old_dot = {r"\." + n for n in OLD_PATH_VERSION_MANAGERS[4:].split(")")[0].split("|")}
        self.assertEqual(markers, old_dot | {"fnm", r"(?:ana|mini)conda\d?(?=/)"})
        rule = rcparse._PATH_VERSION_MANAGERS
        added = [s for s in new(catalog.VERSION_MANAGERS, OLD_VERSION_MANAGERS) if s.path_markers]
        hits = 0
        for path in PATH_SAMPLES:
            with self.subTest(path=path):
                self.assertEqual(bool(rule.search(path)),
                                 matches(path, OLD_PATH_VERSION_MANAGERS, added, path_pattern))
                hits += bool(re.search(OLD_PATH_VERSION_MANAGERS, path))
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
        env_vars = [s.env_var for s in catalog.VERSION_MANAGERS if s.env_var]
        self.assertEqual(len(env_vars), len(set(env_vars)), env_vars)
        self.assertEqual({s.name: s.env_var for s in old(catalog.VERSION_MANAGERS, OLD_VERSION_MANAGERS)
                          if s.env_var}, {
            "nvm": "NVM_DIR", "fnm": "FNM_DIR", "volta": "VOLTA_HOME", "pyenv": "PYENV_ROOT",
            "rbenv": "RBENV_ROOT", "asdf": "ASDF_DATA_DIR", "sdkman": "SDKMAN_DIR", "jenv": "JENV_ROOT"})

    def test_nvm_default_is_the_first_line(self):
        """Like the version files of pyenv, rbenv, and jenv: what follows the first line is not the version."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        h.write(".nvm/alias/default", "22\n# x\n")
        nvm = next(s for s in catalog.VERSION_MANAGERS if s.name == "nvm")
        self.assertEqual(nvm.default(h.home / ".nvm"), "22")

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


class EveryEntryTests(unittest.TestCase):
    """Each entry of the catalog, old or new: rcparse knows its lines, PATH directories, and variables."""

    def attributed(self, rule, names, text: str, spec) -> None:
        m = rule.search(text)
        self.assertIsNotNone(m, text)
        self.assertEqual(rcparse._source(m, names), spec.name, text)

    def test_framework_words(self):
        rule = dict(rcparse._RULES)[rcparse.FRAMEWORK]
        for spec in catalog.FRAMEWORKS:
            for word in spec.words:
                with self.subTest(spec=spec.name, word=word):
                    re.compile(word)
                    self.assertRegex(example(word), word)
                    self.attributed(rule, rcparse._FRAMEWORK_NAMES, f"source {example(word)}", spec)

    def test_version_manager_words(self):
        rule = dict(rcparse._RULES)[rcparse.VERSION_MANAGER]
        for spec in catalog.VERSION_MANAGERS:
            self.assertTrue(spec.words, spec.name)
            for word in spec.words:
                with self.subTest(spec=spec.name, word=word):
                    self.attributed(rule, rcparse._VERSION_MANAGER_NAMES, f'eval "$({word} init -)"', spec)

    def test_path_markers(self):
        for spec in catalog.VERSION_MANAGERS:
            for marker in spec.path_markers:
                with self.subTest(spec=spec.name, marker=marker):
                    re.compile(marker)
                    self.attributed(rcparse._PATH_VERSION_MANAGERS, rcparse._PATH_VERSION_MANAGER_NAMES,
                                    f"/Users/me/{example(marker)}/bin", spec)

    def test_variables(self):
        """Each is a name, and an assignment of it is a framework or version-manager line of its entry."""
        for specs, category, of in ((catalog.FRAMEWORKS, rcparse.FRAMEWORK, lambda s: s.vars),
                                    (catalog.VERSION_MANAGERS, rcparse.VERSION_MANAGER, lambda s: s.extra_vars)):
            for spec in specs:
                for var in of(spec):
                    with self.subTest(spec=spec.name, var=var):
                        self.assertRegex(var, rf"^{rcparse._NAME}$")
                        self.assertEqual(rcparse.SKIP_VARS[var], category)
                        self.assertEqual(rcparse._VAR_SOURCES[var], spec.name)
        for spec in catalog.VERSION_MANAGERS:
            if spec.env_var:
                self.assertRegex(spec.env_var, rf"^{rcparse._NAME}$")

    def test_example(self):
        """The examples of the helper itself."""
        patterns = (r"\$\{ZSH\}", r"\bplugins=\(", r"(?:ana|mini)conda\d?(?=/)", r"\.nvm")
        self.assertEqual([example(p) for p in patterns], ["${ZSH}", "plugins=(", "anaconda0", ".nvm"])


if __name__ == "__main__":
    unittest.main()
