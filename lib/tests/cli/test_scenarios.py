"""The scenarios of the dotkit command.

Each scenario of scenarios.py runs the launcher in a sandbox (harness.py), and
its result must equal golden/<scenario>.json: the exit code, the output, the
changed files, and the commands that dotkit runs. A change of a golden is a
change of behavior: change the row of BEHAVIOR.md with it. To write the
goldens again after such a change:

    cd lib && DOTKIT_CLI_REGOLD=1 /usr/bin/python3 -B -m unittest tests.cli.test_scenarios

then review the diff of tests/cli/golden/ and commit it with the change.
The sandbox of `nix flake check` has no copy of the launcher dotkit, so these
tests are skipped there; CI runs them in the check job.
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
BEHAVIOR = Path(__file__).resolve().parents[1] / "BEHAVIOR.md"
IMPL = harness.PYTHON
REGOLD = os.environ.get("DOTKIT_CLI_REGOLD") == "1"



@unittest.skipIf(IMPL.missing(), f"the {IMPL.name} implementation cannot run here: {IMPL.missing()}")
class ScenarioTests(unittest.TestCase):
    maxDiff = None

    def test_scenarios(self):
        todo = [s for s in SCENARIOS if not s.skip()]
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
                self.assertEqual(got, want)



def behavior_rows() -> dict:
    """The rows of BEHAVIOR.md: ID -> the text of its last column, "Covered by"."""
    rows = {}
    for line in BEHAVIOR.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("| ") else []
        if cells and re.fullmatch(r"[A-Z][a-z]?\d+", cells[0]):
            rows[cells[0]] = cells[-1]
    return rows


class InventoryTests(unittest.TestCase):
    """The scenarios and BEHAVIOR.md agree. These run everywhere: they read files only."""

    def test_rows_of_scenarios_are_in_the_inventory(self):
        rows = behavior_rows()
        for s in SCENARIOS:
            with self.subTest(scenario=s.name):
                self.assertTrue(s.rows, "a scenario names the rows that it covers")
                self.assertEqual([r for r in s.rows if r not in rows], [])

    def test_rows_covered_by_scenarios_have_one(self):
        covered = {r for s in SCENARIOS for r in s.rows}
        missing = sorted(r for r, how in behavior_rows().items() if how.startswith("scenarios") and r not in covered)
        self.assertEqual(missing, [], "rows of BEHAVIOR.md that say scenarios, without a scenario")

    def test_every_golden_has_a_scenario(self):
        names = {s.name for s in SCENARIOS}
        extra = sorted(p.stem for p in GOLDEN.glob("*.json") if p.stem not in names)
        self.assertEqual(extra, [], "goldens without a scenario")


if __name__ == "__main__":
    unittest.main()
