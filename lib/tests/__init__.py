"""The tests. `unittest discover -s tests -t .` runs them through load_tests, which adds a last check:
the run left no new dotkit-migrate-* entries in the temporary directory (see fake.tempdir)."""

import os
import unittest


def load_tests(loader, standard_tests, pattern):
    from tests import fake

    before = fake.temp_entries()
    standard_tests.addTests(loader.discover(start_dir=os.path.dirname(__file__), pattern=pattern))

    def no_temp_left():
        removed, kept = fake.check_temp(before)
        if removed or kept:
            raise AssertionError(f"the tests left {len(removed) + len(kept)} temporary directories. Removed: "
                                 f"{', '.join(removed[:20]) or 'none'}. Not removed (no pid in the name): "
                                 f"{', '.join(kept[:20]) or 'none'}")

    standard_tests.addTest(unittest.FunctionTestCase(
        no_temp_left, description="the tests leave no dotkit-migrate-* entries in the temporary directory"))
    return standard_tests
