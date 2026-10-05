#!/usr/bin/env python3
"""Compute how long package U7 took, from the recorded commit times.

Reads data/reviews/u7-timing.json (two timestamps of 27 Sept 2026) and prints
the duration and the paper's verdict ("less than an hour"). Run from the
repository root without arguments for the overview, or with a numbers.csv id
(N023) for exactly that row's value on one line (unknown id: exit 2).
Standard library only.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# the full relative path on purpose: scripts/check_numbers.py requires the
# script to name the file in numbers.csv's source_file column
DATA = ROOT / "data/reviews/u7-timing.json"


VALUES = {
    "N023": lambda v: "less than an hour" if v["ok"] else "an hour or more",
}


def compute() -> dict:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    start = datetime.fromisoformat(data["task_committed"]["time"])
    end = datetime.fromisoformat(data["done_committed"]["time"])
    minutes = (end - start).total_seconds() / 60
    return {"minutes": minutes, "ok": minutes < 60}


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
    print(f"U7 from OK to done: {v['minutes']:.0f} minutes")
    print(f"less than an hour: {'true' if v['ok'] else 'false'}")
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
