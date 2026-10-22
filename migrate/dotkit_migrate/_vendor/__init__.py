"""Third-party code that the wizard carries, because it uses the standard library only.

tomli/ is tomli 2.3.1 by Taneli Hukkinen, MIT license (tomli/LICENSE). It is
src/tomli of the PyPI sdist tomli-2.3.1.tar.gz, SHA-256
833f1bdec09874d3239b87e1527342cda82137abe730518066777ffed876acf4, without
changes. The wizard uses it only on Python 3.9 and 3.10, which have no
tomllib. tomli 2.3 reads TOML 1.0, as tomllib of Python 3.11 to 3.14 does.
"""
