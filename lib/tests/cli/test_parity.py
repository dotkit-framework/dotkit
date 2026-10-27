"""The parity tests of the dotkit command (CLI-PLAN.md, section 4.3).

Each scenario of scenarios.py runs in a sandbox (harness.py), and its result
must equal golden/<scenario>.json. The goldens come from the Bash scripts.
DOTKIT_CLI_IMPL selects the implementation: python (the default) or bash.
To write the goldens again after a change on purpose, with the Bash scripts:

    cd lib && DOTKIT_CLI_IMPL=bash DOTKIT_CLI_REGOLD=1 /usr/bin/python3 -B -m unittest tests.cli.test_parity

then review the diff of tests/cli/golden/ and commit it with the change.
The sandbox of `nix flake check` has no Bash 3.2 and no scripts/, so these
tests are skipped there.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.cli import harness
from tests.cli.scenarios import SCENARIOS

GOLDEN = Path(__file__).resolve().parent / "golden"
PARITY = Path(__file__).resolve().parents[1] / "PARITY.md"
IMPLS = {"bash": harness.BASH, "python": harness.PYTHON}
IMPL = IMPLS[os.environ.get("DOTKIT_CLI_IMPL", "python")]
# Only the Bash scripts write goldens: they are the reference.
REGOLD = os.environ.get("DOTKIT_CLI_REGOLD") == "1" and IMPL is harness.BASH


def _no_text(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("stdout", "stderr", "terminal")}


def _sorted_stdout(r: dict) -> dict:
    return {**r, "stdout": sorted(r["stdout"].splitlines())}


# What the other implementations must match for a deviation of PARITY.md: a view of the result.
COMPARE = {"DV3": _no_text, "DV4": _sorted_stdout}


@unittest.skipIf(IMPL.missing(), f"the {IMPL.name} implementation cannot run here: {IMPL.missing()}")
class ParityTests(unittest.TestCase):
    maxDiff = None

    def test_scenarios(self):
        todo = [s for s in SCENARIOS if not s.skip()
                and (IMPL is harness.BASH or not (s.deviation or "").startswith("B"))]
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
            results = dict(zip([s.name for s in todo], pool.map(lambda s: harness.run(IMPL, s), todo)))
        if REGOLD:
            GOLDEN.mkdir(exist_ok=True)
            for s in todo:
                (GOLDEN / f"{s.name}.json").write_text(json.dumps(results[s.name], indent=1, ensure_ascii=False)
                                                       + "\n", encoding="utf-8")
        for s in todo:
            with self.subTest(scenario=s.name):
                path = GOLDEN / f"{s.name}.json"
                self.assertTrue(path.exists(), f"no golden for {s.name}; see the docstring")
                want = json.loads(path.read_text(encoding="utf-8"))
                got = results[s.name]
                if s.deviation in COMPARE and IMPL is not harness.BASH:
                    want, got = COMPARE[s.deviation](want), COMPARE[s.deviation](got)
                self.assertEqual(got, want)



def parity_rows() -> dict:
    """The rows of PARITY.md: ID -> the text of its last column ("Covered by", "Reason", or "Python")."""
    rows = {}
    for line in PARITY.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("| ") else []
        if cells and re.fullmatch(r"[A-Z][a-z]?\d+|DV\d+|B\d+", cells[0]):
            rows[cells[0]] = cells[-1]
    return rows


class InventoryTests(unittest.TestCase):
    """The scenarios and PARITY.md agree. These run everywhere: they read files only."""

    def test_rows_of_scenarios_are_in_the_inventory(self):
        rows = parity_rows()
        for s in SCENARIOS:
            with self.subTest(scenario=s.name):
                self.assertTrue(s.rows, "a scenario names the rows that it covers")
                self.assertEqual([r for r in s.rows if r not in rows], [])
                if s.deviation:
                    self.assertIn(s.deviation, rows)

    def test_rows_covered_by_scenarios_have_one(self):
        covered = {r for s in SCENARIOS for r in s.rows}
        missing = sorted(r for r, how in parity_rows().items() if how.startswith("scenarios") and r not in covered)
        self.assertEqual(missing, [], "rows of PARITY.md that say scenarios, without a scenario")

    def test_every_golden_has_a_scenario(self):
        names = {s.name for s in SCENARIOS}
        extra = sorted(p.stem for p in GOLDEN.glob("*.json") if p.stem not in names)
        self.assertEqual(extra, [], "goldens without a scenario")


if __name__ == "__main__":
    unittest.main()
