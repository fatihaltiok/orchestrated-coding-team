#!/usr/bin/env python3
"""Count the duplicate-run figures from the pseudonymized pair data.

Reads data/dedup-run/ (note slugs replaced by n001, n002, …) and prints the
figures the paper quotes from the 21 Sept 2026 run: number of note pairs,
unlinked related pairs and the note-base size. Run from the repository root
without arguments for the overview, or with a numbers.csv id (N042, N044,
N061) for exactly that row's value on one line (unknown id: exit 2).
Standard library only.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# the full relative paths on purpose: scripts/check_numbers.py requires the
# script to name the file in numbers.csv's source_file column
PAIRS_FILE = "data/dedup-run/jev-dublettenlauf-2026-09-21.json"
UNLINKED_FILE = "data/dedup-run/jev-unverlinkte-paare-2026-09-21.json"


VALUES = {
    "N042": lambda v: f"{v['pairs']:,}",
    "N044": lambda v: f"{v['unlinked']}",
    "N061": lambda v: f"{v['notes']}",
}


def compute() -> dict:
    pairs = json.loads((ROOT / PAIRS_FILE).read_text(encoding="utf-8"))
    unlinked = json.loads((ROOT / UNLINKED_FILE).read_text(encoding="utf-8"))
    slugs = set()
    for key in pairs:
        left, right = key.split("||", 1)
        slugs.add(left)
        slugs.add(right)
    return {"pairs": len(pairs), "unlinked": len(unlinked), "notes": len(slugs)}


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
    print(f"note pairs in the full run: {v['pairs']}")
    print(f"related pairs without a link: {v['unlinked']}")
    print(f"notes in the pair run: {v['notes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
