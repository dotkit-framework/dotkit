"""dotkit tidy for command-line tools: the scan, the default verbs, the check, and the apply (TIDY.md, section 8.1)."""

import argparse
import contextlib
import io
import os
import unittest
from unittest import mock

from dotkit_migrate.context import tilde
from dotkit_migrate.tidy import apps, cli, plan
from dotkit_migrate.tidy.cli import declared_of
from tests import fake
from tests.tidy.mac import TidyMac
from tests.tidy.test_cli import Answer, FakeActions

NVM = ".nvm/versions/node/v20.11.0"


def executable(p, text="#!/bin/sh\n"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    p.chmod(0o755)
    return p


class ToolMac(TidyMac):
    """A Mac with old copies of tools: Homebrew, nvm, pipx, uv, cargo, Go, files, and version managers."""

    def __init__(self, test):
        super().__init__(test)
        h = self.h
        self.repo = h.home / ".dotfiles"
        h.write(".dotfiles/nix/brews.txt", "wget\n")
        h.write(".dotfiles/config/mise.toml", '[tools]\n"npm:typescript" = "latest"\n"pipx:ruff" = "latest"\n'
                                              '"pipx:black" = "latest"\n')
        self.commands["brew leaves --installed-on-request"] = "jq\nwget\nhtop\n"
        for name in ("jq", "wget", "htop"):
            (self.h.prefix / "Cellar" / name / "1.0").mkdir(parents=True)
        # nvm with npm, typescript (in mise), prettier (not in mise), npm itself, and a linked package.
        executable(h.home / NVM / "bin/npm")
        for name, bins in (("typescript", {"tsc": "bin/tsc"}), ("prettier", "bin/prettier"), ("npm", "bin/npm")):
            h.json(f"{NVM}/lib/node_modules/{name}/package.json", {"name": name, "bin": bins})
        os.symlink(h.home / "src/mine", h.home / NVM / "lib/node_modules/mine")
        # ~/.npm-global without an npm, with typescript.
        h.json(".npm-global/lib/node_modules/typescript/package.json", {"name": "typescript", "bin": {"tsc": "x"}})
        self.paths.update({"pipx": "/opt/homebrew/bin/pipx", "uv": "/opt/homebrew/bin/uv"})
        h.json(".local/pipx/venvs/ruff/pipx_metadata.json", {"main_package": {"package": "ruff", "apps": ["ruff"]}})
        h.write(".local/share/uv/tools/black/uv-receipt.toml",
                '[tool]\nrequirements = [{ name = "black" }]\nentrypoints = [{ name = "black" }]\n')
        # cargo: bat (dotkit has it from Nix), rustup with its links, and cargo-deny.
        h.json(".cargo/.crates2.json", {"installs": {"bat 0.24.0 (registry+https://x)": {"bins": ["bat"]},
                                                     "cargo-deny 0.16.0 (registry+https://x)": {"bins": ["cargo-deny"]}}})
        executable(h.home / ".cargo/bin/rustup")
        for name in ("cargo", "rustc"):
            os.symlink("rustup", h.home / ".cargo/bin" / name)
        executable(h.home / ".cargo/bin/bat")
        executable(h.home / ".cargo/bin/cargo-deny")
        executable(h.home / "go/bin/hey")
        executable(h.home / "go/bin/jq")
        # Files: mise and uv from their installers, claude as a link, and links that other rows or Nix own.
        executable(h.home / ".local/bin/mise")
        executable(h.home / ".local/bin/uv")
        executable(h.home / ".local/share/claude/versions/1.0")
        os.symlink(h.home / ".local/share/claude/versions/1.0", h.home / ".local/bin/claude")
        os.symlink("/nix/store/abc-home-manager-files/.local/bin/x", h.home / ".local/bin/x")
        os.symlink("/Applications/CuaDriver.app/Contents/MacOS/cua-driver", h.home / ".local/bin/cua-driver")
        os.symlink(h.home / ".local/pipx/venvs/ruff/bin/ruff", h.home / ".local/bin/ruff")
        h.write(".local/bin/notes.txt", "not executable")
        executable(self.usr_local / "tailscale")
        os.chmod(self.usr_local, 0o555)
        h.mkdir(".oh-my-zsh")

    def tool(self, t, rel):
        path = rel if rel.startswith("/") else str(self.h.home / rel)
        return next(x for x in t.tools if x.path == path)

    def lines(self):
        ctx = self.context()
        t = apps.scan(ctx)
        lines, _ = plan.defaults(t, declared_of(ctx, self.repo, {}))
        return t, {tilde(x.path, str(self.h.home)): (x.verb, x.thing, x.note) for x in lines}


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.m = ToolMac(self)
        self.t = self.m.scan()

    def test_what_the_scan_finds(self):
        home = str(self.m.h.home)
        got = sorted((x.kind, tilde(x.path, home)) for x in self.t.tools)
        self.assertEqual(got, sorted([
            ("brew", str(self.m.h.prefix / "Cellar/jq")), ("brew", str(self.m.h.prefix / "Cellar/wget")),
            ("brew", str(self.m.h.prefix / "Cellar/htop")),
            ("npm", f"~/{NVM}/lib/node_modules/typescript"), ("npm", f"~/{NVM}/lib/node_modules/prettier"),
            ("npm", "~/.npm-global/lib/node_modules/typescript"),
            ("pipx", "~/.local/pipx/venvs/ruff"), ("uv", "~/.local/share/uv/tools/black"),
            ("cargo", "~/.cargo/bin/bat"), ("cargo", "~/.cargo/bin/cargo-deny"),
            ("go", "~/go/bin/hey"), ("go", "~/go/bin/jq"),
            ("file", "~/.local/bin/claude"), ("file", "~/.local/bin/mise"), ("file", "~/.local/bin/uv"),
            ("file", "~/.cargo/bin/rustup"), ("file", str(self.m.usr_local / "tailscale")),
            ("manager", "~/.nvm"), ("manager", "~/.oh-my-zsh"),
        ]))

    def test_uninstall_commands(self):
        m, t = self.m, self.t
        nvm = m.tool(t, f"{NVM}/lib/node_modules/typescript")
        self.assertEqual(nvm.uninstall, [str(m.h.home / NVM / "bin/npm"), "uninstall", "-g", "typescript"])
        self.assertEqual((nvm.path_first, nvm.where, nvm.commands), (str(m.h.home / NVM / "bin"), "nvm v20.11.0",
                                                                     ["tsc"]))
        self.assertEqual(m.tool(t, ".npm-global/lib/node_modules/typescript").uninstall, [])
        self.assertEqual(m.tool(t, ".local/pipx/venvs/ruff").uninstall, ["/opt/homebrew/bin/pipx", "uninstall", "ruff"])
        self.assertEqual(m.tool(t, ".local/share/uv/tools/black").uninstall,
                         ["/opt/homebrew/bin/uv", "tool", "uninstall", "black"])
        self.assertEqual(m.tool(t, str(m.h.prefix / "Cellar/jq")).uninstall, [fake.BREW, "uninstall", "jq"])
        # cargo is a link to rustup, so it counts as a command of $CARGO_HOME/bin.
        self.assertEqual(m.tool(t, ".cargo/bin/bat").uninstall, [str(m.h.home / ".cargo/bin/cargo"), "uninstall",
                                                                 "bat"])

    def test_files(self):
        m, t = self.m, self.t
        self.assertEqual(m.tool(t, ".local/bin/claude").target, str(m.h.home / ".local/share/claude/versions/1.0"))
        tailscale = m.tool(t, str(m.usr_local / "tailscale"))
        self.assertFalse(tailscale.writable)


class DefaultTests(unittest.TestCase):
    def setUp(self):
        self.m = ToolMac(self)
        self.t, self.got = self.m.lines()

    def test_copies_of_tools_that_dotkit_has(self):
        got, prefix = self.got, str(self.m.h.prefix)
        self.assertEqual(got[f"{prefix}/Cellar/jq"], ("uninstall", "brew:jq", "dotkit has jq (from Nix)"))
        self.assertEqual(got[f"~/{NVM}/lib/node_modules/typescript"],
                         ("uninstall", "npm:typescript", "nvm v20.11.0; dotkit has npm:typescript (from mise)"))
        self.assertEqual(got["~/.npm-global/lib/node_modules/typescript"],
                         ("keep", "", "~/.npm-global; dotkit has npm:typescript (from mise); no npm next to it to "
                                      "uninstall it"))
        self.assertEqual(got["~/.local/pipx/venvs/ruff"], ("uninstall", "pipx:ruff", "dotkit has pipx:ruff (from mise)"))
        self.assertEqual(got["~/.local/share/uv/tools/black"],
                         ("uninstall", "uv:black", "dotkit has pipx:black (from mise)"))
        self.assertEqual(got["~/.cargo/bin/bat"], ("uninstall", "cargo:bat", "dotkit has bat (from Nix)"))
        self.assertEqual(got["~/go/bin/jq"], ("trash", "", "from go install; dotkit has jq (from Nix)"))
        self.assertEqual(got["~/.local/bin/mise"], ("trash", "", "a file from an installer; dotkit has mise (from Nix)"))
        self.assertEqual(got["~/.local/bin/uv"], ("trash", "", "a file from an installer; dotkit has uv (from mise)"))

    def test_what_dotkit_migrate_adds_and_what_the_lists_have(self):
        got, prefix = self.got, str(self.m.h.prefix)
        for path in (f"{prefix}/Cellar/wget", f"{prefix}/Cellar/htop", f"~/{NVM}/lib/node_modules/prettier",
                     "~/.cargo/bin/cargo-deny", "~/go/bin/hey"):
            self.assertNotIn(path, got)

    def test_files_and_managers_that_stay(self):
        got = self.got
        self.assertEqual(got["~/.local/bin/claude"], ("keep", "", "a link to ~/.local/share/claude/versions/1.0"))
        self.assertEqual(got["~/.cargo/bin/rustup"], ("keep", "", "a file from an installer"))
        self.assertEqual(got[str(self.m.usr_local / "tailscale")][:2], ("keep", ""))
        self.assertIn("a file from an installer; owned by", got[str(self.m.usr_local / "tailscale")][2])
        self.assertEqual(got["~/.nvm"], ("keep", "", "nvm, a version manager; dotkit does not load it. "
                                                    + plan.CHECK_REFERENCES))
        self.assertEqual(got["~/.oh-my-zsh"][:2], ("keep", ""))

    def test_the_tools_come_after_the_apps(self):
        m = self.m
        m.app("Old.app", "com.example.old")
        t, _ = m.lines()
        lines, listed = plan.defaults(t, declared_of(m.context(), m.repo, {}))
        text = plan.render(lines, t, listed)
        self.assertIn("# dotkit tidy: 1 app and 14 command-line tools.", text)
        self.assertLess(text.index("Old.app"), text.index("# Command-line tools"))
        self.assertLess(text.index("# Command-line tools"), text.index("uninstall  brew:jq"))
        parsed, errors = plan.parse(text, t)
        self.assertEqual(errors, [])
        self.assertEqual(plan.check(parsed, t), ([], []))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.m = ToolMac(self)
        self.m.app("Old.app", "com.example.old")
        self.t = self.m.scan()

    def errors(self, text):
        parsed, errors = plan.parse(text, self.t)
        return errors + plan.check(parsed, self.t)[0]

    def test_errors(self):
        h, apps_dir = self.m.h.home, self.m.h.apps
        text = "\n".join([
            f"adopt cask:jq {self.m.h.prefix}/Cellar/jq",
            f"uninstall ~/.local/bin/mise",
            f"uninstall npm:prettier ~/{NVM}/lib/node_modules/typescript",
            "uninstall npm:typescript ~/.npm-global/lib/node_modules/typescript",
            "trash ~/.local/pipx/venvs/ruff",
            f"trash {self.m.usr_local}/tailscale",
            f"uninstall {apps_dir}/Old.app",
        ])
        self.assertEqual(self.errors(text), [
            "line 1: adopt is for apps. Use uninstall, trash, or keep.",
            "line 2: ~/.local/bin/mise is not a package. Use trash or keep.",
            f"line 3: ~/{NVM}/lib/node_modules/typescript is npm:typescript: uninstall npm:typescript <path>.",
            "line 4: the scan found no command that uninstalls npm:typescript.",
            "line 5: pipx:ruff is a package. Use uninstall, so that its package manager forgets it too.",
            f"line 6: {self.m.usr_local}/tailscale is owned by {self.m.tool(self.t, str(self.m.usr_local / 'tailscale')).owner}, "
            "so tidy cannot move it to the Trash.",
            "line 7: uninstall is for command-line tools. Use trash or keep for an app.",
        ])
        del h

    def test_commands(self):
        x = plan.Line("uninstall", str(self.m.h.home / ".local/pipx/venvs/ruff"), "pipx:ruff")
        y = plan.Line("uninstall", str(self.m.h.home / NVM / "lib/node_modules/typescript"), "npm:typescript")
        z = plan.Line("trash", str(self.m.h.home / ".local/bin/mise"))
        self.assertEqual(plan.commands([x, y, z], self.t), [
            "/opt/homebrew/bin/pipx uninstall ruff",
            f"~/{NVM}/bin/npm uninstall -g typescript",
            "trash ~/.local/bin/mise",
        ])


class ToolActions(FakeActions):
    def __init__(self, brew, fail_uninstall=(), **kw):
        super().__init__(brew, **kw)
        self.fail_uninstall = set(fail_uninstall)

    def uninstall(self, argv, path_first=""):
        name = argv[-1]
        self._call(f"uninstall {os.path.basename(argv[0])} {name}" + (" (PATH)" if path_first else ""))
        return name not in self.fail_uninstall


class ApplyTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch("socket.gethostname", return_value="test-mac.local"),):
            patch.start()
            self.addCleanup(patch.stop)
        self.m = ToolMac(self)
        self.actions = None

    def run_tidy(self, text, **fake_kw):
        p = self.m.h.root / "plan.txt"
        p.write_text(text)
        a = argparse.Namespace(repo=str(self.m.repo), declared=None, dry_run=False, yes=False, base=None, plan=str(p))
        ctx = self.m.context()

        def make_actions(brew):
            self.actions = ToolActions(brew, **fake_kw)
            return self.actions

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch("sys.stdin", Answer("y\n")):
            code = cli.cmd_tidy(a, lambda home, b: ctx, make_actions)
        return code, out.getvalue(), err.getvalue()

    def test_uninstall_and_trash(self):
        before = {f: (self.m.repo / f).read_text() for f in ("nix/brews.txt", "config/mise.toml")}
        code, out, err = self.run_tidy("\n".join([
            f"uninstall npm:typescript ~/{NVM}/lib/node_modules/typescript",
            f"uninstall brew:jq {self.m.h.prefix}/Cellar/jq",
            "trash ~/.local/bin/mise",
            "trash ~/.nvm",
        ]))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.actions.calls, ["uninstall npm typescript (PATH)", "uninstall brew jq",
                                              "trash mise", "trash .nvm"])
        self.assertIn("Uninstalled:\n  npm:typescript (~/" + NVM + "/lib/node_modules/typescript)\n  brew:jq", out)
        self.assertIn("In the Trash:\n  ~/.local/bin/mise\n  ~/.nvm", out)
        self.assertNotIn("Then it adds the casks", out)
        self.assertEqual({f: (self.m.repo / f).read_text() for f in before}, before)
        self.assertFalse((self.m.repo / "nix/casks.txt").exists())

    def test_a_failed_uninstall_and_a_path_that_is_gone(self):
        def gone():
            os.unlink(self.m.h.home / ".local/bin/uv")

        p = self.m.h.root / "plan.txt"
        p.write_text("uninstall pipx:ruff ~/.local/pipx/venvs/ruff\ntrash ~/.local/bin/uv\n")
        a = argparse.Namespace(repo=str(self.m.repo), declared=None, dry_run=False, yes=False, base=None, plan=str(p))
        ctx = self.m.context()
        acts = []

        def make_actions(brew):
            acts.append(ToolActions(brew, fail_uninstall=["ruff"]))
            return acts[0]

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch("sys.stdin", Answer("y\n", gone)):
            code = cli.cmd_tidy(a, lambda home, b: ctx, make_actions)
        self.assertEqual(code, 1)
        self.assertEqual(acts[0].calls, ["uninstall pipx ruff"])
        self.assertIn("`/opt/homebrew/bin/pipx uninstall ruff` failed; its output is above.", out.getvalue())
        self.assertIn("~/.local/bin/uv: it is gone since the scan. Run dotkit tidy again.", out.getvalue())


class ParseTests(unittest.TestCase):
    def test_a_thing_needs_a_known_prefix(self):
        m = TidyMac(self)
        t = m.scan()
        parsed, errors = plan.parse("uninstall go:hey ~/go/bin/hey\nkeep note:x\n", t)
        self.assertEqual(errors, [])
        self.assertEqual([(x.thing, x.path) for x in parsed],
                         [("go:hey", str(m.h.home / "go/bin/hey")), ("", os.path.abspath("note:x"))])
        self.assertEqual([x.mas_id for x in parsed], [0, 0])


if __name__ == "__main__":
    unittest.main()
