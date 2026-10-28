"""Refresh lib/dotkit/_vendor from vendor.txt: download the pinned wheels and unpack them.

    /usr/bin/python3 lib/tools/vendor.py

pip checks the hashes of vendor.txt (--require-hashes) and takes wheels only,
for Python 3.9. The script removes the old packages first, so a package that
vendor.txt no longer has goes away. Review the diff and commit it with
vendor.txt. tests/test_vendor.py checks that _vendor matches vendor.txt.
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

VENDOR = pathlib.Path(__file__).resolve().parents[1] / "dotkit/_vendor"
KEEP = {"vendor.txt", "README.md", "__init__.py"}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check",
                        "--no-deps", "--require-hashes", "--only-binary=:all:", "--python-version", "3.9",
                        "--dest", tmp, "-r", str(VENDOR / "vendor.txt")], check=True)
        for p in VENDOR.iterdir():
            if p.name not in KEEP:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        for wheel in sorted(pathlib.Path(tmp).glob("*.whl")):
            with zipfile.ZipFile(wheel) as z:
                z.extractall(VENDOR)
            print(f"unpacked {wheel.name}")
    # The list of files of a wheel names the paths of an install; dotkit does not install them.
    for record in VENDOR.glob("*.dist-info/RECORD"):
        record.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
