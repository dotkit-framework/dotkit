"""The flow of the wizard's screens, without a terminal: a scripted frontend answers them by id."""

from __future__ import annotations

import io
import unittest
from unittest import mock

from dotkit_migrate.ui import plain, screens
from tests import fake
from tests.test_plan import used_mac


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

    def show(self, screen: screens.Screen, step: int, total: int) -> int:
        self.shown.append(screen.id)
        self.steps.append((step, total))
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
        moves = self.moves.get(screen.id)
        return moves.pop(0) if moves else 1


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)

    def planner(self, runner=None):
        return fake.planner(fake.scanner(self.h, runner if runner is not None else used_mac(self.h)).run())

    def run_flow(self, p, **kw):
        c = p.defaults(str(self.h.home / ".dotfiles"))
        ui = ScriptedFrontend(**kw)
        ok = screens.run(ui, screens.build(p, c, str(self.h.home)))
        return ok, c, ui

    def test_page_order(self):
        p = self.planner()
        ok, _, ui = self.run_flow(p)
        self.assertTrue(ok)
        keys = [page.key for page in p.pages()]
        self.assertEqual(ui.shown, ["repo", "keymap", *keys, "review"])
        self.assertEqual(keys, ["formulae", "casks", "taps", "mas", "npm", "pipx", "cargo", "runtimes", "shell",
                                "files", "other"])
        total = len(keys) + 3
        self.assertEqual(ui.steps, [(i, total) for i in range(1, total + 1)])

    def test_empty_pages_are_skipped(self):
        ok, _, ui = self.run_flow(self.planner(fake.FakeRunner()))
        self.assertTrue(ok)
        self.assertEqual(ui.shown, ["repo", "keymap", "review"])
        # Only formulae and the default tap of Homebrew: no screen for casks, taps, and the rest.
        ok, _, ui = self.run_flow(self.planner(fake.brew_runner(formulae=["wget"])))
        self.assertEqual(ui.shown, ["repo", "keymap", "formulae", "review"])

    def test_defaults(self):
        p = self.planner()
        ok, c, ui = self.run_flow(p)
        self.assertTrue(ok)
        self.assertEqual(c, p.defaults(str(self.h.home / ".dotfiles")))
        self.assertNotIn("Not carried, by your choice:", ui.review)
        self.assertEqual(ui.review, p.build(c).summary)

    def test_single_items(self):
        p = self.planner()
        ok, c, ui = self.run_flow(p, answers={
            "keymap": {"vi": True},
            "formulae": {"brew:hashicorp/tap/terraform": False},
            "cargo": {"cargo:bat": True},
            "shell": {"shell:.zshrc": False},
            "files": {"file:.config/ghostty/config": "replace"},
            "other": {"other:.config/nvim": True},
        })
        self.assertTrue(ok)
        self.assertEqual(c.keymap, "vi")
        self.assertEqual(c.picked["formulae"], {"brew:wget"})
        self.assertEqual(c.picked["cargo"], {"cargo:bat"})
        self.assertEqual(c.picked["shell"], {"shell:.zprofile", "shell:.bash_profile"})
        self.assertEqual(c.picked["other"], {"other:.config/nvim"})
        self.assertEqual(c.option("file:.config/ghostty/config"), "replace")
        # Two items that were on by default are left out; cargo was off and is on now.
        i = ui.review.index("Not carried, by your choice:")
        self.assertEqual(ui.review[i + 1:], ["  Homebrew formulae: 1", "  Shell lines: 1"])
        plan = p.build(c)
        self.assertEqual(ui.review[:i - 1], plan.summary)
        self.assertIn("- `hashicorp/tap/terraform` (formula): `dotkit add brew:hashicorp/tap/terraform`\n",
                      plan.report)
        self.assertIn("1 cargo tool -> config/mise.toml", plan.summary)

    def test_an_option_that_the_item_does_not_have(self):
        p = self.planner()
        ok, c, ui = self.run_flow(p, answers={"files": {"file:.npmrc": "replace"}})
        self.assertTrue(ok)
        self.assertEqual(ui.errors["file:.npmrc"], "replace is not one of: keep, dotkit")
        self.assertEqual(c.option("file:.npmrc"), "keep")

    def test_a_repository_outside_the_home_directory(self):
        p = self.planner()
        ok, c, ui = self.run_flow(p, answers={"repo": "/tmp/elsewhere"}, quit_at="keymap")
        self.assertFalse(ok)
        self.assertIn("Use a directory in", ui.errors["repo"])
        self.assertEqual(c.repo, str(self.h.home / ".dotfiles"))
        ok, c, ui = self.run_flow(p, answers={"repo": "~/dots"})
        self.assertEqual((ok, c.repo, ui.errors["repo"]), (True, str(self.h.home / "dots"), ""))

    def test_back_rebuilds_the_plan(self):
        p = self.planner()
        ui = ScriptedFrontend(moves={"review": [-1], "other": [1, 1]}, answers={"other": {"other:.bashrc": True}})
        c = p.defaults(str(self.h.home / ".dotfiles"))
        self.assertTrue(screens.run(ui, screens.build(p, c, str(self.h.home))))
        self.assertEqual(ui.shown[-3:], ["review", "other", "review"])
        self.assertIn("1 other config file or directory -> config/, linked with dotkit.links", ui.review)

    def test_back_on_the_first_screen_stays(self):
        p = self.planner()
        ui = ScriptedFrontend(moves={"repo": [-1]})
        c = p.defaults(str(self.h.home / ".dotfiles"))
        self.assertTrue(screens.run(ui, screens.build(p, c, str(self.h.home))))
        self.assertEqual(ui.shown[:3], ["repo", "repo", "keymap"])

    def test_quit(self):
        ok, _, ui = self.run_flow(self.planner(), quit_at="files")
        self.assertFalse(ok)
        self.assertEqual(ui.shown[-1], "files")
        self.assertEqual(ui.review, [])

    def test_all_or_none(self):
        p = self.planner()
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
        p = self.planner()
        c = p.defaults(str(self.h.home / ".dotfiles"))
        by_id = {s.id: s for s in screens.build(p, c, str(self.h.home))}
        cargo = by_id["cargo"]
        self.assertEqual((cargo.kind, cargo.title, cargo.dest), ("checklist", "cargo install tools",
                                                                 "config/mise.toml"))
        self.assertEqual(cargo.note, "off: mise builds them from source with cargo")
        self.assertEqual(cargo.detail("cargo:bat"), "0.24.0 in cargo install")
        files = by_id["files"]
        self.assertEqual(files.kind, "choices")
        npmrc = next(r for r in files.rows if r.id == "file:.npmrc")
        self.assertEqual(npmrc.options, ("keep", "dotkit"))
        self.assertEqual(set(npmrc.option_text), {"keep", "dotkit"})
        self.assertEqual(files.get("file:.npmrc"), "keep")


class PlainTests(unittest.TestCase):
    """The answers of a checklist page in the plain prompts."""

    def setUp(self):
        self.h = fake.Home()
        self.addCleanup(self.h.cleanup)
        p = fake.planner(fake.scanner(self.h, used_mac(self.h)).run())
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
