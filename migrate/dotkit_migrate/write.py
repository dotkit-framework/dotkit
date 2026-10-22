"""Write a plan: the new repository, and nothing outside it."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .plan import Plan


def _copy_template(src: Path, dest: Path) -> None:
    """Copy the template without the permissions of the Nix store, which are read-only."""
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dest / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copyfile(Path(root, name), dest / rel / name)
            if os.access(Path(root, name), os.X_OK):
                os.chmod(dest / rel / name, 0o755)


def write(plan: Plan, template: Path) -> None:
    """Create the repository of the plan. It must not exist yet."""
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
        subprocess.run(["git", "init", "--quiet", str(tmp)], check=True)
        if os.path.lexists(repo):
            raise FileExistsError(f"{repo} exists")
        tmp.rename(repo)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
