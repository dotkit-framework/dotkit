"""Third-party code that the wizard carries, because it uses the standard library only.

tomli/ is tomli 2.3.2 by Taneli Hukkinen, MIT license (tomli/LICENSE). It is
src/tomli of the PyPI sdist tomli-2.3.2.tar.gz, SHA-256
ba6f699836c403252ae4a41ddaae9a30333bdac3fbb57b6ea28a50fffbb14d77, without
changes. The wizard uses it only on Python 3.9 and 3.10, which have no
tomllib. tomli 2.3 reads TOML 1.0, as tomllib of Python 3.11 to 3.14 does.
"""
