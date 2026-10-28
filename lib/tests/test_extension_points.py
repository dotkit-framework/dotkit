"""The extension points (ARCHITECTURE.md, section 10): a new catalog entry and a new migrator need no test list.

A fake version manager and a fake framework are added to the catalog, with
the rcparse rules built again from it, and a fake tool migrator (gem tools,
through migrators/tools.py) is added to MIGRATORS. All of them exist only
here. With them, the catalog, invariant, failed-scan, isolation, and
leftovers tests run unchanged, as subclasses of their own classes, on fake
Macs that have the new sources too.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from unittest import mock

from dotkit.migrate import catalog, rcparse, registry
from dotkit.migrate.context import Context, read_json, read_text
from dotkit.migrate.declared import Declared
from dotkit.migrate.migrators import Migrator, tools
from dotkit.migrate.migrators.tools import Package
from dotkit.migrate.model import AdoptOptions, Choices, Facts, Page
from tests import fake, test_catalog, test_failed_scans, test_isolation, test_registry
from tests.migrators import test_leftovers
from tests.migrators.test_leftovers import link

FAKE_MANAGER = catalog.VersionManagerSpec(
    "fakeenv", (".fakeenv",), "lua", "versions", lambda root: read_text(root / "version").split("\n")[0].strip(),
    env_var="FAKEENV_ROOT", mise_replaces=True, words=("fakeenv", "FAKEENV_ROOT"), extra_vars=("FAKEENV_ROOT",),
    path_markers=(r"\.fakeenv",))
FAKE_FRAMEWORK = catalog.FrameworkSpec("Fakefw", (".fakefw",), vars=("FAKEFW_HOME",), words=(r"\bfakefw\b",))
SHELL_LINES = ('eval "$(fakeenv init -)"\nexport PATH="$HOME/.fakeenv/shims:$PATH"\nexport FAKEFW_HOME=~/.fakefw\n'
               'source $FAKEFW_HOME/fakefw.zsh\n')


@dataclass
class GemFacts:
    packages: List[Package] = field(default_factory=list)


class GemTools(Migrator):
    """A tool source that moves to mise, as section 10 describes one: it reuses migrators/tools.py."""

    key = "gem_tools"
    title = "gem tools"
    sections = ("Tools that moved to mise", "Tools that are not in mise", "mise", "Not carried, by your choice")

    def manual_steps(self, ctx: Context) -> str:
        return ("None of your gems is in config/mise.toml. They stay where they are. Add the ones you want with "
                "`mise use -g gem:<name>`.")

    def facts_of(self, f: Facts) -> Optional[GemFacts]:
        return f.get(self.key)

    def scan(self, ctx: Context) -> GemFacts:
        data = read_json(ctx.home / ".fakegem/installed.json")
        found = data.items() if isinstance(data, dict) else []
        return GemFacts([Package(name, str(v.get("version", "")), "~/.fakegem",
                                 commands=[str(c) for c in v.get("bin") or []])
                         for name, v in sorted(found) if isinstance(v, dict)])

    def pages(self, f: Facts) -> List[Page]:
        g = self.facts_of(f)
        if g is None:
            return []
        return [tools.page("gem", "gem tools", "Your gems. The selected ones move to mise.", g.packages,
                           Declared.for_setup(f.base, f))]

    def plan(self, f: Facts, c: Choices, txn) -> None:
        g = self.facts_of(f)
        if g is not None:
            tools.plan_tools(f, c, txn, self.pages(f), "gem", g.packages, "# From gem, see MIGRATION.md.")

    def adopt(self, f: Facts, declared: Declared, txn, opts: AdoptOptions) -> None:
        g = self.facts_of(f)
        if g is not None:
            tools.adopt_tools(declared, txn, "gem", g.packages)

    def mise_commands(self, f: Facts) -> Dict[str, Tuple[str, ...]]:
        g = self.facts_of(f)
        return tools.mise_commands("gem", g.packages if g else [])


GEMS = GemTools()


def add_sources(h: fake.Home) -> None:
    """The new sources on a fake Mac: a gem with a copy in ~/.local/bin, the fake manager, and the framework,
    with their lines at the end of ~/.zshrc."""
    h.json(".fakegem/installed.json", {"rake2": {"version": "1.0.0", "bin": ["rake2"]}})
    link(h, ".local/bin/rake2", str(h.home / ".fakegem/gems/rake2/bin/rake2"))
    h.write(".fakeenv/version", "5.4.6\n")
    h.mkdir(".fakeenv/versions/5.4.6")
    h.mkdir(".fakefw")
    zshrc = h.home / ".zshrc"
    h.write(".zshrc", (zshrc.read_text(encoding="utf-8") if zshrc.exists() else "") + SHELL_LINES)


def fresh_rcparse() -> types.ModuleType:
    """rcparse executed again, so that it builds its rules from the catalog as it is now."""
    name = "dotkit.migrate._rcparse_extended"
    spec = importlib.util.spec_from_file_location(name, rcparse.__file__)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[name]
    return module


def extend(test: unittest.TestCase) -> None:
    """The catalog with the fake entries, rcparse's rules built from it, and MIGRATORS with the gem tools,
    until the test ends."""
    stack = contextlib.ExitStack()
    test.addCleanup(stack.close)
    stack.enter_context(mock.patch.object(catalog, "VERSION_MANAGERS", catalog.VERSION_MANAGERS + (FAKE_MANAGER,)))
    stack.enter_context(mock.patch.object(catalog, "FRAMEWORKS", catalog.FRAMEWORKS + (FAKE_FRAMEWORK,)))
    for name, value in vars(fresh_rcparse()).items():
        if name.startswith("__") or callable(value) or isinstance(value, types.ModuleType):
            continue
        if getattr(rcparse, name) != value:
            stack.enter_context(mock.patch.object(rcparse, name, value))
    keys = [m.key for m in registry.MIGRATORS]
    at = keys.index("built_tools") + 1
    stack.enter_context(mock.patch.object(registry, "MIGRATORS",
                                          (*registry.MIGRATORS[:at], GEMS, *registry.MIGRATORS[at:])))


def with_sources(make):
    """A fake Mac of the tests with the new sources too."""

    def made(h: fake.Home):
        out = make(h)
        add_sources(h)
        return out

    return made


class ExtendedTests(unittest.TestCase):
    """The extension itself: the rules changed, and the fake entries and the gem tools take part."""

    def setUp(self):
        extend(self)
        for patch in (mock.patch("getpass.getuser", return_value="jane"),
                      mock.patch("socket.gethostname", return_value="test-mac.local")):
            patch.start()
            self.addCleanup(patch.stop)
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def test_the_rules_know_the_new_entries(self):
        self.assertIn(GEMS, registry.MIGRATORS)
        self.assertEqual(rcparse.SKIP_VARS["FAKEENV_ROOT"], rcparse.VERSION_MANAGER)
        self.assertEqual(rcparse.SKIP_VARS["FAKEFW_HOME"], rcparse.FRAMEWORK)
        res = rcparse.Parser(str(self.h.home)).parse(".zshrc", SHELL_LINES)
        self.assertEqual([(f.category, f.source) for f in res.findings], [
            (rcparse.VERSION_MANAGER, "fakeenv"), (rcparse.VERSION_MANAGER, "fakeenv"),
            (rcparse.FRAMEWORK, "Fakefw"), (rcparse.FRAMEWORK, "Fakefw")])
        self.assertEqual(res.carried, [])

    def test_a_plan_with_the_new_sources(self):
        add_sources(self.h)
        s = fake.session(self.h, fake.brew_runner())
        self.assertEqual(s.scan().mise_commands, {"gem:rake2": ("rake2",)})
        c = s.defaults(str(self.h.home / ".dotfiles"))
        c.picked["runtimes"].add("runtime:lua")
        plan = s.build(c)
        self.assertEqual(plan.failures, [])
        mise = plan.files["config/mise.toml"]
        self.assertIn('"gem:rake2" = "latest"', mise)
        self.assertIn('"lua" = "5.4.6"', mise)
        self.assertIn("Fakefw", plan.report)
        # The leftovers report knows the commands of the gem from GemTools.mise_commands.
        self.assertIn("- `rake2` in ~/.local/bin (gem:rake2): in GUI apps and launchd agents, this copy comes before "
                      "its mise shim; interactive shells use mise's.", plan.report)


class CleanupTests(unittest.TestCase):
    def test_the_extension_ends_with_its_test(self):
        before = (registry.MIGRATORS, catalog.VERSION_MANAGERS, catalog.FRAMEWORKS, rcparse._RULES,
                  rcparse.SKIP_VARS)
        t = unittest.TestCase()
        self.addCleanup(t.doCleanups)
        extend(t)
        self.assertNotEqual((registry.MIGRATORS, rcparse.SKIP_VARS), (before[0], before[-1]))
        t.doCleanups()
        after = (registry.MIGRATORS, catalog.VERSION_MANAGERS, catalog.FRAMEWORKS, rcparse._RULES,
                 rcparse.SKIP_VARS)
        self.assertTrue(all(x is y for x, y in zip(before, after)))


# --- The tests of the other modules, unchanged, with the extension


class ExtendedCatalogRegexTests(test_catalog.CatalogRegexTests):
    def setUp(self):
        extend(self)
        super().setUp()


class ExtendedEveryEntryTests(test_catalog.EveryEntryTests):
    def setUp(self):
        extend(self)
        super().setUp()


class ExtendedRegistryTests(test_registry.RegistryTests):
    def setUp(self):
        extend(self)
        for name in ("kitchen_sink", "used_mac"):
            patch = mock.patch.object(test_registry, name, with_sources(getattr(test_registry, name)))
            patch.start()
            self.addCleanup(patch.stop)
        super().setUp()


class ExtendedSecretPropertyTests(test_registry.SecretPropertyTests):
    def setUp(self):
        extend(self)
        patch = mock.patch.object(test_registry, "kitchen_sink", with_sources(test_registry.kitchen_sink))
        patch.start()
        self.addCleanup(patch.stop)
        super().setUp()


class ExtendedFailedScanTests(test_failed_scans.FailedScanTests):
    def setUp(self):
        extend(self)
        super().setUp()

    def mac(self):
        h, runner = super().mac()
        add_sources(h)
        return h, runner


class ExtendedEveryMigratorTests(test_isolation.EveryMigratorTests):
    def setUp(self):
        extend(self)
        super().setUp()
        add_sources(self.h)


class ExtendedAdoptTests(test_isolation.AdoptTests):
    def setUp(self):
        extend(self)
        super().setUp()
        add_sources(self.h)


class ExtendedCommandsTests(test_leftovers.CommandsTests):
    def setUp(self):
        extend(self)
        super().setUp()


class ExtendedLeftoversTests(test_leftovers.LeftoversTests):
    def setUp(self):
        extend(self)
        super().setUp()


if __name__ == "__main__":
    unittest.main()
