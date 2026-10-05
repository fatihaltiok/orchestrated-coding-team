#!/usr/bin/env python3
"""Count the sources the 27 Sept 2026 literature search listed as opened.

Reads data/reviews/literature-sources.csv (one row per source in the search's
own source table) and prints the count behind the paper's "about 100 sources".
That each listed source was opened (mostly its abstract page) is stated in the
search report itself, see data/reviews/literature-search-note.md.
Run from the repository root without arguments for the overview, or with a
numbers.csv id (N082) for exactly that row's value on one line (unknown id:
exit 2). Standard library only.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# the full relative path on purpose: scripts/check_numbers.py requires the
# script to name the file in numbers.csv's source_file column
DATA = ROOT / "data/reviews/literature-sources.csv"


VALUES = {
    "N082": lambda v: f"{v['approx']}",
}


def compute() -> dict:
    with DATA.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("title")]
    n = len(rows)
    return {"n": n, "approx": round(n, -2)}  # 103 -> 100


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
    print(f"sources listed in the search report: {v['n']}")
    print(f"rounded as in the paper: about {v['approx']} sources (opened; mostly the abstract page read, see data/reviews/literature-search-note.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
