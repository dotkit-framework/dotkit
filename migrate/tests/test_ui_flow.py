"""The flow of the wizard's screens, without a terminal: a scripted frontend answers them by id."""

from __future__ import annotations

import io
import unittest
from unittest import mock

from dotkit_migrate import registry
from dotkit_migrate.ui import plain, screens
from tests import fake
from tests.test_session import used_mac


class ScriptedFrontend(screens.Frontend):
    """Answers the screens by screen id and item id, and records what it was shown.

    answers: screen id -> the text (text screens), or {row id: value} (True/False for
    radio and checklist rows, an option for choices rows). moves: screen id -> the
    moves to make there, one per visit (1 next, -1 back); then 1. quit_at: the
    screen id where the person quits.
    """

    def __init__(self, answers=None, moves=None, quit_at: str = ""):
        self.answers = answers or {}
        self.moves = {k: list(v) for k, v in (moves or {}).items()}
        self.quit_at = quit_at
        self.shown: list[str] = []
        self.steps: list[tuple[int, int]] = []
        self.errors: dict[str, str] = {}
        self.review: list[str] = []
        self.problems: list[str] = []
        self.warnings: dict[str, str] = {}  # screen id -> its warning line

    def show(self, screen: screens.Screen, step: int, total: int) -> int:
        self.shown.append(screen.id)
        self.steps.append((step, total))
        if screen.warning:
            self.warnings[screen.id] = screen.warning
        if screen.id == self.quit_at:
            raise screens.Quit()
        answer = self.answers.get(screen.id)
        if screen.kind == "text" and answer is not None:
            self.errors[screen.id] = screen.set(screen.id, answer)
        elif isinstance(answer, dict):
            rows = [r.id for r in screen.rows]
            for row, value in answer.items():
                if row not in rows:
                    raise AssertionError(f"{screen.id} has no row {row}: {rows}")
                self.errors[row] = screen.set(row, value)
        if screen.kind == "review":
            self.review = screen.lines()
            self.problems = screen.problems()
        moves = self.moves.get(screen.id)
        return moves.pop(0) if moves else 1


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def session(self, runner=None):
        return fake.session(self.h, runner if runner is not None else used_mac(self.h))

    def run_flow(self, p, **kw):
        c = p.defaults(str(self.h.home / ".dotfiles"))
        ui = ScriptedFrontend(**kw)
        ok = screens.run(ui, screens.build(p, c, str(self.h.home)))
        return ok, c, ui

    def test_page_order(self):
        p = self.session()
        ok, _, ui = self.run_flow(p)
        self.assertTrue(ok)
        keys = [page.key for page in p.pages()]
        self.assertEqual(ui.shown, ["repo", "keymap", *keys, "review"])
        self.assertEqual(keys, ["formulae", "casks", "taps", "mas", "npm", "pipx", "runtimes", "shell", "files",
                                "other"])
        total = len(keys) + 3
        self.assertEqual(ui.steps, [(i, total) for i in range(1, total + 1)])

    def test_empty_pages_are_skipped(self):
        ok, _, ui = self.run_flow(self.session(fake.FakeRunner()))
        self.assertTrue(ok)
        self.assertEqual(ui.shown, ["repo", "keymap", "review"])
        # Only formulae and the default tap of Homebrew: no screen for casks, taps, and the rest.
        ok, _, ui = self.run_flow(self.session(fake.brew_runner(formulae=["wget"])))
        self.assertEqual(ui.shown, ["repo", "keymap", "formulae", "review"])

    def test_defaults(self):
        p = self.session()
        ok, c, ui = self.run_flow(p)
        self.assertTrue(ok)
        self.assertEqual(c, p.defaults(str(self.h.home / ".dotfiles")))
        self.assertNotIn("Not carried, by your choice:", ui.review)
        self.assertEqual(ui.review, p.build(c).summary)

    def test_single_items(self):
        p = self.session()
        ok, c, ui = self.run_flow(p, answers={
            "keymap": {"vi": True},
            "formulae": {"brew:hashicorp/tap/terraform": False},
            "runtimes": {"runtime:node": True},
            "shell": {"shell:.zshrc": False},
            "files": {"file:.config/ghostty/config": "replace"},
            "other": {"other:.config/nvim": True},
        })
        self.assertTrue(ok)
        self.assertEqual(c.keymap, "vi")
        self.assertEqual(c.picked["formulae"], {"brew:wget"})
        self.assertEqual(c.picked["runtimes"], {"runtime:node"})
        self.assertEqual(c.picked["shell"], {"shell:.zprofile", "shell:.bash_profile"})
        self.assertEqual(c.picked["other"], {"other:.config/nvim"})
        self.assertEqual(c.option("file:.config/ghostty/config"), "replace")
        # Two items that were on by default are left out; runtimes was off and is on now.
        i = ui.review.index("Not carried, by your choice:")
        self.assertEqual(ui.review[i + 1:], ["  Homebrew formulae: 1", "  Shell lines: 1"])
        plan = p.build(c)
        self.assertEqual(ui.review[:i - 1], plan.summary)
        self.assertIn("- `hashicorp/tap/terraform` (formula): `dotkit add brew:hashicorp/tap/terraform`\n",
                      plan.report)
        self.assertIn("runtime versions of nvm -> config/mise.toml", plan.summary)

    def test_an_option_that_the_item_does_not_have(self):
        p = self.session()
        ok, c, ui = self.run_flow(p, answers={"files": {"file:.npmrc": "replace"}})
        self.assertTrue(ok)
        self.assertEqual(ui.errors["file:.npmrc"], "replace is not one of: tokens, dotkit, keep")
        self.assertEqual(c.option("file:.npmrc"), "tokens")

    def test_a_repository_outside_the_home_directory(self):
        p = self.session()
        ok, c, ui = self.run_flow(p, answers={"repo": "/tmp/elsewhere"}, quit_at="keymap")
        self.assertFalse(ok)
        self.assertIn("Use a directory in", ui.errors["repo"])
        self.assertEqual(c.repo, str(self.h.home / ".dotfiles"))
        ok, c, ui = self.run_flow(p, answers={"repo": "~/dots"})
        self.assertEqual((ok, c.repo, ui.errors["repo"]), (True, str(self.h.home / "dots"), ""))

    def test_back_rebuilds_the_plan(self):
        p = self.session()
        ui = ScriptedFrontend(moves={"review": [-1], "other": [1, 1]}, answers={"other": {"other:.bashrc": True}})
        c = p.defaults(str(self.h.home / ".dotfiles"))
        self.assertTrue(screens.run(ui, screens.build(p, c, str(self.h.home))))
        self.assertEqual(ui.shown[-3:], ["review", "other", "review"])
        self.assertIn("1 other config file or directory -> config/, linked with dotkit.links", ui.review)

    def test_back_on_the_first_screen_stays(self):
        p = self.session()
        ui = ScriptedFrontend(moves={"repo": [-1]})
        c = p.defaults(str(self.h.home / ".dotfiles"))
        self.assertTrue(screens.run(ui, screens.build(p, c, str(self.h.home))))
        self.assertEqual(ui.shown[:3], ["repo", "repo", "keymap"])

    def test_quit(self):
        ok, _, ui = self.run_flow(self.session(), quit_at="files")
        self.assertFalse(ok)
        self.assertEqual(ui.shown[-1], "files")
        self.assertEqual(ui.review, [])

    def test_all_or_none(self):
        p = self.session()
        c = p.defaults(str(self.h.home / ".dotfiles"))
        by_id = {s.id: s for s in screens.build(p, c, str(self.h.home))}
        screens.toggle_all(by_id["taps"])  # all selected: none
        self.assertEqual(c.picked["taps"], set())
        screens.toggle_all(by_id["taps"])  # none: all
        self.assertEqual(c.picked["taps"], {"tap:hashicorp/tap", "tap:acme/tools"})
        by_id["other"].set("other:.bashrc", True)  # some: all
        screens.toggle_all(by_id["other"])
        self.assertEqual(c.picked["other"], {"other:.config/nvim", "other:.bashrc"})

    def test_screens_show_the_pages(self):
        p = self.session()
        c = p.defaults(str(self.h.home / ".dotfiles"))
        by_id = {s.id: s for s in screens.build(p, c, str(self.h.home))}
        runtimes = by_id["runtimes"]
        self.assertEqual((runtimes.kind, runtimes.title, runtimes.dest),
                         ("checklist", "Runtime versions of version managers", "config/mise.toml"))
        self.assertEqual(runtimes.note, "off: mise installs them; the base has Node 24, Python 3.14, and Ruby 3.4")
        self.assertEqual(runtimes.detail("runtime:node"), "From nvm")
        self.assertNotIn("cargo", by_id, "dotkit has bat already: no cargo page")
        files = by_id["files"]
        self.assertEqual(files.kind, "choices")
        npmrc = next(r for r in files.rows if r.id == "file:.npmrc")
        self.assertEqual(npmrc.options, ("tokens", "dotkit", "keep"))
        self.assertEqual(set(npmrc.option_text), {"tokens", "dotkit", "keep"})
        self.assertEqual(files.get("file:.npmrc"), "tokens")


