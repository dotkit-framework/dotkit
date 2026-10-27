"""Crash isolation (ARCHITECTURE.md, section 7): a migrator that raises contributes nothing, and the run goes on.

For every migrator M and every phase P, M.P raising gives the plan of the
registry without M, plus one Failure (and a "skipped" one for each migrator
that needs M). A plan raises after its real work, so its transaction has
pending entries that must be rolled back. Strict mode re-raises instead.
A secret in an exception message reaches no output. A failure of the core
(a document render, the guard) stops the run, and nothing is written.
"""

from __future__ import annotations

import contextlib
import copy
import io
import re
import unittest
from pathlib import Path
from unittest import mock

from dotkit_migrate import __main__ as cli
from dotkit_migrate import docs, registry, writer
from dotkit_migrate.migrators import Migrator
from dotkit_migrate.model import AdoptOptions
from dotkit_migrate.ui import screens
from tests import fake
from tests.test_golden import ADOPT_DECLARED, ADOPT_REPO, check_syntax, kitchen_sink, kitchen_sink_choices
from tests.test_session import used_mac

PHASES = ("scan", "pages", "plan")
TOKEN = fake.TOKENS["@GH@"]
PROBLEMS = re.compile(r"### Problems during the migration\n\n(?:- .*\n)+\n")
COUNT = re.compile(r"^MIGRATION\.md: (\d+) items to check by hand\.$")


def crashing(m, phase: str, error: Exception):
    """A patch: m's phase raises. A plan or an adopt does its real work first, so it has pending entries."""
    original = getattr(type(m), phase)

    def crash(self, *args):
        if phase in ("plan", "adopt"):
            original(self, *args)
        raise type(error)(*error.args)  # a new one each time, with a traceback of its own

    return mock.patch.object(type(m), phase, crash)


def without_problems(plan) -> dict:
    """The plan without what the failures add: the Problems section, the summary line, and its items."""
    n = len(plan.failures)
    report = PROBLEMS.sub("", plan.report)
    summary = list(plan.summary)
    if n:
        k = next(i for i, line in enumerate(summary) if not line.startswith("Problems: "))
        assert k and summary[k] == "", summary[:k + 1]
        summary = summary[k + 1:]
    summary = [f"MIGRATION.md: {int(COUNT.match(s).group(1)) - n} items to check by hand."
               if COUNT.match(s) else s for s in summary]
    files = {k: v for k, v in plan.files.items() if k != "MIGRATION.md"}
    return {"files": files, "copies": plan.copies, "secrets": plan.secrets, "links": plan.links,
            "move_links": plan.move_links, "blockers": plan.blockers, "report": report, "summary": summary}


