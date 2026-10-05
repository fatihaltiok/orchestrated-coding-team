#!/usr/bin/env python3
"""Recompute the paper's U4 measurement figures from the raw run data.

Reads data/u4-jev/ (runs, reference judgments) and prints every figure the
paper quotes from the 27 Sept 2026 judging-model measurement. Run from the
repository root without arguments for the overview, or with a numbers.csv id
for exactly that row's value on one line (unknown id: exit 2). Standard
library only.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# the full relative paths on purpose: scripts/check_numbers.py requires the
# script to name the file in numbers.csv's source_file column
ERGEBNIS = "data/u4-jev/ergebnis.json"
REFERENZ = "data/u4-jev/referenz.json"
ENTSCHEID = "data/u4-jev/referenz/entscheid.json"
AUFTRAEGE = "data/u4-jev/auftraege.json"
JUDGE_FILES = [
    "data/u4-jev/referenz/beurteiler-1.json",
    "data/u4-jev/referenz/beurteiler-2.json",
]
# the measurement tool carries the spending cap; this script reads it from
# there instead of typing the number in again (numbers.csv N050)
MEASURE_TOOL = "scripts/measurements/u4_jev_messung.py"


def load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def cap_usd() -> float:
    """Spending cap of the measurement tool (its obergrenze_usd default)."""
    text = (ROOT / MEASURE_TOOL).read_text(encoding="utf-8")
    m = re.search(r"obergrenze_usd\s*=\s*(\d*\.?\d+)", text)
    if not m:
        raise ValueError(f"no obergrenze_usd constant found in {MEASURE_TOOL}")
    return float(m.group(1))


# one entry per numbers.csv row: id -> f(values)
VALUES = {
    "N004": lambda v: f"{v['jev_top10']}",
    "N005": lambda v: f"{v['lokal_top10']}",
    "N046": lambda v: f"{v['tasks']}",
    "N047": lambda v: f"{v['agree']:.0f} %",
    "N048": lambda v: f"{v['disputed']}",
    "N049": lambda v: f"{v['rules']}",
    "N050": lambda v: f"{v['cap_cents']:.0f} cents",
    "N051": lambda v: f"{v['lokal_top5']}",
    "N052": lambda v: f"{v['jev_top5']}",
    "N053": lambda v: f"{v['controls']} of {v['control_total']}",
    "N054": lambda v: f"{v['stability']:.1f} %",
    "N055": lambda v: f"{v['cost_mean']:.3f}",
    "N056": lambda v: f"{v['time_mean']:.0f} s",
    "N057": lambda v: f"{v['fehlalarm']}",
    "N058": lambda v: f"{v['irrelevant_total']}",
    "N094": lambda v: f"{v['judges']}",
}


def compute() -> dict:
    erg = load(ERGEBNIS)
    ref = load(REFERENZ)
    jobs = load(AUFTRAEGE)["auftraege"]
    decisions = load(ENTSCHEID)

    lokal_top5 = lokal_top10 = jev_top5 = jev_top10 = fehlalarm = 0
    controls = 0
    control_total = 0
    costs = []
    times = []
    for task in erg["auftraege"].values():
        for run in ("L1", "L2"):
            m = task[run]
            if run == "L1":
                lokal_top5 += m["lokal_top5"]
                lokal_top10 += m["lokal_top10"]
                jev_top5 += m["jev_top5"]
                jev_top10 += m["jev_top10"]
                fehlalarm += m["fehlalarm"]
            control_total += 1
            if m["kontrolle"]["bestanden"]:
                controls += 1
            costs.append(m["kosten_usd"])
            times.append(m["laufzeit_s"])

    total = sum(len(c) for c in ref["auftraege"].values())
    relevant = sum(1 for c in ref["auftraege"].values() for e in c.values() if e.get("relevant"))
    disputed = sum(1 for c in ref["auftraege"].values() for e in c.values() if e.get("strittig"))
    if len(decisions) != disputed:
        raise ValueError(
            f"{ENTSCHEID} has {len(decisions)} entries, but {REFERENZ} marks {disputed} disputed")
    rules = erg["entscheidungsregel"]
    return {
        "tasks": len(jobs),
        "judges": sum(1 for p in JUDGE_FILES if (ROOT / p).is_file()),
        "lokal_top5": lokal_top5,
        "lokal_top10": lokal_top10,
        "jev_top5": jev_top5,
        "jev_top10": jev_top10,
        "fehlalarm": fehlalarm,
        "irrelevant_total": total - relevant,
        "agree": ref["uebereinstimmung"]["gesamt"] * 100,
        "disputed": disputed,
        "cap_cents": cap_usd() * 100,
        "rules": sum(1 for k in rules if k != "alle_erfuellt"),
        "controls": controls,
        "control_total": control_total,
        "stability": erg["stabilitaet_gesamt"]["gleiche_seite"] * 100,
        "cost_mean": sum(costs) / len(costs),
        "time_mean": sum(times) / len(times),
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
    print(f"real tasks: {v['tasks']}")
    print(f"blind judges per task: {v['judges']}")
    print(f"keyword top5 sum: {v['lokal_top5']}")
    print(f"keyword top10 sum: {v['lokal_top10']}")
    print(f"jev top5 sum: {v['jev_top5']}")
    print(f"jev top10 sum: {v['jev_top10']}")
    print(f"irrelevant sections high score (false alarms): {v['fehlalarm']}")
    print(f"irrelevant sections total: {v['irrelevant_total']}")
    print(f"judge agreement: {v['agree']:.1f} %")
    print(f"disputed cases: {v['disputed']}")
    print(f"decision rules: {v['rules']}")
    print(f"spending cap: {v['cap_cents']:.0f} cents ({cap_usd()} usd, from {MEASURE_TOOL})")
    print(f"control passed: {v['controls']} of {v['control_total']} runs")
    print(f"repeat run same judgment: {v['stability']:.1f} %")
    print(f"cost per task usd (mean of runs): {v['cost_mean']:.4f}")
    print(f"time per task seconds (mean of runs): {v['time_mean']:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