class ProblemTests(unittest.TestCase):
    """Section 8: the warning line of the first screen, and the Problems block of the plan screen."""

    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def migrator(self, key: str):
        return next(m for m in registry.MIGRATORS if m.key == key)

    def run_flow(self, **kw):
        p = fake.session(self.h, used_mac(self.h), strict=False)
        c = p.defaults(str(self.h.home / ".dotfiles"))
        ui = ScriptedFrontend(**kw)
        self.assertTrue(screens.run(ui, screens.build(p, c, str(self.h.home))))
        return p, c, ui

    def test_no_problems(self):
        _, _, ui = self.run_flow()
        self.assertEqual((ui.warnings, ui.problems), ({}, []))

    def test_the_warning_line(self):
        """A scan or pages that failed: no screen, and one line under the intro of the first screen."""
        with mock.patch.object(type(self.migrator("runtimes")), "scan", side_effect=RuntimeError("boom")):
            _, _, ui = self.run_flow()
        self.assertEqual(ui.warnings, {"repo": "1 part of the scan failed: Runtime versions of version managers. "
                                               "It is left out; the plan screen and MIGRATION.md say more."})
        self.assertNotIn("runtimes", ui.shown)
        self.assertEqual(ui.problems[:2], ["Problems:", "  Runtime versions of version managers: failed while "
                                                        "scanning: RuntimeError: boom."])
        with mock.patch.object(type(self.migrator("runtimes")), "scan", side_effect=RuntimeError("boom")), \
                mock.patch.object(type(self.migrator("other_config")), "pages", side_effect=KeyError("x")):
            _, _, ui = self.run_flow()
        self.assertEqual(ui.warnings["repo"], "2 parts of the scan failed: Runtime versions of version managers, "
                                              "Other config. They are left out; the plan screen and MIGRATION.md "
                                              "say more.")
        self.assertNotIn("other", ui.shown)

    def test_the_problems_block(self):
        """A plan that failed: its page is there, and the plan screen says that its choices have no effect."""
        shell = self.migrator("shell")
        with mock.patch.object(type(shell), "plan", side_effect=RuntimeError("boom")):
            p, c, ui = self.run_flow()
        self.assertIn("shell", ui.shown)
        self.assertEqual(ui.warnings, {}, "the scan worked")
        self.assertEqual(ui.problems, [
            "Problems:",
            "  Shell lines: failed while planning: RuntimeError: boom.",
            "  Their part is left out, so its choices have no effect. MIGRATION.md says what to do by hand.",
            "  b goes back: a changed choice builds the plan again.",
            "",
        ])
        self.assertEqual(ui.review[0], "Problems: 1 part of the migration failed: shell (planning). It is left "
                                       "out; MIGRATION.md says what to do.")

    def test_back_after_a_crash_that_depends_on_a_choice(self):
        shell = self.migrator("shell")
        original = type(shell).plan

        def plan(self, facts, choices, txn):
            if choices.selected("shell", "shell:.zshrc"):
                raise RuntimeError("boom")
            original(self, facts, choices, txn)

        with mock.patch.object(type(shell), "plan", plan):
            p, c, ui = self.run_flow(moves={"review": [-1]})
            self.assertEqual(ui.shown[-3:], ["review", "other", "review"])
            self.assertEqual(ui.problems[1], "  Shell lines: failed while planning: RuntimeError: boom.")
            # Back on the shell page, ~/.zshrc off: the plan screen builds the plan again, without the problem.
            by_id = {x.id: x for x in screens.build(p, c, str(self.h.home))}
            by_id["shell"].set("shell:.zshrc", False)
            review = by_id["review"]
            self.assertIn("1 line of ~/.zprofile -> config/zsh/zprofile (~/.zprofile.local)", review.lines())
            self.assertEqual(review.problems(), [])

    def test_plain_prints_the_warning_line_first(self):
        with mock.patch.object(type(self.migrator("runtimes")), "scan", side_effect=RuntimeError("boom")):
            p = fake.session(self.h, used_mac(self.h), strict=False)
            c = p.defaults(str(self.h.home / ".dotfiles"))
            first = screens.build(p, c, str(self.h.home))[0]
        stdin, stdout = io.StringIO("\n"), io.StringIO()
        with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", stdout):
            plain.Plain(False).show(first, 1, 1)
        self.assertTrue(stdout.getvalue().startswith("1 part of the scan failed: Runtime versions of version "
                                                     "managers. It is left out;"), stdout.getvalue())


