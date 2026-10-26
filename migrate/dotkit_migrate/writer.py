"""The plan of the documents, the secret guards, and the only code that writes to a repository.

render() turns the committed documents into a Plan and runs the plan-time
guard over it: a copy with a secret is dropped, and a line with a secret in a
generated file is moved to local/ or masked. mask_lines() masks the report,
the summary, and adopt's output. write_new() creates the repository, and
before the rename it checks the staged tracked files once more: a secret
there aborts the write, and nothing is left behind. apply_edits() changes
the files of an existing repository for `dotkit migrate`: all of them or
none.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from . import rcparse, secrets
from .context import tilde
from .docs import Docs
from .model import AdoptResult, Plan

# --- The plan


def render(docs: Docs) -> Plan:
    """The plan of the committed documents, after the plan-time guard.

    In this order: the copy guard, which drops a copy with a secret through
    Docs.drop, so that its links never reach nix/home.nix; the documents
    render; the file guard over the rendered tracked files; the /local/ block
    in .gitignore.
    """
    _guard_copies(docs)
    plan = Plan(docs.repo, docs.keymap(), home=docs.home)
    plan.files, plan.local = docs.render()
    plan.copies = docs.copies.items()
    plan.links = docs.links()
    _guard_files(docs, plan)
    if plan.local:
        plan.files[".gitignore"] = (docs.template / ".gitignore").read_text(encoding="utf-8").rstrip("\n") + \
            "\n# Files with secrets, from the migration. See MIGRATION.md.\n/local/\n"
    return plan


# The plan-time guard: the last checks that no secret goes into a file that
# Git tracks. The planning rules keep secrets out already. These drop a copy
# with a secret, and a line with a secret in a generated file, and report it.


def _checked(path: Path, root: Path) -> bool:
    """Whether the guards check a file of a copied directory: not a link inside it, nothing of .DS_Store."""
    return ".DS_Store" not in path.relative_to(root).parts and not (path.is_symlink() and path != root)


def _guard_copies(docs: Docs) -> None:
    dropped = []
    for rel, src in docs.copies.items().items():
        paths = [src] if not src.is_dir() else [p for p in src.rglob("*") if not p.is_dir()]
        # A link inside a directory is copied as a link. Anything else that
        # cannot be checked counts as a secret.
        if any(secrets.has_secret(p) for p in paths if _checked(p, src)):
            dropped.append((rel, src))
    t = docs.begin("guard")
    for rel, src in dropped:
        t.report.todo("Secrets", "", [f"{tilde(src, docs.home)} has a secret, so it is not in the repository."])
    t.commit()
    for rel, _ in dropped:
        docs.drop(rel)


def _guard_files(docs: Docs, plan: Plan) -> None:
    t = docs.begin("guard")
    for rel, text in list(plan.files.items()):
        if not secrets.scan_text(text):
            continue
        shell = "bash" if rel == "config/bash/bash_profile" else "zsh" if rel in {
            "config/zsh/zshrc", "config/zsh/zprofile", "config/zsh/zshenv"} else None
        if shell:
            # Never cut a shell statement in half. This guard is a fallback;
            # the parser normally routes the original statement to local/.
            kept, local = [], []
            for st in rcparse._statements(text):
                if not secrets.scan_text(st.text):
                    kept.append(st.text)
                elif not st.text.lstrip().startswith("#"):
                    local.append(st.text)
            if local:
                name = f"local/{Path(rel).name}.{shell}"
                plan.local[name] = plan.local.get(name, "") + "\n".join(local) + "\n"
                source = tilde(plan.repo / name, docs.home).replace("~", "$HOME", 1)
                if not any(f'source "{source}"' in st for st in kept):
                    kept.append(f'if [[ -r "{source}" ]]; then\n  source "{source}"\nfi')
            plan.files[rel] = "\n".join(kept) + "\n"
        else:
            rows = text.splitlines()
            for start, end, logical in secrets.logical_lines(text):
                if secrets.line_secret(logical):
                    rows[start - 1:end] = ["# (a line with a secret was here, see MIGRATION.md)"] * (end - start + 1)
            plan.files[rel] = "\n".join(rows) + "\n"
        t.report.todo("Secrets", "", [f"{rel}: a statement with a secret is not in Git."])
    t.commit()


def mask_lines(lines: list[str]) -> list[str]:
    """Lines of text that is shown or written, with each line that has a secret replaced."""
    return [line if not secrets.line_secret(line) else
            "(a line with a secret was here)" + ("\n" if line.endswith("\n") else "") for line in lines]


# --- Writing


class SecretInRepository(Exception):
    """The write-time verification found a secret in a staged file that Git would track."""


def _copy_template(src: Path, dest: Path) -> None:
    """Copy the template without the permissions of the Nix store, which are read-only."""
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dest / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copyfile(Path(root, name), dest / rel / name)
            if os.access(Path(root, name), os.X_OK):
                os.chmod(dest / rel / name, 0o755)


def _secret_in_file(path: Path) -> str:
    """What secrets.has_secret finds in a file, as text for the message, or ""."""
    try:
        hits = secrets.scan_file(path)
    except secrets.Unscannable as e:
        return str(e)
    return f"line {hits[0].line}: {hits[0].what}" if hits else ""


def verify(tree: Path, plan: Plan) -> list[str]:
    """The staged files that Git would track and that have a secret, or cannot be checked.

    The checks are those of the plan-time guard: secrets.scan_text for the
    files of the plan, secrets.has_secret for the other files (the copies and
    the template), with links inside a copied directory and .DS_Store
    skipped. So only what changed after the guard ran is found. local/,
    .build/, and .git are left out, as Git does not track them. A directory
    that cannot be read is a find. It changes nothing.
    """
    found: list[str] = []

    def unreadable(e: OSError) -> None:
        found.append(f"{Path(e.filename).relative_to(tree)} (cannot be read: {e.strerror})")

    for root, dirs, files in os.walk(tree, onerror=unreadable, followlinks=False):
        here = Path(root)
        if here == tree:
            dirs[:] = [d for d in dirs if d not in ("local", ".build", ".git")]
        dirs[:] = [d for d in dirs if d != ".DS_Store"]
        for name in sorted(files):
            path = here / name
            rel = path.relative_to(tree).as_posix()
            if rel in plan.files:
                try:
                    hits = secrets.scan_text(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError) as e:
                    found.append(f"{rel} (cannot be read: {e})")
                    continue
                if hits:
                    found.append(f"{rel} (line {hits[0].line}: {hits[0].what})")
                continue
            if name == ".DS_Store" or stat.S_ISLNK(os.lstat(path).st_mode):
                continue
            what = _secret_in_file(path)
            if what:
                found.append(f"{rel} ({what})")
    return sorted(found)


def _remove(tmp: Path) -> None:
    """Remove a staged tree, also when a copy brought read-only directories. Say so when it stays."""

    def retry(func, path, exc) -> None:
        exc = exc if isinstance(exc, BaseException) else exc[1]
        if isinstance(exc, FileNotFoundError):
            return
        if not isinstance(exc, PermissionError):
            raise exc
        os.chmod(os.path.dirname(path), 0o700)
        if os.path.isdir(path) and not os.path.islink(path):
            # A directory that could not be read or emptied: again, now that it can.
            os.chmod(path, 0o700)
            _rmtree(path)
        else:
            func(path)

    def _rmtree(path) -> None:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry)
        else:
            shutil.rmtree(path, onerror=retry)

    try:
        _rmtree(tmp)
    except OSError:
        pass
    if os.path.lexists(tmp):
        print(f"dotkit: could not remove {tmp}. It may hold a secret; remove it by hand.", file=sys.stderr,
              flush=True)


def write_new(plan: Plan, template: Path) -> None:
    """Create the repository of the plan. It must not exist yet.

    A secret in a staged file that Git would track raises SecretInRepository,
    and then nothing is written.
    """
    repo = plan.repo
    if os.path.lexists(repo):
        raise FileExistsError(f"{repo} exists")
    # A new directory next to the repository, so that the rename is atomic and
    # nothing that exists is touched.
    tmp = Path(tempfile.mkdtemp(dir=repo.parent, prefix=repo.name + ".partial-"))
    os.chmod(tmp, 0o755)
    try:
        _copy_template(template, tmp)
        for rel, src in plan.copies.items():
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dest, symlinks=True, copy_function=shutil.copy2,
                                ignore=shutil.ignore_patterns(".DS_Store"))
            else:
                shutil.copyfile(src, dest)
        for rel, text in plan.files.items():
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        for rel, text in plan.local.items():
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(dest.parent, 0o700)
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
        if plan.move_links:
            # For scripts/setup, also when it runs again: .build/ is ignored by Git.
            (tmp / ".build").mkdir(exist_ok=True)
            (tmp / ".build/links-in-way").write_text("".join(t + "\n" for t in plan.move_links), encoding="utf-8")
        found = verify(tmp, plan)
        if found:
            raise SecretInRepository("the new repository would have a secret in a file that Git tracks, or "
                                     "a file that cannot be checked: " + ", ".join(found))
        subprocess.run(["git", "init", "--quiet", str(tmp)], check=True)
        if os.path.lexists(repo):
            raise FileExistsError(f"{repo} exists")
        tmp.rename(repo)
    except BaseException:
        _remove(tmp)
        raise


class AdoptStopped(Exception):
    """apply_edits() stopped before anything was staged: a file changed, or a link points outside."""


def verify_edits(staged: dict[str, Path], old: dict[str, str | None]) -> list[str]:
    """The staged files of `dotkit migrate` that have a secret in a line that the file did not have.

    The lines are compared as a multiset of their texts: a secret that was in
    the file before is not dotkit's, and `dotkit migrate` only must not add
    one. A staged file that cannot be read is a find. It changes nothing.
    """
    found = []
    for rel, path in staged.items():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            found.append(f"{rel} (cannot be read: {e})")
            continue
        before = Counter(line for _, _, line in secrets.logical_lines(old.get(rel) or ""))
        for start, _end, line in secrets.logical_lines(text):
            if before[line] > 0:
                before[line] -= 1
                continue
            what = secrets.line_secret(line)
            if what:
                found.append(f"{rel} (line {start}: {what})")
                break
    return sorted(found)


def _targets(result: AdoptResult, repo: Path) -> dict[str, Path]:
    """The file that each changed path stands for: a link is resolved to its target, inside the repository."""
    root = Path(os.path.realpath(repo))
    out = {}
    for rel in result.files:
        target = Path(os.path.realpath(repo / rel))
        if target != root and root not in target.parents:
            raise AdoptStopped(f"{rel} links to {target}, outside the repository")
        out[rel] = target
    return out


def _current(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def apply_edits(result: AdoptResult, repo: Path) -> None:
    """Change the files of the repository to the texts of result.files: all of them, or none.

    1. Check each file: it still has the text that the plan was built from
       (result.before), and a link resolves to a file inside the repository,
       whose target is changed so that the link stays a link. Otherwise
       raise AdoptStopped. Then write every changed file as a temp file next
       to its target, with the mode of the file it replaces.
    2. Verify them: a secret in a line that the file did not have raises
       SecretInRepository, and nothing is written.
    3. os.replace them one by one.
    4. If a replace fails, write back the old texts, kept in memory.
    """
    targets = _targets(result, repo)
    old: dict[str, str | None] = {}
    for rel, target in targets.items():
        old[rel] = _current(target)
        if old[rel] != result.before.get(rel):
            raise AdoptStopped(f"{rel} changed since the scan; run dotkit migrate again")
    staged: dict[str, Path] = {}
    try:
        for rel, text in result.files.items():
            target = targets[rel]
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.dotkit-")
            staged[rel] = Path(tmp)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(tmp, stat.S_IMODE(os.stat(target).st_mode) if old[rel] is not None else 0o644)
        found = verify_edits(staged, old)
        if found:
            raise SecretInRepository("the repository would have a secret in a file that Git tracks, or a file "
                                     "that cannot be checked: " + ", ".join(found))
        done: list[str] = []
        try:
            for rel, tmp_path in staged.items():
                os.replace(tmp_path, targets[rel])
                done.append(rel)
        except BaseException:
            for rel in reversed(done):
                _restore(targets[rel], old[rel])
            raise
    finally:
        for tmp_path in staged.values():
            if os.path.lexists(tmp_path):
                os.unlink(tmp_path)
def _restore(path: Path, text: str | None) -> None:
    """Put back the old text of a file, or remove a file that did not exist. Say so when it fails."""
    try:
        if text is None:
            path.unlink()
        else:
            path.write_text(text, encoding="utf-8")
    except OSError as e:
        print(f"dotkit: could not restore {path} ({e.strerror}). Check it with git diff.", file=sys.stderr,
              flush=True)
