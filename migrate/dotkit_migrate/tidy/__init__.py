"""dotkit tidy: the apps of this Mac, a clean source for each one, and the changes (TIDY.md).

apps.py scans and plan.py makes and checks the plan. Both follow the rules
of migrators/: no writes, commands only through ctx.runner. actions.py is
the only module of the package that changes the Mac, and cli.py is the only
one that creates its Actions.
"""
