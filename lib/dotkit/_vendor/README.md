# Vendored packages

dotkit runs with the Python of the Xcode Command Line Tools
(`/usr/bin/python3 -I -B`), so it cannot install packages. It carries the
few it uses here, as pip carries its own. `dotkit/__init__.py` puts this
directory first on `sys.path`.

| Package | Used for |
|---|---|
| `prompt_toolkit` | the source picker of `dotkit add` |
| `wcwidth` | the width of text in the terminal (also a dependency of `prompt_toolkit`) |
| `tomli` | TOML on Python 3.9 and 3.10, which have no `tomllib` |

Rules:

- Pure Python wheels only, each with a release that supports Python 3.9.
  `prompt_toolkit` 3.0.52 is the last one.
- No changes to the vendored code. A fix goes upstream, or into dotkit.
- `vendor.txt` pins each version with the hash of its wheel. To change one,
  edit `vendor.txt` (`pip hash` gives the hash), run
  `/usr/bin/python3 lib/tools/vendor.py`, review the diff, and run the tests.
- The licenses are in each `*.dist-info` directory.