class Isolated(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        for patch in (mock.patch("getpass.getuser", return_value="jane"),
                      mock.patch("socket.gethostname", return_value="test-mac.local")):
            patch.start()
            self.addCleanup(patch.stop)
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        self.runner, self.env = kitchen_sink(self.h)
        self.repo = str(self.h.home / ".dotfiles")

    def session(self, migrators=None, strict: bool = False) -> registry.Session:
        """A Session of the kitchen-sink Mac; by default with registry.MIGRATORS as it is when the test runs."""
        return registry.Session(fake.context_of(self.h, self.runner, self.env), migrators=migrators,
                                strict=strict, check_syntax=check_syntax)


class EveryMigratorTests(Isolated):
    def test_every_migrator_and_phase(self):
        """The plan of the registry without M, plus one Failure of M in that phase."""
        # Each migrator contributes to the kitchen-sink Mac, so leaving it out changes the plan.
        full = without_problems(self.build(self.session()))
        for m in registry.MIGRATORS:
            for phase in PHASES:
                with self.subTest(migrator=m.key, phase=phase):
                    with crashing(m, phase, KeyError("versions")):
                        s = self.session()
                        c = kitchen_sink_choices(s, self.repo)
                        plan = s.build(c)
                    rest = self.session(tuple(x for x in registry.MIGRATORS if x is not m), strict=True)
                    expected = rest.build(copy.deepcopy(c))
                    self.assertEqual(expected.failures, [])
                    self.assertEqual([(f.migrator, f.title, f.phase, f.error) for f in plan.failures],
                                     [(m.key, m.title, phase, "KeyError: 'versions'")])
                    f = plan.failures[0]
                    self.assertEqual(f.manual, m.manual_steps(s.ctx))
                    self.assertTrue(f.where.startswith("registry.py:"), f.where)
                    self.assertEqual(without_problems(plan), without_problems(expected))
                    self.assertNotEqual(without_problems(plan), full, "M contributes on this Mac")
                    if phase != "plan":
                        self.assertEqual([p.key for p in s.pages()], [p.key for p in rest.pages()])
                    self.assertEqual(s.scan().get(m.key) is None, phase == "scan")
                    # The scan and the pages fail once, in the Session; a plan fails in its build.
                    self.assertEqual(s.failures, plan.failures if phase != "plan" else [])
                    self.assertIn(f"### Problems during the migration\n\n- **{m.title}**: failed while ",
                                  plan.report)

    def build(self, s: registry.Session):
        return s.build(kitchen_sink_choices(s, self.repo))

    def test_every_mise_commands_hook(self):
        """Migrator.mise_commands raising fails its migrator as its scan would: the plan of the registry
        without M, one Failure of the scan, no facts of M, and strict mode re-raises."""
        for m in registry.MIGRATORS:
            with self.subTest(migrator=m.key), crashing(m, "mise_commands", KeyError("versions")):
                s = self.session()
                c = kitchen_sink_choices(s, self.repo)
                plan = s.build(c)
                self.assertEqual([(f.migrator, f.phase, f.error) for f in plan.failures],
                                 [(m.key, "scan", "KeyError: 'versions'")])
                self.assertTrue(plan.failures[0].where.startswith("registry.py:"), plan.failures[0].where)
                self.assertEqual(s.failures, plan.failures)
                self.assertIsNone(s.scan().get(m.key))
                rest = self.session(tuple(x for x in registry.MIGRATORS if x is not m), strict=True)
                self.assertEqual(s.scan().mise_commands, rest.scan().mise_commands)
                self.assertEqual(without_problems(plan), without_problems(rest.build(copy.deepcopy(c))))
                with self.assertRaises(KeyError):
                    self.session(strict=True).scan()

    def test_every_plan_fails(self):
        """Each failure starts the build again: one pass more than there are migrators, within the bound."""
        with contextlib.ExitStack() as stack:
            for m in registry.MIGRATORS:
                stack.enter_context(crashing(m, "plan", RuntimeError("boom")))
            plan = self.build(self.session())
        self.assertEqual([f.migrator for f in plan.failures], [m.key for m in registry.MIGRATORS])
        self.assertEqual(plan.copies, {})

    def test_the_problem_in_the_report_and_the_summary(self):
        rt = next(m for m in registry.MIGRATORS if m.key == "runtimes")
        with crashing(rt, "plan", KeyError("versions")):
            s = self.session()
            plan = self.build(s)
        f = plan.failures[0]
        self.assertIn(f"- **Runtime versions of version managers**: failed while planning: `KeyError: 'versions'` "
                      f"({f.where}). Nothing of it is in this repository. By hand: {rt.manual_steps(s.ctx)} "
                      "Details: `dotkit-migrate setup --dry-run --debug`. Please report it.\n", plan.report)
        self.assertEqual(plan.summary[0], "Problems: 1 part of the migration failed: runtimes (planning). It is "
                                          "left out; MIGRATION.md says what to do.")
        # The first section of "To do".
        self.assertEqual(plan.report.split("## To do\n\n", 1)[1].split("\n", 1)[0],
                         "### Problems during the migration")

    def test_where_is_the_last_frame_inside_the_package(self):
        with mock.patch("dotkit_migrate.migrators.runtimes.runtime_lines", side_effect=KeyError("versions")):
            plan = self.build(self.session())
        where = {f.migrator: f.where for f in plan.failures}
        self.assertRegex(where["runtimes"], r"^migrators/runtimes\.py:\d+ in pages$")

    def test_a_crash_that_depends_on_a_choice(self):
        """Each build has its own failures: the same Session plans without the crash for other choices."""
        shell = next(m for m in registry.MIGRATORS if m.key == "shell")
        original = type(shell).plan

        def plan(self, facts, choices, txn):
            original(self, facts, choices, txn)
            if choices.selected("shell", "shell:.zshrc"):
                raise RuntimeError("boom")

        with mock.patch.object(type(shell), "plan", plan):
            s = self.session()
            c = kitchen_sink_choices(s, self.repo)
            self.assertEqual([f.migrator for f in s.build(c).failures], ["shell"])
            c.picked["shell"].discard("shell:.zshrc")
            ok = s.build(c)
        self.assertEqual(ok.failures, [])
        self.assertIn("config/zsh/zprofile", ok.files)


class BashrcTests(Isolated):
    def test_bashrc_when_other_config_fails(self):
        """shell asks other_config.adopted(): without the facts of other config, ~/.bashrc gets its note."""
        other = next(m for m in registry.MIGRATORS if m.key == "other_config")
        note = "- ~/.bashrc: It stays in place. dotkit's ~/.bash_profile reads it in an interactive bash.\n"
        s = self.session()
        c = kitchen_sink_choices(s, self.repo)
        self.assertIn("other:.bashrc", c.picked["other"])
        self.assertNotIn(note, s.build(c).report, "carried: no note")
        with crashing(other, "plan", RuntimeError("boom")):
            plan = self.session().build(copy.deepcopy(c))
        self.assertEqual([f.migrator for f in plan.failures], ["other_config"])
        self.assertIn("### Other shell files\n\n" + note, plan.report)
        rest = self.session(tuple(m for m in registry.MIGRATORS if m is not other), strict=True)
        self.assertEqual(without_problems(plan), without_problems(rest.build(copy.deepcopy(c))))


class IdentityTests(Isolated):
    def test_identity(self):
        with mock.patch.object(registry.IdentityScan, "scan", side_effect=RuntimeError("boom")):
            self.env["USER"] = "jo"
            s = self.session()
            facts = s.scan()
        self.assertEqual([(f.migrator, f.phase) for f in s.failures], [("identity", "scan")])
        self.assertEqual((facts.identity.user, facts.identity.hostname, facts.identity.login_shell),
                         ("jo", "", ""))
        plan = self.build(s)
        self.assertIn('  name = "jo";\n', plan.files["nix/user.nix"])
        self.assertEqual(plan.blockers, [])
        self.assertIn("- **Your account and Git identity**: failed while scanning: `RuntimeError: boom`",
                      plan.report)
        self.assertNotIn("### Login shell", plan.report)
        self.assertEqual(s.failures[0].manual, "nix/user.nix uses the account `$USER`; check it, and your name and "
                                               "email in ~/.gitconfig.user.")

    def test_an_empty_user_is_a_blocker(self):
        with mock.patch.object(registry.IdentityScan, "scan", side_effect=RuntimeError("boom")):
            s = self.session()
            plan = self.build(s)
        self.assertEqual(s.scan().identity.user, "")
        self.assertEqual(plan.blockers, ["dotkit could not find the name of your macOS account. Set USER to it "
                                         "and run setup again."])

    def build(self, s):
        return s.build(s.defaults(self.repo))


class Needing(Migrator):
    """A migrator that cannot work without homebrew."""

    key = "needing"
    title = "Needs Homebrew"
    needs = ("homebrew",)
    sections = ("Homebrew taps",)

    def scan(self, ctx):
        return {}

    def plan(self, facts, choices, txn):
        txn.report.todo("Homebrew taps", "", ["needing ran"])

    def manual_steps(self, ctx):
        return "Do it by hand."


class NeedsTests(Isolated):
    def check(self, phase: str) -> None:
        brew = next(m for m in registry.MIGRATORS if m.key == "homebrew")
        # Before homebrew in the order: its plan ran already when homebrew's plan fails.
        with crashing(brew, phase, RuntimeError("boom")):
            s = self.session((Needing(), *registry.MIGRATORS))
            plan = s.build(s.defaults(self.repo))
        self.assertEqual([(f.migrator, f.phase, f.reason) for f in plan.failures],
                         [("homebrew", phase, ""), ("needing", "skipped", "Homebrew packages and taps failed")])
        self.assertNotIn("needing ran", plan.report)
        self.assertIn("- **Needs Homebrew**: skipped, because Homebrew packages and taps failed. Nothing of it is "
                      "in this repository. By hand: Do it by hand.\n", plan.report)

    def test_skipped_after_each_phase(self):
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.check(phase)

    def test_it_runs_when_its_needs_work(self):
        s = self.session((Needing(), *registry.MIGRATORS))
        plan = s.build(s.defaults(self.repo))
        self.assertEqual(plan.failures, [])
        self.assertIn("needing ran", plan.report)


class StrictTests(Isolated):
    def test_strict_re_raises(self):
        rt = next(m for m in registry.MIGRATORS if m.key == "runtimes")
        for phase in PHASES:
            with self.subTest(phase=phase), crashing(rt, phase, KeyError("versions")):
                s = self.session(strict=True)
                with self.assertRaises(KeyError):
                    s.build(kitchen_sink_choices(s, self.repo))
        with mock.patch.object(registry.IdentityScan, "scan", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.session(strict=True).scan()

    def test_fake_session_is_strict(self):
        self.assertTrue(fake.session(self.h, self.runner).strict)

    def test_strict_from_the_environment_and_the_hidden_flag(self):
        rt = next(m for m in registry.MIGRATORS if m.key == "runtimes")
        for args, env in ((["--strict"], {}), ([], {"DOTKIT_MIGRATE_STRICT": "1"})):
            with self.subTest(args=args), crashing(rt, "plan", KeyError("versions")), \
                    mock.patch.dict("os.environ", env):
                with self.assertRaises(KeyError), contextlib.redirect_stderr(io.StringIO()), \
                        contextlib.redirect_stdout(io.StringIO()):
                    main(self, ["setup", "--yes", "--dry-run", "--repo", self.repo, *args])


class Two(Migrator):
    """Adds a mise tool; two of them with different values are a conflict."""

    def __init__(self, key: str, value: str):
        self.key, self.title, self.value = key, f"Tool {key}", value

    sections = ()

    def scan(self, ctx):
        return {}

    def plan(self, facts, choices, txn):
        txn.mise().add_tool("npm:conflict-test", self.value)


class ConflictTests(Isolated):
    def test_a_conflict_is_a_problem(self):
        s = self.session((Two("first", "1.7"), Two("second", "1.6"), *registry.MIGRATORS))
        plan = s.build(s.defaults(self.repo))
        self.assertEqual([(f.migrator, f.title, f.phase) for f in plan.failures],
                         [("second", "Tool second", "conflict")])
        f = plan.failures[0]
        self.assertEqual((f.where, f.reason, f.error),
                         ("config/mise.toml", "npm:conflict-test", 'Tool first has "1.7", and Tool second wanted "1.6"'))
        line = ("config/mise.toml: two parts wanted different values for `npm:conflict-test`; the value of Tool "
                "second is left out.")
        self.assertIn(f'- **Tool second**: {line} Tool first has "1.7", and Tool second wanted "1.6". Please '
                      'report it.\n', plan.report)
        self.assertEqual(plan.summary[:2], [f"Problems: {line}", ""])
        self.assertNotIn("It is left out", "\n".join(plan.summary))
        self.assertEqual(screens.problem_lines(plan.failures)[1],
                         '  Tool second: config/mise.toml: two parts wanted different values for npm:conflict-test; '
                         'the value of Tool second is left out (Tool first has "1.7", and Tool second wanted "1.6").')
        self.assertIn('"npm:conflict-test" = "1.7"\n', plan.files["config/mise.toml"])

    def test_strict_raises_on_a_conflict(self):
        s = self.session((Two("first", "1.7"), Two("second", "1.6"), *registry.MIGRATORS), strict=True)
        with self.assertRaises(docs.ConflictError):
            s.build(s.defaults(self.repo))


class Warned(Migrator):
    key = "warned"
    title = "Warned"
    sections = ("Scan warnings",)

    def scan(self, ctx):
        class Found:
            warnings = ["warned: ~/.thing could not be read"]
        return Found()


class ScanWarningTests(Isolated):
    def test_the_warnings_of_a_failed_plan_are_gone(self):
        """A migrator whose plan fails contributes nothing, also no scan warnings."""
        warned = Warned()
        with crashing(warned, "plan", RuntimeError("boom")):
            s = self.session((*registry.MIGRATORS, warned))
            plan = s.build(s.defaults(self.repo))
        self.assertEqual([(f.migrator, f.phase) for f in plan.failures], [("warned", "plan")])
        self.assertNotIn("Scan warnings", plan.report)
        self.assertNotIn("~/.thing", plan.report)
        rest = self.session().build(s.defaults(self.repo))
        self.assertEqual(without_problems(plan), without_problems(rest))

    def test_scan_warnings(self):
        s = self.session((*registry.MIGRATORS, Warned()))
        plan = s.build(s.defaults(self.repo))
        self.assertEqual(plan.failures, [])
        self.assertIn("### Scan warnings\n\nPart of the scan could not read everything. Check these by hand.\n\n"
                      "- warned: ~/.thing could not be read\n", plan.report)


def main(test, argv, runner=None):
    """dotkit-migrate in this process, on the fake Mac of the test."""
    h = test.h
    return cli.main(argv, make_context=lambda _home, _base: fake.context_of(h, runner or test.runner, test.env))


class OutputTests(Isolated):
    """stderr, --debug, the exit code, and the masking of the error text."""

    def run_main(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.dict("os.environ", env or {}):
            code = main(self, argv)
        return code, out.getvalue(), err.getvalue()

    def test_exit_code_and_stderr(self):
        rt = next(m for m in registry.MIGRATORS if m.key == "runtimes")
        brew = next(m for m in registry.MIGRATORS if m.key == "homebrew")
        line_scan = ("Problems: 1 part of the migration failed: homebrew (scanning). It is left out; MIGRATION.md "
                     "says what to do.")
        line_all = ("Problems: 2 parts of the migration failed: homebrew (scanning), runtimes (planning). They are "
                    "left out; MIGRATION.md says what to do.")
        with crashing(rt, "plan", KeyError("versions")), crashing(brew, "scan", RuntimeError("boom")):
            code, out, err = self.run_main(["setup", "--yes", "--repo", self.repo])
        self.assertEqual(code, 0, err)
        lines = err.splitlines()
        self.assertEqual(lines.index(line_scan), 1, "after the scan")
        self.assertIn(line_all, lines, "after the final build")
        self.assertNotIn("Traceback", err)
        report = (Path(self.repo) / "MIGRATION.md").read_text(encoding="utf-8")
        self.assertIn("- **Runtime versions of version managers**: failed while planning:", report)
        self.assertIn("- **Homebrew packages and taps**: failed while scanning:", report)

    def test_debug_prints_the_traceback(self):
        rt = next(m for m in registry.MIGRATORS if m.key == "runtimes")
        for argv, env in ((["--debug"], {}), ([], {"DOTKIT_MIGRATE_DEBUG": "1"})):
            with self.subTest(argv=argv), crashing(rt, "plan", KeyError("versions")):
                code, out, err = self.run_main(["setup", "--yes", "--dry-run", "--repo", self.repo, *argv], env)
                self.assertEqual(code, 0, err)
                self.assertEqual(err.count("Traceback (most recent call last):"), 1, err)
                self.assertIn("KeyError: 'versions'", err)
                self.assertIn("Problems: 1 part of the migration failed: runtimes (planning)", out)
                self.assertFalse(Path(self.repo).exists())
        with crashing(rt, "plan", KeyError("versions")):
            code, out, err = self.run_main(["setup", "--yes", "--dry-run", "--repo", self.repo])
        self.assertNotIn("Traceback", err)

    def test_no_secret_in_the_error_text(self):
        """A token in the message: not in the report, the summary, stderr, or the --debug output."""
        shell = next(m for m in registry.MIGRATORS if m.key == "shell")
        home = str(self.h.home)
        error = RuntimeError(f"cannot read {home}/.zshrc\nGITHUB_TOKEN={TOKEN}")
        with crashing(shell, "plan", error):
            code, out, err = self.run_main(["setup", "--yes", "--dry-run", "--debug", "--repo", self.repo])
            s = self.session()
            plan = s.build(kitchen_sink_choices(s, self.repo))
        self.assertEqual(code, 0, err)
        for where, text in (("stdout", out), ("stderr", err), ("report", plan.report),
                            ("summary", "\n".join(plan.summary)), ("trace", s.trace(plan.failures[0]))):
            self.assertNotIn(TOKEN, text, where)
        self.assertIn("Traceback", err)
        self.assertIn("(this line has a secret; not shown)", err)
        self.assertEqual(plan.failures[0].error,
                         "RuntimeError: cannot read ~/.zshrc (this line has a secret; not shown)")
        self.assertNotIn(home, plan.failures[0].error)

    def test_a_secret_made_by_joining_the_lines(self):
        """A name on one line and its value on the next: one line after the join, so it is masked as a whole."""
        shell = next(m for m in registry.MIGRATORS if m.key == "shell")
        value = "abc123def456ghi"
        with crashing(shell, "plan", RuntimeError(f"//registry.npmjs.org/:_authToken=\n{value}")):
            s = self.session()
            plan = s.build(kitchen_sink_choices(s, self.repo))
        self.assertEqual(plan.failures[0].error, registry.SECRET_LINE)
        for where, text in (("report", plan.report), ("summary", "\n".join(plan.summary))):
            self.assertNotIn(value, text, where)
        self.assertEqual(registry.error_text(RuntimeError("a\nb"), ""), "RuntimeError: a b")
        # After the type of the exception, which secrets.line_secret reads as a name too.
        self.assertEqual(registry.error_text(RuntimeError(f"_authToken= {value}"), ""), registry.SECRET_LINE)
        self.assertNotIn(value, registry.trace_text(RuntimeError(f"_authToken= {value}"), ""))
        error = ValueError(f"bad entry: _authToken={value}")
        self.assertEqual(registry.error_text(error, ""), registry.SECRET_LINE)
        self.assertNotIn(value, registry.trace_text(error, ""))

    def test_a_long_error_is_cut(self):
        shell = next(m for m in registry.MIGRATORS if m.key == "shell")
        with crashing(shell, "plan", RuntimeError("x " * 300)):
            plan = self.session().build(kitchen_sink_choices(self.session(), self.repo))
        self.assertEqual(len(plan.failures[0].error), 200)
        self.assertTrue(plan.failures[0].error.endswith("…"))


def adopters():
    """The migrators that add to a repository in `dotkit migrate`: those that override Migrator.adopt."""
    return [m for m in registry.MIGRATORS if type(m).adopt is not Migrator.adopt]


class AdoptTests(Isolated):
    """`dotkit migrate`: a migrator whose adopt raises adds nothing, and the others still add."""

    def adopt(self, migrators=None, strict: bool = False):
        repo = self.h.home / ".dotfiles"
        for rel, text in ADOPT_REPO.items():
            self.h.write(f".dotfiles/{rel}", text)
        return self.session(migrators, strict).adopt(repo, ADOPT_DECLARED, AdoptOptions(all_tools=True))

    def test_every_adopter(self):
        full = self.adopt()
        self.assertEqual(full.failures, [])
        self.assertGreaterEqual(len(adopters()), 5)
        for m in adopters():
            with self.subTest(migrator=m.key), crashing(m, "adopt", RuntimeError("boom")):
                result = self.adopt()
                self.assertEqual([(f.migrator, f.phase, f.error) for f in result.failures],
                                 [(m.key, "adopt", "RuntimeError: boom")])
                self.assertTrue(result.failures[0].where.startswith("registry.py:"), result.failures[0].where)
                rest = self.adopt(tuple(x for x in registry.MIGRATORS if x is not m), strict=True)
                self.assertEqual((result.files, result.output), (rest.files, rest.output))
                self.assertNotEqual(result.output, full.output, "M adds on this Mac")
                with self.assertRaises(RuntimeError):
                    self.adopt(strict=True)

    def test_stderr(self):
        m = next(x for x in registry.MIGRATORS if x.key == "node_tools")
        err = io.StringIO()
        with crashing(m, "adopt", RuntimeError("boom")), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = main(self, ["adopt", "--repo", str(self.h.home / ".dotfiles"), "--all", "-y"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("Could not check: node_tools (failed while adding to the lists: RuntimeError: boom); nothing "
                      "of it was added.\n", err.getvalue())
        self.assertNotIn("npm:", out.getvalue())
        self.assertIn("pipx:black", out.getvalue())


class CoreTests(Isolated):
    """The core is not isolated: an exception there stops the run, and nothing is written."""

    def test_core_failures_write_nothing(self):
        for target in ("render", "guard"):
            patch = (mock.patch.object(docs.Docs, "render", side_effect=RuntimeError("render")) if target == "render"
                     else mock.patch.object(writer, "_guard_files", side_effect=RuntimeError("guard")))
            with self.subTest(target=target), patch:
                with self.assertRaises(RuntimeError), contextlib.redirect_stderr(io.StringIO()):
                    main(self, ["setup", "--yes", "--repo", self.repo])
                self.assertEqual([p.name for p in self.h.home.iterdir() if p.name.startswith(".dotfiles")], [])


class ManualStepsTests(unittest.TestCase):
    """Section 7.5: what the template does without shell and config_files, word for word."""

    def ctx(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        return fake.context_of(h, fake.FakeRunner())

    def text(self, key: str) -> str:
        return next(m for m in registry.MIGRATORS if m.key == key).manual_steps(self.ctx())

    def test_shell(self):
        self.assertEqual(self.text("shell"), (
            "Nothing of your shell files is carried. dotkit links its own ~/.zshenv, ~/.zprofile, ~/.zshrc, and "
            "~/.bash_profile, and the template's config/zsh/zshrc as ~/.zshrc.local. The first apply renames your "
            "files at these paths to <name>.before-dotkit. Your ~/.zshenv.local, ~/.zprofile.local, and "
            "~/.bash_profile.local stay in place if they exist, and dotkit's files source them, also if your old "
            "files did not. Check what they run (a framework, a version manager, PATH changes). They stay outside "
            "the repository. Copy the lines you need from the .before-dotkit files to config/zsh/ and config/bash/ "
            "of this repository, and keep lines with secrets out of files that Git tracks."))

    def test_config_files(self):
        self.assertEqual(self.text("config_files"), (
            "None of your config files is carried or kept in place. dotkit links its own files at "
            "~/.config/git/config, ~/.config/git/ignore, ~/.config/ghostty/config, ~/.config/herdr/config.toml, "
            "~/.config/starship.toml, ~/.config/uv/uv.toml, ~/.npmrc, ~/.gemrc, ~/.bunfig.toml, ~/.editorconfig, "
            "and ~/Library/Preferences/pnpm/config.yaml, and links config/mise.toml of this repository as "
            "~/.config/mise/config.toml. The first apply renames your files at these paths to <name>.before-dotkit. "
            "They stay on disk, outside the repository, and the tools no longer read them. The tools of your old "
            "mise config are not in config/mise.toml. If a file with a login, such as ~/.npmrc with a token, was "
            "renamed, log in again or copy the token by hand; do not put it into Git. ~/.gitconfig stays in place, "
            "and Git still reads it after the dotkit settings. Copy the settings you need from the .before-dotkit "
            "files."))


class UsedMacTests(unittest.TestCase):
    def test_a_used_mac_has_no_failures(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        s = fake.session(h, used_mac(h))
        self.assertEqual(s.build(s.defaults(str(h.home / ".dotfiles"))).failures, [])
        self.assertEqual(s.scan_warning(), "")


if __name__ == "__main__":
    unittest.main()
