"""tomllib of the standard library, or on Python 3.9 and 3.10 the same parser from _vendor."""

try:
    import tomllib
except ModuleNotFoundError:
    from ._vendor import tomli as tomllib

__all__ = ["tomllib"]
