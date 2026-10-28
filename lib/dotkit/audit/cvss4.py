"""The base score of a CVSS 4.0 vector, as the reference calculator of FIRST computes it.

A port of cvss_score.js, cvss_lookup.js, max_composed.js, and max_severity.js of
https://github.com/FIRSTdotorg/cvss-v4-calculator (Copyright FIRST, Red Hat, and contributors;
BSD-2-Clause). The score of a vector is the score of its MacroVector (LOOKUP), lowered by its mean
distance to the highest vectors of that MacroVector. tests/audit/test_cvss4.py compares it with scores
of the calculator.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

# The score of each MacroVector: EQ1 to EQ6.
LOOKUP: Dict[str, float] = {
    "000000": 10.0, "000001": 9.9, "000010": 9.8, "000011": 9.5, "000020": 9.5, "000021": 9.2, "000100": 10.0,
    "000101": 9.6, "000110": 9.3, "000111": 8.7, "000120": 9.1, "000121": 8.1, "000200": 9.3, "000201": 9.0,
    "000210": 8.9, "000211": 8.0, "000220": 8.1, "000221": 6.8, "001000": 9.8, "001001": 9.5, "001010": 9.5,
    "001011": 9.2, "001020": 9.0, "001021": 8.4, "001100": 9.3, "001101": 9.2, "001110": 8.9, "001111": 8.1,
    "001120": 8.1, "001121": 6.5, "001200": 8.8, "001201": 8.0, "001210": 7.8, "001211": 7.0, "001220": 6.9,
    "001221": 4.8, "002001": 9.2, "002011": 8.2, "002021": 7.2, "002101": 7.9, "002111": 6.9, "002121": 5.0,
    "002201": 6.9, "002211": 5.5, "002221": 2.7, "010000": 9.9, "010001": 9.7, "010010": 9.5, "010011": 9.2,
    "010020": 9.2, "010021": 8.5, "010100": 9.5, "010101": 9.1, "010110": 9.0, "010111": 8.3, "010120": 8.4,
    "010121": 7.1, "010200": 9.2, "010201": 8.1, "010210": 8.2, "010211": 7.1, "010220": 7.2, "010221": 5.3,
    "011000": 9.5, "011001": 9.3, "011010": 9.2, "011011": 8.5, "011020": 8.5, "011021": 7.3, "011100": 9.2,
    "011101": 8.2, "011110": 8.0, "011111": 7.2, "011120": 7.0, "011121": 5.9, "011200": 8.4, "011201": 7.0,
    "011210": 7.1, "011211": 5.2, "011220": 5.0, "011221": 3.0, "012001": 8.6, "012011": 7.5, "012021": 5.2,
    "012101": 7.1, "012111": 5.2, "012121": 2.9, "012201": 6.3, "012211": 2.9, "012221": 1.7, "100000": 9.8,
    "100001": 9.5, "100010": 9.4, "100011": 8.7, "100020": 9.1, "100021": 8.1, "100100": 9.4, "100101": 8.9,
    "100110": 8.6, "100111": 7.4, "100120": 7.7, "100121": 6.4, "100200": 8.7, "100201": 7.5, "100210": 7.4,
    "100211": 6.3, "100220": 6.3, "100221": 4.9, "101000": 9.4, "101001": 8.9, "101010": 8.8, "101011": 7.7,
    "101020": 7.6, "101021": 6.7, "101100": 8.6, "101101": 7.6, "101110": 7.4, "101111": 5.8, "101120": 5.9,
    "101121": 5.0, "101200": 7.2, "101201": 5.7, "101210": 5.7, "101211": 5.2, "101220": 5.2, "101221": 2.5,
    "102001": 8.3, "102011": 7.0, "102021": 5.4, "102101": 6.5, "102111": 5.8, "102121": 2.6, "102201": 5.3,
    "102211": 2.1, "102221": 1.3, "110000": 9.5, "110001": 9.0, "110010": 8.8, "110011": 7.6, "110020": 7.6,
    "110021": 7.0, "110100": 9.0, "110101": 7.7, "110110": 7.5, "110111": 6.2, "110120": 6.1, "110121": 5.3,
    "110200": 7.7, "110201": 6.6, "110210": 6.8, "110211": 5.9, "110220": 5.2, "110221": 3.0, "111000": 8.9,
    "111001": 7.8, "111010": 7.6, "111011": 6.7, "111020": 6.2, "111021": 5.8, "111100": 7.4, "111101": 5.9,
    "111110": 5.7, "111111": 5.7, "111120": 4.7, "111121": 2.3, "111200": 6.1, "111201": 5.2, "111210": 5.7,
    "111211": 2.9, "111220": 2.4, "111221": 1.6, "112001": 7.1, "112011": 5.9, "112021": 3.0, "112101": 5.8,
    "112111": 2.6, "112121": 1.5, "112201": 2.3, "112211": 1.3, "112221": 0.6, "200000": 9.3, "200001": 8.7,
    "200010": 8.6, "200011": 7.2, "200020": 7.5, "200021": 5.8, "200100": 8.6, "200101": 7.4, "200110": 7.4,
    "200111": 6.1, "200120": 5.6, "200121": 3.4, "200200": 7.0, "200201": 5.4, "200210": 5.2, "200211": 4.0,
    "200220": 4.0, "200221": 2.2, "201000": 8.5, "201001": 7.5, "201010": 7.4, "201011": 5.5, "201020": 6.2,
    "201021": 5.1, "201100": 7.2, "201101": 5.7, "201110": 5.5, "201111": 4.1, "201120": 4.6, "201121": 1.9,
    "201200": 5.3, "201201": 3.6, "201210": 3.4, "201211": 1.9, "201220": 1.9, "201221": 0.8, "202001": 6.4,
    "202011": 5.1, "202021": 2.0, "202101": 4.7, "202111": 2.1, "202121": 1.1, "202201": 2.4, "202211": 0.9,
    "202221": 0.4, "210000": 8.8, "210001": 7.5, "210010": 7.3, "210011": 5.3, "210020": 6.0, "210021": 5.0,
    "210100": 7.3, "210101": 5.5, "210110": 5.9, "210111": 4.0, "210120": 4.1, "210121": 2.0, "210200": 5.4,
    "210201": 4.3, "210210": 4.5, "210211": 2.2, "210220": 2.0, "210221": 1.1, "211000": 7.5, "211001": 5.5,
    "211010": 5.8, "211011": 4.5, "211020": 4.0, "211021": 2.1, "211100": 6.1, "211101": 5.1, "211110": 4.8,
    "211111": 1.8, "211120": 2.0, "211121": 0.9, "211200": 4.6, "211201": 1.8, "211210": 1.7, "211211": 0.7,
    "211220": 0.8, "211221": 0.2, "212001": 5.3, "212011": 2.4, "212021": 1.4, "212101": 2.4, "212111": 1.2,
    "212121": 0.5, "212201": 1.0, "212211": 0.3, "212221": 0.1,
}

# The highest vectors of each MacroVector part. EQ3 has one list per EQ6 value.
MAX_COMPOSED = {
    "eq1": {0: ["AV:N/PR:N/UI:N/"], 1: ["AV:A/PR:N/UI:N/", "AV:N/PR:L/UI:N/", "AV:N/PR:N/UI:P/"],
            2: ["AV:P/PR:N/UI:N/", "AV:A/PR:L/UI:P/"]},
    "eq2": {0: ["AC:L/AT:N/"], 1: ["AC:H/AT:N/", "AC:L/AT:P/"]},
    "eq3": {
        0: {"0": ["VC:H/VI:H/VA:H/CR:H/IR:H/AR:H/"],
            "1": ["VC:H/VI:H/VA:L/CR:M/IR:M/AR:H/", "VC:H/VI:H/VA:H/CR:M/IR:M/AR:M/"]},
        1: {"0": ["VC:L/VI:H/VA:H/CR:H/IR:H/AR:H/", "VC:H/VI:L/VA:H/CR:H/IR:H/AR:H/"],
            "1": ["VC:L/VI:H/VA:L/CR:H/IR:M/AR:H/", "VC:L/VI:H/VA:H/CR:H/IR:M/AR:M/",
                  "VC:H/VI:L/VA:H/CR:M/IR:H/AR:M/", "VC:H/VI:L/VA:L/CR:M/IR:H/AR:H/",
                  "VC:L/VI:L/VA:H/CR:H/IR:H/AR:M/"]},
        2: {"1": ["VC:L/VI:L/VA:L/CR:H/IR:H/AR:H/"]},
    },
    "eq4": {0: ["SC:H/SI:S/SA:S/"], 1: ["SC:H/SI:H/SA:H/"], 2: ["SC:L/SI:L/SA:L/"]},
    "eq5": {0: ["E:A/"], 1: ["E:P/"], 2: ["E:U/"]},
}

# The highest severity distance in each MacroVector part, plus 1.
MAX_SEVERITY = {
    "eq1": {0: 1, 1: 4, 2: 5},
    "eq2": {0: 1, 1: 2},
    "eq3eq6": {0: {0: 7, 1: 6}, 1: {0: 8, 1: 8}, 2: {1: 10}},
    "eq4": {0: 6, 1: 5, 2: 4},
}

# The level of each metric value: the distance from the most severe value.
LEVELS = {
    "AV": {"N": 0.0, "A": 0.1, "L": 0.2, "P": 0.3}, "PR": {"N": 0.0, "L": 0.1, "H": 0.2},
    "UI": {"N": 0.0, "P": 0.1, "A": 0.2}, "AC": {"L": 0.0, "H": 0.1}, "AT": {"N": 0.0, "P": 0.1},
    "VC": {"H": 0.0, "L": 0.1, "N": 0.2}, "VI": {"H": 0.0, "L": 0.1, "N": 0.2}, "VA": {"H": 0.0, "L": 0.1, "N": 0.2},
    "SC": {"H": 0.1, "L": 0.2, "N": 0.3}, "SI": {"S": 0.0, "H": 0.1, "L": 0.2, "N": 0.3},
    "SA": {"S": 0.0, "H": 0.1, "L": 0.2, "N": 0.3},
    "CR": {"H": 0.0, "M": 0.1, "L": 0.2}, "IR": {"H": 0.0, "M": 0.1, "L": 0.2}, "AR": {"H": 0.0, "M": 0.1, "L": 0.2},
}
BASE = ("AV", "AC", "AT", "PR", "UI", "VC", "VI", "VA", "SC", "SI", "SA")


def _metrics(vector: str) -> Optional[Dict[str, str]]:
    """The metrics of a vector; None when it is not a CVSS 4.0 vector with all base metrics."""
    if not vector.startswith("CVSS:4.0/"):
        return None
    out = {}
    for part in vector.split("/")[1:]:
        key, _, value = part.partition(":")
        if not value:
            return None
        out[key] = value
    if any(k not in out for k in BASE):
        return None
    return out


def _m(metrics: Dict[str, str], metric: str) -> str:
    """The effective value: a modified metric (MAV, ...) wins; E, CR, IR, and AR default to the worst case."""
    selected = metrics.get(metric, "X")
    if metric == "E" and selected == "X":
        return "A"
    if metric in ("CR", "IR", "AR") and selected == "X":
        return "H"
    modified = metrics.get("M" + metric, "X")
    return modified if modified != "X" else selected


def _macro_vector(m) -> str:
    av, pr, ui = m("AV"), m("PR"), m("UI")
    if av == "N" and pr == "N" and ui == "N":
        eq1 = 0
    elif (av == "N" or pr == "N" or ui == "N") and av != "P":
        eq1 = 1
    else:
        eq1 = 2
    eq2 = 0 if m("AC") == "L" and m("AT") == "N" else 1
    vc, vi, va = m("VC"), m("VI"), m("VA")
    if vc == "H" and vi == "H":
        eq3 = 0
    elif vc == "H" or vi == "H" or va == "H":
        eq3 = 1
    else:
        eq3 = 2
    if m("MSI") == "S" or m("MSA") == "S":
        eq4 = 0
    elif m("SC") == "H" or m("SI") == "H" or m("SA") == "H":
        eq4 = 1
    else:
        eq4 = 2
    eq5 = {"A": 0, "P": 1, "U": 2}[m("E")]
    eq6 = 0 if ((m("CR") == "H" and vc == "H") or (m("IR") == "H" and vi == "H")
                or (m("AR") == "H" and va == "H")) else 1
    return f"{eq1}{eq2}{eq3}{eq4}{eq5}{eq6}"


def _value(metric: str, vector: str) -> str:
    """The value of a metric in a highest vector: "AV:N/PR:N/UI:N/" -> N for AV."""
    return vector[vector.index(metric + ":") + len(metric) + 1:].split("/", 1)[0]


def score(vector: str) -> Optional[float]:
    """The base score, 0.0 to 10.0; None when the vector is not a valid CVSS 4.0 vector."""
    metrics = _metrics(vector)
    if metrics is None:
        return None
    m = lambda metric: _m(metrics, metric)  # noqa: E731
    try:
        if all(m(k) == "N" for k in ("VC", "VI", "VA", "SC", "SI", "SA")):
            return 0.0
        macro = _macro_vector(m)
        for k in LEVELS:
            LEVELS[k][m(k)]  # a value that the metric does not have: KeyError
    except KeyError:
        return None
    value = LOOKUP[macro]
    eq = [int(c) for c in macro]

    def lower(*delta: int) -> Optional[float]:
        return LOOKUP.get("".join(str(e + d) for e, d in zip(eq, delta)))

    eq3, eq6 = eq[2], eq[5]
    if eq3 == 0 and eq6 == 0:
        left, right = lower(0, 0, 0, 0, 0, 1), lower(0, 0, 1, 0, 0, 0)
        # The higher of the two; the comparison of the reference is false when one is missing.
        next_eq3eq6 = left if left is not None and right is not None and left > right else right
    elif eq3 == 1 and eq6 == 0:
        next_eq3eq6 = lower(0, 0, 0, 0, 0, 1)
    elif eq6 == 1 and eq3 in (0, 1):
        next_eq3eq6 = lower(0, 0, 1, 0, 0, 0)
    else:
        next_eq3eq6 = lower(0, 0, 1, 0, 0, 1)
    next_scores = [lower(1, 0, 0, 0, 0, 0), lower(0, 1, 0, 0, 0, 0), next_eq3eq6, lower(0, 0, 0, 1, 0, 0),
                   lower(0, 0, 0, 0, 1, 0)]

    maxes: List[str] = [a + b + c + d + e
                        for a in MAX_COMPOSED["eq1"][eq[0]] for b in MAX_COMPOSED["eq2"][eq[1]]
                        for c in MAX_COMPOSED["eq3"][eq3][str(eq6)] for d in MAX_COMPOSED["eq4"][eq[3]]
                        for e in MAX_COMPOSED["eq5"][eq[4]]]
    # The first highest vector that is as severe as the vector or more; the last one when none is.
    distance: Dict[str, float] = {}
    for max_vector in maxes:
        distance = {k: LEVELS[k][m(k)] - LEVELS[k][_value(k, max_vector)] for k in LEVELS}
        if all(d >= 0 for d in distance.values()):
            break
    current = [distance["AV"] + distance["PR"] + distance["UI"], distance["AC"] + distance["AT"],
               sum(distance[k] for k in ("VC", "VI", "VA", "CR", "IR", "AR")),
               distance["SC"] + distance["SI"] + distance["SA"], 0.0]
    step = 0.1
    depth = [MAX_SEVERITY["eq1"][eq[0]] * step, MAX_SEVERITY["eq2"][eq[1]] * step,
             MAX_SEVERITY["eq3eq6"][eq3][eq6] * step, MAX_SEVERITY["eq4"][eq[3]] * step, 1.0]
    normalized = [(value - nxt) * (cur / dep) for nxt, cur, dep in zip(next_scores, current, depth) if nxt is not None]
    mean = sum(normalized) / len(normalized) if normalized else 0.0
    value = min(max(value - mean, 0.0), 10.0)
    # Math.round of JavaScript: halves go up.
    return math.floor(value * 10 + 0.5) / 10
