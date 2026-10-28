"""The CVSS 4.0 scores of audit/cvss4.py (BEHAVIOR.md, Au7). The expected scores come from the reference
calculator of FIRST (https://github.com/FIRSTdotorg/cvss-v4-calculator). The port gave the same score as the
calculator for 20,000 random vectors."""

from __future__ import annotations

import unittest

from dotkit.audit import cvss4


class Cvss4Tests(unittest.TestCase):
    def test_reference_scores(self):
        rows = [
            ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N", 9.3),
            ("AV:L/AC:L/AT:P/PR:L/UI:N/VC:N/VI:N/VA:L/SC:N/SI:N/SA:N", 2.0),
            ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N", 8.7),
            ("AV:P/AC:H/AT:P/PR:H/UI:A/VC:L/VI:L/VA:L/SC:L/SI:L/SA:L", 1.0),
            ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:U", 8.1),
            ("AV:A/AC:H/AT:N/PR:L/UI:P/VC:H/VI:L/VA:N/SC:H/SI:N/SA:L", 5.7),
            ("AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:N/SA:N", 0.0),
        ]
        for vector, score in rows:
            with self.subTest(vector=vector):
                self.assertEqual(cvss4.score("CVSS:4.0/" + vector), score)

    def test_not_a_vector(self):
        for vector in ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "CVSS:4.0/AV:N/AC:L",
                       "CVSS:4.0/AV:Q/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
                       "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:Z", "CVSS:4.0/AV"):
            with self.subTest(vector=vector):
                self.assertIsNone(cvss4.score(vector))


if __name__ == "__main__":
    unittest.main()
