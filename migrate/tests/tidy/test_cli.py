"""The tidy mode: the plan file, the confirmation, the actions, and the list edits (TIDY.md, sections 2 and 6)."""

import argparse
import contextlib
import io
import os
import plistlib
import unittest
from unittest import mock

from dotkit_migrate.tidy import cli
from tests.tidy.mac import TidyMac


class FakeActions:
    """Records the calls. fail: the tokens that brew does not install; interrupt: the call that raises Ctrl-C."""

    def __init__(self, brew, fail=(), interrupt="", stuck=()):
        self.brew = brew
        self.calls = []
        self.fail, self.interrupt, self.stuck = set(fail), interrupt, set(stuck)

    def _call(self, call):
        self.calls.append(call)
        if call == self.interrupt:
            raise KeyboardInterrupt

    def brew_install(self, token, how):
        self._call(f"brew install --cask --{how} {token}")
        return token not in self.fail

    def quit_app(self, bundle_id, path, wait=30):
        self._call(f"quit {bundle_id}")
        return bundle_id not in self.stuck

    def trash(self, path):
        self._call(f"trash {os.path.basename(path)}")
        return True, ""


class Answer(io.StringIO):
    """stdin for the question: a terminal that answers, and runs a hook first."""

    def __init__(self, text, hook=None):
        super().__init__(text)
        self.hook = hook

    def isatty(self):
        return True

    def readline(self, *args):
        if self.hook:
            self.hook()
        return super().readline(*args)


class TidyTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch("socket.gethostname", return_value="test-mac.local"),):
            patch.start()
            self.addCleanup(patch.stop)
        m = self.m = TidyMac(self)
        m.app("Slack.app", "com.tinyspeck.slackmacgap")
        m.cask("slack", apps=["Slack.app"], quit=["com.tinyspeck.slackmacgap"], auto_updates=True)
        m.app("Joplin.app", "net.cozic.joplin-desktop", "3.6.14")
        m.cask("joplin", apps=["Joplin.app"], quit=["net.cozic.joplin-desktop"], version="3.7.21")
        m.app("Old.app", "com.example.old")
        m.app("Zed.app", "dev.zed.Zed")
        m.cask("zed", apps=["Zed.app"], installed="1.0")
        m.paths["mas"] = "/opt/homebrew/bin/mas"
        m.commands["mas list"] = "409201541  Pages  (14.5)\n"
        m.app("Pages.app", "com.apple.iWork.Pages", mas=409201541)
        self.repo = m.h.home / ".dotfiles"
        (self.repo / "nix").mkdir(parents=True)
        (self.repo / "nix/casks.txt").write_text("ghostty\n")
        self.actions = None

    def run_tidy(self, plan_text=None, answer="y\n", hook=None, dry_run=False, yes=False, **fake):
        if plan_text is not None:
            p = self.m.h.root / "plan.txt"
            p.write_text(plan_text)
        a = argparse.Namespace(repo=str(self.repo), declared=None, dry_run=dry_run, yes=yes, base=None,
                               plan=str(self.m.h.root / "plan.txt") if plan_text is not None else None)
        runner = self.m.runner()
        ctx = self.m.context(runner)

        def make_actions(brew):
            self.actions = FakeActions(brew, **fake)
            return self.actions

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("sys.stdin", Answer(answer, hook)):
            code = cli.cmd_tidy(a, lambda home, b: ctx, make_actions,
                                edit=lambda text, env: self.fail("the editor opens only without --plan"))
        return code, out.getvalue(), err.getvalue()

    def app(self, name):
        return str(self.m.h.apps / name)

    def casks(self):
        return (self.repo / "nix/casks.txt").read_text()

    def test_dry_run_prints_the_plan_and_changes_nothing(self):
        code, out, err = self.run_tidy(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn(f"adopt    cask:slack     {self.app('Slack.app')}", out)
        self.assertIn(f"add      cask:zed       {self.app('Zed.app')}", out)
        self.assertIn(f"add      mas:409201541  {self.app('Pages.app')}", out)
        self.assertIn("With these verbs, tidy runs:\n  brew install --cask --adopt slack", out)
        self.assertIn("No change.", err)
        self.assertIsNone(self.actions)
        self.assertEqual(self.casks(), "ghostty\n")

    def test_a_plan_runs_line_by_line_and_adds_to_the_lists(self):
        plan = "\n".join([f"adopt cask:slack {self.app('Slack.app')}",
                          f"replace cask:joplin {self.app('Joplin.app')}",
                          f"trash {self.app('Old.app')}",
                          f"add cask:zed {self.app('Zed.app')}",
                          f"add mas:409201541 {self.app('Pages.app')}"])
        code, out, err = self.run_tidy(plan)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.actions.calls, ["brew install --cask --adopt slack", "quit net.cozic.joplin-desktop",
                                              "brew install --cask --force joplin", "quit com.example.old",
                                              "trash Old.app"])
        self.assertEqual(self.casks(), "ghostty\n# Added by dotkit tidy on test-mac.\nslack\njoplin\nzed\n")
        self.assertEqual((self.repo / "nix/mas.txt").read_text(),
                         "# Added by dotkit tidy on test-mac.\n409201541 Pages\n")
        self.assertIn("Tidy runs:\n  brew install --cask --adopt slack", out)
        self.assertIn("In the Trash:\n  " + self.app("Old.app"), out)
        self.assertIn("the next dotkit apply records them", err)

    def test_the_question_says_no(self):
        code, _, err = self.run_tidy(f"adopt cask:slack {self.app('Slack.app')}\n", answer="n\n")
        self.assertEqual((code, self.actions), (1, None))
        self.assertIn("nothing changed", err)
        self.assertEqual(self.casks(), "ghostty\n")

    def test_a_failed_line_adds_nothing_and_the_others_go_on(self):
        plan = f"adopt cask:slack {self.app('Slack.app')}\nreplace cask:joplin {self.app('Joplin.app')}\n"
        code, out, err = self.run_tidy(plan, fail=["slack"])
        self.assertEqual(code, 1)
        self.assertEqual(self.casks(), "ghostty\n# Added by dotkit tidy on test-mac.\njoplin\n")
        self.assertIn("brew could not adopt it, often because the app differs from the cask slack. To use the cask, "
                      "change the line to replace.", out)

    def test_an_app_that_does_not_quit(self):
        code, out, _ = self.run_tidy(f"replace cask:joplin {self.app('Joplin.app')}\n",
                                     stuck=["net.cozic.joplin-desktop"])
        self.assertEqual(code, 1)
        self.assertEqual(self.actions.calls, ["quit net.cozic.joplin-desktop"])
        self.assertIn("it does not quit. Quit it, then run dotkit tidy again.", out)

    def test_ctrl_c_keeps_the_lines_that_worked(self):
        plan = f"adopt cask:slack {self.app('Slack.app')}\nreplace cask:joplin {self.app('Joplin.app')}\n"
        code, _, err = self.run_tidy(plan, interrupt="quit net.cozic.joplin-desktop")
        self.assertEqual(code, 130)
        self.assertEqual(self.casks(), "ghostty\n# Added by dotkit tidy on test-mac.\nslack\n")
        self.assertIn("stopped", err)

    def test_an_app_that_changed_since_the_scan_is_skipped(self):
        def update():
            with open(self.m.h.apps / "Slack.app/Contents/Info.plist", "wb") as f:
                plistlib.dump({"CFBundleIdentifier": "com.tinyspeck.slackmacgap",
                               "CFBundleShortVersionString": "9.9"}, f)

        code, out, _ = self.run_tidy(f"adopt cask:slack {self.app('Slack.app')}\n", hook=update)
        self.assertEqual((code, self.actions.calls), (1, []))
        self.assertIn("it changed since the scan. Run dotkit tidy again.", out)
        self.assertEqual(self.casks(), "ghostty\n")

    def test_a_bad_plan_changes_nothing(self):
        code, _, err = self.run_tidy(f"adopt {self.app('Slack.app')}\nadopt cask:slack /Applications/Nope.app\n")
        self.assertEqual((code, self.actions), (2, None))
        self.assertIn("dotkit tidy: line 1: adopt needs a cask", err)
        self.assertIn("dotkit tidy: line 2: /Applications/Nope.app is not an app of the scan.", err)

    def test_an_empty_plan_and_a_plan_of_keep_lines(self):
        self.assertEqual(self.run_tidy("# nothing\n\n")[0], 1)
        code, out, _ = self.run_tidy(f"keep {self.app('Slack.app')}\n")
        self.assertEqual(code, 0)
        self.assertIn("Every line is keep.", out)
        self.assertIsNone(self.actions)

    def test_yes_applies_the_default_verbs(self):
        code, _, err = self.run_tidy(yes=True, answer="")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.actions.calls, ["brew install --cask --adopt slack", "quit net.cozic.joplin-desktop",
                                              "brew install --cask --force joplin"])
        self.assertEqual(self.casks(), "ghostty\n# Added by dotkit tidy on test-mac.\nslack\njoplin\nzed\n")

    def test_the_cleanup_hint_when_the_lists_have_every_cask(self):
        self.m.commands["brew list --cask --full-name -1"] = "ghostty\nslack\n"
        _, _, err = self.run_tidy(f"adopt cask:slack {self.app('Slack.app')}\n")
        self.assertIn("The lists have every installed cask now.", err)
        self.m.commands["brew list --cask --full-name -1"] = "ghostty\nslack\nother\n"
        _, _, err = self.run_tidy(f"adopt cask:slack {self.app('Slack.app')}\n")
        self.assertNotIn("The lists have every installed cask now.", err)

    def test_without_a_terminal_and_without_a_plan(self):
        a = argparse.Namespace(repo=str(self.repo), declared=None, dry_run=False, yes=False, base=None, plan=None)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), mock.patch("sys.stdin", io.StringIO("")):
            code = cli.cmd_tidy(a, lambda home, b: self.m.context())
        self.assertEqual(code, 2)
        self.assertIn("needs a terminal", err.getvalue())


class EditorTests(unittest.TestCase):
    def test_the_editor_gets_a_private_file_and_tidy_removes_it(self):
        seen = {}

        def fake_run(argv, check=False):
            path = argv[-1]
            seen["mode"] = os.stat(path).st_mode & 0o777
            seen["argv"] = argv
            with open(path, "a", encoding="utf-8") as f:
                f.write("edited\n")
            return mock.Mock(returncode=0)

        with mock.patch("subprocess.run", fake_run):
            text = cli.edit_text("plan\n", {"EDITOR": "code --wait"})
        self.assertEqual(text, "plan\nedited\n")
        self.assertEqual(seen["mode"], 0o600)
        self.assertEqual(seen["argv"][:4], ["/bin/sh", "-c", 'code --wait "$@"', "code --wait"])
        self.assertFalse(os.path.exists(seen["argv"][-1]))

    def test_an_editor_that_fails(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)):
            self.assertIsNone(cli.edit_text("plan\n", {}))


if __name__ == "__main__":
    unittest.main()
