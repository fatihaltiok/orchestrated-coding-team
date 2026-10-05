#!/usr/bin/env python3
"""Print the review and test counts quoted in the paper.

Reads data/reviews/findings-counts.json (the evidence lines behind it are
quoted in data/reviews/report-extracts.md) and prints each figure the paper
takes from the cross-vendor reviews and test gates. Only N078 is computed
here ("3 of 4" = the four review gaps minus the ones deliberately left open,
counted from the data fields); the other figures are typed-in counters of the
JSON and are checked as `literal` rows against their fields. Run from the
repository root without arguments for the overview, or with a numbers.csv id
for exactly that row's value on one line (unknown id: exit 2). Standard
library only.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# the full relative path on purpose: scripts/check_numbers.py requires the
# script to name the file in numbers.csv's source_file column
DATA_FILE = "data/reviews/findings-counts.json"


# one entry per numbers.csv row computed by this script: id -> f(values)
VALUES = {
    "N078": lambda v: f"{v['gaps_fixed']} of {v['gaps_total']}",
}


def compute() -> dict:
    counts = json.loads((ROOT / DATA_FILE).read_text(encoding="utf-8"))
    # "three of the four gaps were fixed": the open ones are a data field with
    # provenance (gaps_left_open_on_purpose), not a number typed in here
    counts["gaps_total"] = (
        counts["reviews"]["R12_team_channel"]["findings"]
        + counts["reviews"]["R13_run_watcher"]["findings"]
    )
    counts["gaps_open"] = counts["gaps_left_open_on_purpose"]["count"]
    counts["gaps_fixed"] = counts["gaps_total"] - counts["gaps_open"]
    return counts


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    counts = compute()
    if argv:
        fmt = VALUES.get(argv[0])
        if fmt is None or len(argv) != 1:
            print(f"unknown id: {argv[0]}", file=sys.stderr)
            return 2
        print(fmt(counts))
        return 0
    reviews = counts["reviews"]
    gaps = counts["working_copy_gaps"]
    tests = counts["tests_499"]
    tests2 = counts["tests_752"]
    mem = counts["memory_checker_tests"]
    wc = counts["working_copy_tests"]

    print(f"U0 contracts findings (Kimi): {reviews['V1_contract_check']['findings']}")
    print(f"U1 context package findings (Codex): {reviews['R1_context_package']['findings']}")
    print(f"U2 Jev module findings (Codex): {reviews['R2_jev_module']['findings']}")
    print(f"U2 leaks among them: {reviews['R2_jev_module']['of_which_leaks']}")
    print(f"U3 rule-file findings (Kimi): {reviews['R3_rule_tool']['findings']}")
    print(f"U3 small-parts findings (Muse): {reviews['R4_small_parts']['findings']}")
    print(f"U5 findings (Kimi): {reviews['R5_u5']['findings']}")
    print(f"U5 medium among them: {reviews['R5_u5']['of_which_medium']}")
    print(f"U6 findings (Kimi): {reviews['R6_u6']['findings']}")
    print(f"U7 confirmed findings (Muse): {reviews['R7_u7']['findings']}")
    print(f"U7 memo items: {reviews['R7_u7']['memo']}")
    print(f"team channel review gaps (channel part): {reviews['R12_team_channel']['findings']}")
    print(f"team channel review gaps (watcher part): {reviews['R13_run_watcher']['findings']}")
    print(f"team channel gaps fixed: {counts['gaps_fixed']} of {counts['gaps_total']}")
    print(
        f"working-copy gaps: {gaps['outside_review_findings']} + "
        f"{gaps['orchestrator_review_findings']} - {gaps['overlap_found_by_both']} "
        f"= {gaps['distinct_gaps']} distinct gaps"
    )
    print(f"memory checker tests: {mem['green']} of {mem['of']}")
    print(f"working-copy tests: {wc['green']} of {wc['of']}")
    print(f"tool tests on 2026-09-27: {tests['passed']} passed (commit {tests['commit']})")
    print(f"tool tests on 2026-10-01: {tests2['passed']} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
