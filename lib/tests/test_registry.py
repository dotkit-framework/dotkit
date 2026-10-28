"""Invariants of the registry: what every migrator must hold to, checked over all of them.

A new migrator is covered without new code here (ARCHITECTURE.md, section 9).
"""

from __future__ import annotations

import ast
import builtins
import importlib
import io
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from dotkit.migrate import docs, rcparse, registry, report, secrets, writer
from dotkit.migrate.model import AdoptOptions
from tests import fake
from tests.test_golden import ADOPT_DECLARED, ADOPT_REPO, PLANS, check_syntax, kitchen_sink, kitchen_sink_choices
from tests.test_session import used_mac

PACKAGE = Path(registry.__file__).resolve().parent
MIGRATORS_DIR = PACKAGE / "migrators"

# Section 6: a migrator never writes a file and never runs a command but through ctx.runner.
FORBIDDEN_MODULES = {"proc", "subprocess", "writer", "ui", "curses"}
FORBIDDEN_CALLS = {
    ("os", "replace"), ("os", "rename"), ("os", "remove"), ("os", "unlink"),
    ("os", "system"), ("os", "popen"), ("os", "symlink"), ("os", "makedirs"),
    ("shutil", "copytree"), ("shutil", "move"), ("shutil", "rmtree"),
}
# Methods of Path that write. Not replace: str.replace has the same name.
FORBIDDEN_METHODS = {"write_text", "write_bytes", "unlink", "rename", "touch", "mkdir", "rmdir", "symlink_to",
                     "hardlink_to", "chmod"}


