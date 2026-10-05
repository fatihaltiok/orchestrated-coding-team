#!/usr/bin/env python3
"""Print the team-channel scenario rating figures quoted in the paper.

Reads data/team-channel/jev_teamkanal_ergebnis.json (rating run of 1 Oct 2026)
and prints the scenario count, the rating cost (recomputed with the vendor list
price from data/team-channel/vendor-list-price.json), the number of selected
scenarios (data/team-channel/ausgewaehlt.json) and the composite scores the
paper quotes. Run from the repository root without arguments for the
overview, or with a numbers.csv id (N068-N073, N099) for exactly that row's
value on one line (unknown id: exit 2). Standard library only.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# the full relative paths on purpose: scripts/check_numbers.py requires the
# script to name the file in numbers.csv's source_file column
RESULTS_FILE = "data/team-channel/jev_teamkanal_ergebnis.json"
# the vendor list price is a data field (with date and origin), not a constant
PRICE_FILE = "data/team-channel/vendor-list-price.json"
# the scenarios selected for implementation after the rating run ("top three")
SELECT_FILE = "data/team-channel/ausgewaehlt.json"

PAPER_SCORES = {
    "uebergabe_limit": "handover when a session runs out",
    "fatih_unterwegs": "question list",
    "fertigmeldung": "done message",
    "uebergabe_pruefen": "handover check scenario",
}


# one entry per numbers.csv row: id -> (label key, format)
VALUES = {
    "N068": lambda v: f"{v['scenarios']}",
    "N069": lambda v: f"{v['cost_cents']:.2f} cents",
    "N070": lambda v: f"{v['rows']['uebergabe_limit']['komposit']}",
    "N071": lambda v: f"{v['rows']['fatih_unterwegs']['komposit']}",
    "N072": lambda v: f"{v['rows']['fertigmeldung']['komposit']}",
    "N073": lambda v: f"{v['rows']['uebergabe_pruefen']['komposit']}",
    "N099": lambda v: f"{v['selected']}",
}


def compute() -> dict:
    data = json.loads((ROOT / RESULTS_FILE).read_text(encoding="utf-8"))
    price = json.loads((ROOT / PRICE_FILE).read_text(encoding="utf-8"))
    selection = json.loads((ROOT / SELECT_FILE).read_text(encoding="utf-8"))
    tokens = data["token"]
    rows = {row["id"]: row for row in data["ergebnis"]}
    chosen = selection["ausgewaehlt"]
    unknown = [s for s in chosen if s not in rows]
    if unknown:
        raise ValueError(f"selected scenarios not in the rating run: {unknown}")
    return {
        "tokens": tokens,
        "rows": rows,
        "scenarios": len(data["ergebnis"]),
        "selected": len(chosen),
        "price_usd_per_mio_token": price["usd_per_million_tokens"],
        "cost_cents": tokens * price["usd_per_million_tokens"] / 1e6 * 100,
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
    print(f"scenarios rated: {v['scenarios']}")
    print(f"scenarios selected for implementation: {v['selected']}")
    print(f"tokens used: {v['tokens']}")
    print(f"rating cost: {v['cost_cents']:.2f} cents")
    for key, label in PAPER_SCORES.items():
        print(f"{label}: {v['rows'][key]['komposit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
