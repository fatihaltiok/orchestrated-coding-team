#!/usr/bin/env python3
"""Compute the share of the project index a tool actually read.

Reads data/reviews/wegweiser-bytes.json (measured byte sizes of the shared
project index before/after the 2026-09-27 rewrite) and prints the percentage
the paper quotes ("so 43 %"). Run from the repository root without arguments
for the overview, or with a numbers.csv id (N034) for exactly that row's
value on one line (unknown id: exit 2). Standard library only.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# the full relative path on purpose: scripts/check_numbers.py requires the
# script to name the file in numbers.csv's source_file column
DATA = ROOT / "data/reviews/wegweiser-bytes.json"


VALUES = {
    "N034": lambda v: f"{round(v['share']):.0f} %",
}


def compute() -> dict:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    before = data["before"]["bytes"]
    read = data["codex_read_bytes"]
    after = data["after"]["bytes"]
    return {"before": before, "read": read, "after": after, "share": read / before * 100}


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
    print(f"index size before: {v['before']} bytes")
    print(f"read by the tool: {v['read']} bytes")
    print(f"share read: {v['share']:.1f} %")
    print(f"rounded as in the paper: {round(v['share']):.0f} %")
    print(f"index size after: {v['after']} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