def _module_name(node: ast.AST) -> str:
    """The dotted name of an expression such as os.path, or ""."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _module_name(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


def _top(name: str) -> str:
    """The module of the package that a dotted import name is in: dotkit.migrate.ui.screens -> ui."""
    parts = name.split(".")
    if parts[:2] == ["dotkit", "migrate"]:
        parts = parts[2:]
    return parts[0] if parts else ""


def _writing_mode(call: ast.Call, index: int) -> bool:
    """open(...) or Path.open(...) with a mode that writes, or with a mode that is not a literal."""
    mode = call.args[index] if len(call.args) > index else None
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    if mode is None:
        return False
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return True
    return any(c in mode.value for c in "wax+")


def write_findings(source: str, filename: str) -> list[str]:
    """What in a migrator's source writes a file, runs a command, or imports a forbidden module."""
    out = []
    for node in ast.walk(ast.parse(source, filename)):
        where = f"{filename}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Import):
            for a in node.names:
                if _top(a.name) in FORBIDDEN_MODULES:
                    out.append(f"{where}: import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            # from .. import writer, from ..ui.screens import x, from subprocess import run.
            # A level-1 import (from . import x) is a migrator module itself.
            module = node.module or ""
            if node.level != 1 and module and _top(module) in FORBIDDEN_MODULES:
                out.append(f"{where}: from {'.' * node.level}{module} import ...")
            elif (node.level >= 2 and not module) or module == "dotkit.migrate":
                out += [f"{where}: from .. import {a.name}" for a in node.names if a.name in FORBIDDEN_MODULES]
            if module in ("os", "shutil"):
                out += [f"{where}: from {module} import {a.name}" for a in node.names
                        if (module, a.name) in FORBIDDEN_CALLS or a.name.startswith("copy")]
        elif isinstance(node, ast.Attribute):
            owner = _module_name(node.value)
            if (owner, node.attr) in FORBIDDEN_CALLS or (owner == "shutil" and node.attr.startswith("copy")):
                out.append(f"{where}: {owner}.{node.attr}")
            elif owner == "subprocess" or node.attr in FORBIDDEN_METHODS:
                out.append(f"{where}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id == "subprocess":
            out.append(f"{where}: subprocess")
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "open" and _writing_mode(node, 1):
                out.append(f"{where}: open(...) that writes")
            elif isinstance(f, ast.Attribute) and f.attr == "open" and _writing_mode(node, 0):
                out.append(f"{where}: .open(...) that writes")
    return out


def _sessions(test: unittest.TestCase):
    """Sessions of the fake Macs with the most pages, after the scan. test removes their homes."""
    for make in (kitchen_sink, lambda h: (used_mac(h), {})):
        h = fake.Home()
        test.addCleanup(h.cleanup)
        runner, env = make(h)
        yield h, fake.session(h, runner, env, check=check_syntax)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch("getpass.getuser", return_value="jane"),
                      mock.patch("socket.gethostname", return_value="test-mac.local")):
            patch.start()
            self.addCleanup(patch.stop)

    def test_keys_are_unique(self):
        keys = [m.key for m in registry.MIGRATORS]
        self.assertTrue(all(keys), keys)
        self.assertEqual(len(keys), len(set(keys)), keys)
        self.assertNotIn("core", keys)  # the Txn owner of the Session's own entries

    def test_page_keys_and_item_id_prefixes_are_unique(self):
        for h, s in _sessions(self):
            pages = s.pages()
            self.assertGreaterEqual(len(pages), 10)
            keys = [p.key for p in pages]
            self.assertEqual(len(keys), len(set(keys)), keys)
            owner = {}
            for p in pages:
                prefixes = set()
                for i in p.items:
                    prefix, sep, rest = i.id.partition(":")
                    self.assertTrue(prefix and sep and rest, f"{p.key}: item id {i.id!r} has no prefix")
                    prefixes.add(prefix)
                ids = [i.id for i in p.items]
                self.assertEqual(len(ids), len(set(ids)), f"{p.key}: an item id twice")
                self.assertEqual(len(prefixes), 1, f"{p.key}: item ids with more than one prefix: {prefixes}")
                prefix = prefixes.pop()
                self.assertNotIn(prefix, owner, f"pages {owner.get(prefix)} and {p.key} share the prefix {prefix}")
                owner[prefix] = p.key

    def test_sections_are_in_the_report_sections(self):
        self.assertEqual(len(report.SECTIONS), len(set(report.SECTIONS)))
        for m in registry.MIGRATORS:
            with self.subTest(migrator=m.key):
                self.assertTrue(m.sections, "a migrator names the report sections it writes")
                self.assertLessEqual(set(m.sections), set(report.SECTIONS))

    def test_every_migrator_has_a_title_and_manual_steps(self):
        """The text of a failure (section 7.5): a person can do by hand what the migrator did not."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        ctx = fake.context_of(h, fake.FakeRunner())
        keys = {m.key for m in registry.MIGRATORS}
        for m in (registry.IDENTITY, *registry.MIGRATORS):
            with self.subTest(migrator=m.key):
                self.assertTrue(m.title)
                text = m.manual_steps(ctx)
                self.assertTrue(text, "manual_steps is empty")
                self.assertTrue(text.endswith("."), text)
                self.assertIsNone(secrets.line_secret(text))
                self.assertLessEqual(set(getattr(m, "needs", ())), keys)

    def test_a_migrator_writes_only_its_sections(self):
        """Every section a migrator writes in the fake Macs' plans is one of its `sections`."""
        sections = {m.key: set(m.sections) for m in registry.MIGRATORS}
        written = []
        todo = docs.ReportDoc.todo

        def record(doc, txn, title, intro, items):
            written.append((txn.owner, title))
            return todo(doc, txn, title, intro, items)

        with mock.patch.object(docs.ReportDoc, "todo", record):
            for h, s in _sessions(self):
                c = s.defaults(str(h.home / ".dotfiles"))
                s.build(c)
                c.picked = {p.key: {i.id for i in p.items} for p in s.pages() if p.kind == "checklist"}
                s.build(c)
                c.picked = {p.key: set() for p in s.pages()}
                s.build(c)
        self.assertTrue(written)
        for owner, title in sorted(set(written)):
            if owner in sections:
                head = "Shell lines" if title.startswith("Shell lines: ") else title
                self.assertIn(head, sections[owner], f"{owner} writes {title!r}")

    def test_an_empty_home(self):
        """No warnings, no pages, and nothing in the plan but the files of the Session itself."""
        h = fake.Home()
        self.addCleanup(h.cleanup)
        s = fake.session(h, fake.FakeRunner(), check=check_syntax)
        facts = s.scan()
        self.assertEqual(facts.warnings, [])
        for m in registry.MIGRATORS:
            self.assertIsNotNone(facts.get(m.key), m.key)
        self.assertEqual(s.pages(), [])
        plan = s.build(s.defaults(str(h.home / ".dotfiles")))
        self.assertEqual((s.failures, plan.failures), ([], []))
        self.assertEqual(sorted(plan.files), ["MIGRATION.md", "nix/user.nix"])
        self.assertEqual((plan.copies, plan.secrets, plan.links, plan.move_links, plan.blockers), ({}, "", {}, [], []))
        self.assertIn("## Carried over\n\nNothing.\n\n## To do\n\nNothing.\n", plan.report)


class NoWriteTests(unittest.TestCase):
    def test_migrators_do_not_write_or_run_commands(self):
        files = sorted(MIGRATORS_DIR.glob("*.py"))
        self.assertGreater(len(files), 10)
        for f in files:
            with self.subTest(f=f.name):
                self.assertEqual(write_findings(f.read_text(encoding="utf-8"), f.name), [])

    def test_the_scan_and_the_plan_of_tidy_do_not_write_or_run_commands(self):
        """TIDY.md, section 7: only tidy/actions.py and tidy/cli.py change the Mac."""
        for name in ("apps.py", "clis.py", "pkgs.py", "plan.py"):
            f = PACKAGE / "tidy" / name
            with self.subTest(f=name):
                self.assertEqual(write_findings(f.read_text(encoding="utf-8"), name), [])

    def test_the_check_finds_writes(self):
        """The check itself: each of these fails it."""
        for src in ("open(p, 'w')", "open(p, mode='a')", "open(p, m)", "p.open('w')", "p.write_text('x')",
                    "p.write_bytes(b'')", "shutil.copy2(a, b)", "shutil.copytree(a, b)", "os.replace(a, b)",
                    "os.rename(a, b)", "os.remove(a)", "os.unlink(a)", "subprocess.run(['x'])",
                    "import subprocess", "from subprocess import run", "import curses", "from .. import proc",
                    "from ..proc import Runner", "from .. import writer", "from ..writer import render",
                    "from ..ui import screens", "from ..ui.screens import x", "import dotkit.migrate.writer",
                    "from os import remove", "from shutil import copyfile", "from dotkit.migrate import writer",
                    "from dotkit.migrate import ui", "p.unlink()", "p.rename(q)", "p.touch()", "p.mkdir()",
                    "p.rmdir()", "p.symlink_to(q)", "p.hardlink_to(q)", "p.chmod(0o644)", "os.system('x')",
                    "os.popen('x')", "os.symlink(a, b)", "os.makedirs(a)", "from os import system"):
            with self.subTest(src=src):
                self.assertNotEqual(write_findings(src, "x.py"), [])
        for src in ("open(p)", "open(p, 'rb')", "p.read_text()", "os.readlink(p)", "from . import tools",
                    "from .. import secrets", "from ..context import read_text", "s.replace('a', 'b')",
                    "from dotkit.migrate import secrets"):
            with self.subTest(src=src):
                self.assertEqual(write_findings(src, "x.py"), [])


# The .todo(...) calls whose intro the static check cannot read, and why.
NOT_STATIC = {
    # ReportView.todo hands its arguments on to ReportDoc.todo: the intro is whatever its caller gave.
    "dotkit/migrate/docs.py:1032",
    # rcparse.hint(...): test_rcparse_hints_have_no_secret checks every hint with both file names.
    "dotkit/migrate/migrators/shell.py:324",
}


def static_intros() -> tuple[list[tuple[str, str]], list[str]]:
    """The intros of the report sections that are in the code: (where, text), and the calls it cannot read.

    The intro of each .todo(title, intro, items) call in the package: a string
    literal, a module constant (NOT_CARRIED, config_files.KEPT_INTRO), or the
    literal parts of an f-string. Any other intro (a variable, an expression,
    *args) is unread.
    """
    out, unread = [], []
    files = sorted(p for p in PACKAGE.rglob("*.py") if "_vendor" not in p.parts)
    for f in files:
        rel = f.relative_to(PACKAGE.parents[1])
        module = importlib.import_module(".".join(rel.with_suffix("").parts).replace(".__init__", ""))
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"), str(f))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "todo"):
                continue
            where = f"{rel}:{node.lineno}"
            args = node.args
            # ReportDoc.todo(txn, title, intro, items) has the intro third.
            index = 2 if len(args) == 4 else 1
            if node.keywords or len(args) not in (3, 4) or any(isinstance(a, ast.Starred) for a in args):
                unread.append(where)
                continue
            intro = args[index]
            if isinstance(intro, ast.Constant) and isinstance(intro.value, str):
                out.append((where, intro.value))
                continue
            if isinstance(intro, ast.JoinedStr):
                out += [(where, v.value) for v in intro.values
                        if isinstance(v, ast.Constant) and isinstance(v.value, str)]
                continue
            if isinstance(intro, (ast.Name, ast.Attribute)) and _module_name(intro):
                value = module
                for part in _module_name(intro).split("."):
                    value = getattr(value, part, None)
                if isinstance(value, str):
                    out.append((where, value))
                    continue
            unread.append(where)
    return out, unread


class StaticTextTests(unittest.TestCase):
    """The masking of the report must never hide dotkit's own text (section 6).

    An intro never holds user data; that belongs in an item.
    """

    def test_built_intros_have_no_secret(self):
        """Every block intro of the fixture plans as built, before the report is masked."""
        intros = []
        todo = docs.ReportDoc.todo

        def record(doc, txn, title, intro, items):
            intros.append((txn.owner, title, intro))
            return todo(doc, txn, title, intro, items)

        with mock.patch.object(docs.ReportDoc, "todo", record):
            for name, (make, choose) in PLANS.items():
                h = fake.Home()
                self.addCleanup(h.cleanup)
                runner, env = make(h)
                s = fake.session(h, runner, env, check=check_syntax)
                repo = str(h.home / ".dotfiles")
                s.build(choose(s, repo) if choose else s.defaults(repo))
        self.assertGreater(len({i for _, _, i in intros if i}), 15)
        for owner, title, intro in sorted(set(intros)):
            for line in intro.splitlines():
                with self.subTest(owner=owner, title=title, line=line):
                    self.assertIsNone(secrets.line_secret(line))

    def test_section_intros_have_no_secret(self):
        intros, unread = static_intros()
        self.assertEqual(sorted(set(unread) - NOT_STATIC), [], "intros that the check cannot read")
        self.assertEqual(sorted(NOT_STATIC - set(unread)), [], "NOT_STATIC lists a call that is gone")
        texts = [t for _, t in intros]
        for where, text in intros:
            for line in text.splitlines():
                with self.subTest(where=where, line=line):
                    self.assertIsNone(secrets.line_secret(line))
        # The search finds literals, module constants, and the parts of f-strings.
        self.assertGreater(len(texts), 15)
        for must in ("These lines have a secret", "dotkit does not manage them", "To get these tools from mise",
                     "Your login shell is "):
            self.assertTrue(any(t.startswith(must) or must in t for t in texts), must)

    def test_rcparse_hints_have_no_secret(self):
        for cat, _ in rcparse.CATEGORIES:
            for zshrc, zshenv in (("config/zsh/zshrc", "config/zsh/zshenv"),
                                  ("config/bash/bash_profile", "config/bash/bash_profile")):
                text = rcparse.hint(cat, zshrc, zshenv)
                self.assertTrue(text, cat)
                for line in text.splitlines():
                    with self.subTest(category=cat, line=line):
                        self.assertIsNone(secrets.line_secret(line))


# --- The secret property test (section 6)

TOKEN = fake.TOKENS["@GH@"]  # secrets.py knows it by its value
TOKEN_KEY = "dotkit_test_token"
# Files under the home that a migrator reads and that are not UTF-8 text (or have a NUL): they get no token.
NOT_TEXT = {"go/bin/hey"}
# What writer.mask_lines and the plan-time guard put in place of a line with a secret.
MASKED_LINE = "(a line with a secret was here)"
MASKED_COMMENT = "# (a line with a secret was here, see MIGRATION.md)"
# Files that the migrators are known to read: the record must have them, or the patch saw nothing.
KNOWN_READS = {
    ".zshrc", ".npmrc", ".npm-global/lib/node_modules/eslint/package.json",
    ".local/pipx/venvs/black/pipx_metadata.json", ".local/share/uv/tools/httpie/uv-receipt.toml",
}


class Recorder:
    """io.open, builtins.open, and os.open, patched: each file opened under the home is recorded."""

    def __init__(self, home: Path):
        self.homes = {str(home), os.path.realpath(home)}
        self.opened = set()

    def _note(self, file) -> None:
        if isinstance(file, int):
            return
        try:
            path = os.path.abspath(os.fsdecode(file))
        except TypeError:
            return
        for home in self.homes:
            if path.startswith(home + "/"):
                self.opened.add(path[len(home) + 1:])

    def patches(self):
        io_open, os_open = io.open, os.open

        def opener(file, *args, **kwargs):
            self._note(file)
            return io_open(file, *args, **kwargs)

        def os_opener(path, *args, **kwargs):
            self._note(path)
            return os_open(path, *args, **kwargs)

        return (mock.patch.object(io, "open", opener), mock.patch.object(builtins, "open", opener),
                mock.patch.object(os, "open", os_opener))


def source_files(src: Path) -> list[Path]:
    """The files of a copy source: the file, or the files in the directory (a link in it is copied as a link)."""
    if not src.is_dir():
        return [src]
    return sorted(q for q in src.rglob("*") if q.is_file() and not q.is_symlink())


def is_text(path: Path) -> bool:
    """UTF-8 without a NUL byte: secrets.scan_file counts a NUL as a binary file."""
    data = path.read_bytes()
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\0" not in data


def inject(path: Path) -> None:
    """A token in a file, in a way that keeps its format: a JSON object gets one more key, others a comment."""
    text = path.read_bytes().decode("utf-8")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        data[TOKEN_KEY] = TOKEN
        path.write_text(json.dumps(data), encoding="utf-8")
        return
    path.write_text(text + ("" if not text or text.endswith("\n") else "\n") + f"# {TOKEN}\n", encoding="utf-8")


class SecretPropertyTests(unittest.TestCase):
    """A token in every file under the home that a migrator reads reaches no tracked file and no output."""

    def setUp(self):
        for patch in (mock.patch("getpass.getuser", return_value="jane"),
                      mock.patch("socket.gethostname", return_value="test-mac.local")):
            patch.start()
            self.addCleanup(patch.stop)

    def run_all(self, h: fake.Home, runner, env):
        """Scan, plan with every item, and adopt with --all: the plan, the adopt output, the repository files."""
        repo = h.home / ".dotfiles"
        shutil.rmtree(repo, ignore_errors=True)
        s = fake.session(h, runner, env, check=check_syntax)
        facts = s.scan()
        plan = s.build(kitchen_sink_choices(s, str(repo)))
        for rel, text in ADOPT_REPO.items():
            h.write(f".dotfiles/{rel}", text)
        result = s.adopt(repo, ADOPT_DECLARED, AdoptOptions(all_tools=True))
        described = result.output
        writer.apply_edits(result, repo)
        edited = {str(p.relative_to(repo)): p.read_text(encoding="utf-8")
                  for p in sorted(repo.rglob("*")) if p.is_file()}
        return list(facts.warnings), plan, described, edited

    def test_no_token_from_any_file_a_migrator_reads(self):
        h = fake.Home()
        self.addCleanup(h.cleanup)
        runner, env = kitchen_sink(h)
        rec = Recorder(h.home)
        patches = rec.patches()
        for patch in patches:
            patch.start()
        try:
            warnings, plan, described, edited = self.run_all(h, runner, env)
        finally:
            for patch in reversed(patches):
                patch.stop()
        self.assert_not_masked(plan, described, edited)
        # The writer reads the copy sources only when it writes, and a guard that
        # stopped checking them would not open them either: they count as read.
        for src in plan.copies.values():
            for q in source_files(Path(src)):
                rec._note(q)
        # The adopt repository is the output, not the Mac.
        read = sorted(r for r in rec.opened if not r.startswith(".dotfiles/"))
        self.assertLessEqual(KNOWN_READS, set(read), "the record misses files that the migrators read")

        done = set()
        for rel in read:
            p = h.home / rel
            real = os.path.realpath(p)
            if real in done or not p.is_file():
                continue
            done.add(real)
            if not is_text(p):
                self.assertIn(rel, NOT_TEXT, f"~/{rel} is not UTF-8 text; list it in NOT_TEXT")
                continue
            self.assertNotIn(rel, NOT_TEXT, f"~/{rel} is text now; drop it from NOT_TEXT")
            inject(p)
        self.assertGreater(len(done), 30)

        guard = []
        todo = docs.ReportDoc.todo

        def record(doc, txn, title, intro, items):
            if txn.owner == "guard":
                guard.extend(items)
            return todo(doc, txn, title, intro, items)

        with mock.patch.object(docs.ReportDoc, "todo", record):
            warnings2, plan, described, edited = self.run_all(h, runner, env)
        self.assertEqual(warnings2, warnings, "a token broke a parser")
        # The migrators route every secret themselves; the guard of the writer is the fallback (section 6).
        self.assertEqual(guard, [], "the guard caught what a migrator passed on")
        for rel, text in plan.files.items():
            self.assertNotIn(TOKEN, text, rel)
        for rel, src in plan.copies.items():
            for q in source_files(Path(src)):
                self.assertEqual(secrets.scan_file(q), [], f"{rel}: {q}")
        self.assertNotIn(TOKEN, plan.report)
        self.assertNotIn(TOKEN, "\n".join(plan.summary))
        self.assertNotIn(TOKEN, "\n".join(described))
        for rel, text in edited.items():
            self.assertNotIn(TOKEN, text, f"adopt: {rel}")
        self.assert_not_masked(plan, described, edited)

    def assert_not_masked(self, plan, described, edited) -> None:
        """No line that the masking replaced: it would hide a token that a migrator let through."""
        for where, text in (("the report", plan.report), ("the summary", "\n".join(plan.summary)),
                            ("adopt", "\n".join(described)), *((f"adopt: {r}", t) for r, t in edited.items())):
            self.assertNotIn(MASKED_LINE, text, f"a masked line in {where}")
        for rel, text in plan.files.items():
            self.assertNotIn(MASKED_COMMENT, text, f"a masked line in {rel}")


if __name__ == "__main__":
    unittest.main()