class PlainTests(unittest.TestCase):
    """The answers of a checklist page in the plain prompts."""

    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        p = fake.session(self.h, used_mac(self.h))
        self.c = p.defaults(str(self.h.home / ".dotfiles"))
        self.shell = next(s for s in screens.build(p, self.c, str(self.h.home)) if s.id == "shell")

    def answer(self, *lines: str) -> str:
        """Shows the shell page (3 items) with these input lines. Returns the output; all lines must be read."""
        stdin, stdout = io.StringIO("".join(line + "\n" for line in lines)), io.StringIO()
        with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", stdout):
            self.assertEqual(plain.Plain(False).show(self.shell, 1, 1), 1)
        self.assertEqual(stdin.read(), "", "every answer was read")
        return stdout.getvalue()

    def test_toggles(self):
        self.assertEqual(plain.toggles("1 3-5", 5), {0, 2, 3, 4})
        self.assertEqual(plain.toggles("2,3", 3), {1, 2})
        for bad in ("0", "4", "3-1", "x", "1-x", "-2", "2-", "1--2", "\u00b2"):
            with self.subTest(bad=bad):
                self.assertIsNone(plain.toggles(bad, 3))

    def test_an_answer_that_does_not_parse_asks_again(self):
        out = self.answer("0", "4", "3-1", "x", "2")
        self.assertEqual(out.count("Answer with numbers from 1 to 3, ranges like 1-3, a, or n."), 4)
        self.assertEqual(self.c.picked["shell"], {"shell:.zprofile", "shell:.bash_profile"})
        self.assertIn("2 of 3 selected.", out)

    def test_all_none_and_enter(self):
        self.answer("n")
        self.assertEqual(self.c.picked["shell"], set())
        self.answer("a")
        self.assertEqual(len(self.c.picked["shell"]), 3)
        self.answer("")
        self.assertEqual(len(self.c.picked["shell"]), 3)

    def test_end_of_input_quits(self):
        with mock.patch("sys.stdin", io.StringIO("")), mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(screens.Quit):
                plain.Plain(False).show(self.shell, 1, 1)


if __name__ == "__main__":
    unittest.main()
