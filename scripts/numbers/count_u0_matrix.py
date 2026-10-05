#!/usr/bin/env python3
"""Count the rule-file comparison matrix (26 Sept 2026).

Reads data/reviews/u0-matrix.csv (one classification row per rule) and prints
the figures the paper quotes: how many rules the second tool's copy was
missing and how many said something different. Run from the repository root
without arguments for the overview, or with a numbers.csv id (N003, N031) for
exactly that row's value on one line (unknown id: exit 2). Standard library
only.
"""

import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# the full relative path on purpose: scripts/check_numbers.py requires the
# script to name the file in numbers.csv's source_file column
DATA = ROOT / "data/reviews/u0-matrix.csv"


VALUES = {
    "N003": lambda v: f"{v['missing']} of {v['total']}",
    "N031": lambda v: f"{v['differing']}",
}


def compute() -> dict:
    with DATA.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("rule_nr")]
    counts = Counter(r["status_in_second_tool"] for r in rows)
    return {
        "total": len(rows),
        "missing": counts.get("fehlt", 0),
        "differing": counts.get("abweichend", 0),
        "trivial": counts.get("abweichend-trivial", 0),
    }


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    v = compute()
    if argv:
        fmt = VALUES.get(argv[0])
        if fmt is None or len(argv) != 1:
            print(f"unknown id: {argv[0]}", file=sys.stderr)
            return 2
        print(fmt(v))
        return 0
    total, missing = v["total"], v["missing"]
    differing, trivial = v["differing"], v["trivial"]
    print(f"rules compared: {total}")
    print(f"missing in the copy: {missing} of {total}")
    print(f"differing wording: {differing}")
    print(f"differing but trivial (not counted in the paper): {trivial}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
