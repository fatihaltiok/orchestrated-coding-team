#!/usr/bin/env python3
"""Build the ``results/`` files: computed figures written down literally.

Every row this script writes is a value some script computes from raw data in
``data/`` (counting a list, averaging runs, a share, rounding to the printed
precision). Nothing is typed in here by hand and no typed-in JSON counter is
copied: the printed value is produced by the same computation scripts that
``scripts/check_numbers.py`` runs (imported from ``scripts/numbers/``), and a
value that only stands in a project-log excerpt (method ``recorded``) is
deliberately **not** written — its raw data does not exist any more (see
``data/recorded/`` and ``results/README.md``).

Eleven rows are computed here because no script in ``scripts/numbers/`` counts
their lists yet: the Devin-run counts (N007-N009, counted from the run list in
``data/reviews/devin-runs.json``), the outside-review findings (N087, counted
from the one-by-one listing in ``data/reviews/report-extracts.md``), the
U2/U3 review findings and leaks (N017-N019, counted from the one-by-one
finding listings in ``data/reviews/report-extracts.md``; N102/N103 split the
U2 leaks by the words of their headings: "path" or "key"), the memory notes
(N002, counted from the snapshot ``data/memory-snapshot/notes-2026-10-05.json``)
and the test count of the published tools (N101, ``pytest --collect-only`` on
``tools/tests``).

Usage (from the repository root):

    python3 scripts/make_results.py              # write results/ (byte-stable)
    python3 scripts/make_results.py --output DIR # write into DIR instead
    python3 scripts/make_results.py N042         # print that row's value, one line

With a row id nothing is written; the script prints exactly that row's value so
``scripts/check_numbers.py`` can run it like any other numbers script (unknown
id: exit 2). Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = "results"
HEADER = ["id", "quantity", "value_as_in_paper", "value_raw", "inputs", "command"]

# data files this script reads itself (the rows computed here)
DEVIN_RUNS = "data/reviews/devin-runs.json"
REPORT_EXTRACTS = "data/reviews/report-extracts.md"
MEMORY_SNAPSHOT = "data/memory-snapshot/notes-2026-10-05.json"
TOOL_TESTS = "tools/tests"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


u4 = _load("u4_metrics", "scripts/numbers/u4_metrics.py")
u0 = _load("count_u0_matrix", "scripts/numbers/count_u0_matrix.py")
dedup = _load("dedup_counts", "scripts/numbers/dedup_counts.py")
tc = _load("team_channel_scores", "scripts/numbers/team_channel_scores.py")
review = _load("review_counts", "scripts/numbers/review_counts.py")
u7 = _load("u7_duration", "scripts/numbers/u7_duration.py")
weg = _load("wegweiser_share", "scripts/numbers/wegweiser_share.py")
sources = _load("count_sources", "scripts/numbers/count_sources.py")


# --------------------------------------------------------------------------
# the rows computed here (counting an existing one-by-one listing)
# --------------------------------------------------------------------------

def devin_counts() -> dict:
    """Count the Devin run list (every run is one entry, nothing typed in)."""
    data = json.loads((ROOT / DEVIN_RUNS).read_text(encoding="utf-8"))
    runs = data["runs"]
    return {
        "runs": len(runs),
        "packages": len({r["package"] for r in runs}),
        "stopped": sum(1 for r in runs if r.get("stopped_early")),
    }


# one machine-readable finding line: "> Finding <no> (<severity>[; leak]): <heading>"
FINDING_RE = re.compile(r"^> Finding (\S+) \(([^)]*)\):", re.M)
FINDING_HEADING_RE = re.compile(r"^> Finding \S+ \(([^)]*)\): (.*)$", re.M)


def extracts_section(heading_fragment: str) -> str:
    """Body of the first ## section of the extracts whose heading matches."""
    text = (ROOT / REPORT_EXTRACTS).read_text(encoding="utf-8")
    for part in re.split(r"(?m)^## ", text)[1:]:
        if heading_fragment in part.splitlines()[0]:
            return part
    raise ValueError(f"no extracts section heading containing {heading_fragment!r}")


def finding_lines(heading_fragment: str) -> list:
    """(number, severity-marker) of every finding listed in that section."""
    return FINDING_RE.findall(extracts_section(heading_fragment))


def u2_leak_kinds() -> dict:
    """Split the U2 leaks by what escapes: a key, or a file path (heading words)."""
    leaks = [heading.lower() for marker, heading in
             FINDING_HEADING_RE.findall(extracts_section("U2 Jev-module review"))
             if any(part.strip() == "leak" for part in marker.split(";"))]
    return {"path_leaks": sum(1 for h in leaks if "path" in h),
            "key_leaks": sum(1 for h in leaks if "path" not in h and "key" in h)}


def tool_test_count() -> dict:
    """Number of tests pytest collects in the published tools (tools/tests)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", TOOL_TESTS, "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})
    m = re.search(r"^(\d+) tests? collected", proc.stdout, re.M)
    if proc.returncode != 0 or m is None:
        raise ValueError(f"pytest collection of {TOOL_TESTS} failed")
    return {"tests": int(m.group(1))}


def memory_note_count() -> dict:
    """Count the notes in the dated memory snapshot (one entry per note)."""
    data = json.loads((ROOT / MEMORY_SNAPSHOT).read_text(encoding="utf-8"))
    return {"notes": len(data["notes"])}


def outside_findings() -> dict:
    """Count the outside review's findings listed one by one in the extracts."""
    return {"findings": len(finding_lines("Working-copy mode"))}


def review_finding_counts() -> dict:
    """Count the U2/U3 findings (and the U2 leaks) listed one by one."""
    u2 = finding_lines("U2 Jev-module review")
    u3 = finding_lines("U3 small-parts review")
    return {
        "u3_findings": len(u3),
        "u2_findings": len(u2),
        "u2_leaks": sum(
            1 for _, marker in u2
            if any(part.strip() == "leak" for part in marker.split(";"))),
    }


# --------------------------------------------------------------------------
# the rows that go into results/: id -> file, quantity, how to print
# --------------------------------------------------------------------------

NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten"]


def _word(n: int) -> str:
    """Small counts as the paper spells them in prose (zero to ten as words)."""
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)


def _num(v) -> str:
    """Unrounded number as computed (int exact, float at full precision)."""
    return str(v) if isinstance(v, int) else repr(v)


# (id, results file, quantity, compute, paper format, raw format, inputs, command)
ROWS = [
    ("N003", "rule-file-comparison.csv",
     "rules missing in the second tool's copy (of total)",
     u0.compute, u0.VALUES["N003"], lambda v: f"{v['missing']} of {v['total']}",
     "data/reviews/u0-matrix.csv", "python3 scripts/numbers/count_u0_matrix.py N003"),
    ("N031", "rule-file-comparison.csv",
     "rules with different wording in the second tool's copy",
     u0.compute, u0.VALUES["N031"], lambda v: _num(v["differing"]),
     "data/reviews/u0-matrix.csv", "python3 scripts/numbers/count_u0_matrix.py N031"),

    ("N004", "u4-jev-ranking.csv",
     "Jev relevant sections in the top ten (sum over five tasks)",
     u4.compute, u4.VALUES["N004"], lambda v: _num(v["jev_top10"]),
     "data/u4-jev/ergebnis.json", "python3 scripts/numbers/u4_metrics.py N004"),
    ("N005", "u4-jev-ranking.csv",
     "keyword search relevant sections in the top ten (sum over five tasks)",
     u4.compute, u4.VALUES["N005"], lambda v: _num(v["lokal_top10"]),
     "data/u4-jev/ergebnis.json", "python3 scripts/numbers/u4_metrics.py N005"),
    ("N046", "u4-jev-ranking.csv",
     "real tasks in the measurement",
     u4.compute, u4.VALUES["N046"], lambda v: _num(v["tasks"]),
     "data/u4-jev/auftraege.json", "python3 scripts/numbers/u4_metrics.py N046"),
    ("N047", "u4-jev-ranking.csv",
     "blind reference judges agreeing (share, rounded)",
     u4.compute, u4.VALUES["N047"], lambda v: _num(v["agree"]),
     "data/u4-jev/referenz.json", "python3 scripts/numbers/u4_metrics.py N047"),
    ("N048", "u4-jev-ranking.csv",
     "disputed reference cases decided by the orchestrator",
     u4.compute, u4.VALUES["N048"], lambda v: _num(v["disputed"]),
     "data/u4-jev/referenz/entscheid.json", "python3 scripts/numbers/u4_metrics.py N048"),
    ("N049", "u4-jev-ranking.csv",
     "conditions of the decision rule",
     u4.compute, u4.VALUES["N049"], lambda v: _num(v["rules"]),
     "data/u4-jev/ergebnis.json", "python3 scripts/numbers/u4_metrics.py N049"),
    ("N051", "u4-jev-ranking.csv",
     "keyword search relevant sections in the top five (sum over five tasks)",
     u4.compute, u4.VALUES["N051"], lambda v: _num(v["lokal_top5"]),
     "data/u4-jev/ergebnis.json", "python3 scripts/numbers/u4_metrics.py N051"),
    ("N052", "u4-jev-ranking.csv",
     "Jev relevant sections in the top five (sum over five tasks)",
     u4.compute, u4.VALUES["N052"], lambda v: _num(v["jev_top5"]),
     "data/u4-jev/ergebnis.json", "python3 scripts/numbers/u4_metrics.py N052"),
    ("N053", "u4-jev-ranking.csv",
     "control runs passed (of runs)",
     u4.compute, u4.VALUES["N053"], lambda v: f"{v['controls']} of {v['control_total']}",
     "data/u4-jev/ergebnis.json", "python3 scripts/numbers/u4_metrics.py N053"),
    ("N054", "u4-jev-ranking.csv",
     "repeat runs with the same judgment (share, rounded)",
     u4.compute, u4.VALUES["N054"], lambda v: _num(v["stability"]),
     "data/u4-jev/ergebnis.json", "python3 scripts/numbers/u4_metrics.py N054"),
    ("N055", "u4-jev-ranking.csv",
     "mean cost per task in USD (mean over runs)",
     u4.compute, u4.VALUES["N055"], lambda v: _num(v["cost_mean"]),
     "data/u4-jev/ergebnis.json", "python3 scripts/numbers/u4_metrics.py N055"),
    ("N056", "u4-jev-ranking.csv",
     "mean time per task in seconds (mean over runs)",
     u4.compute, u4.VALUES["N056"], lambda v: _num(v["time_mean"]),
     "data/u4-jev/ergebnis.json", "python3 scripts/numbers/u4_metrics.py N056"),
    ("N057", "u4-jev-ranking.csv",
     "irrelevant sections with a high score (sum over five tasks)",
     u4.compute, u4.VALUES["N057"], lambda v: _num(v["fehlalarm"]),
     "data/u4-jev/ergebnis.json", "python3 scripts/numbers/u4_metrics.py N057"),
    ("N058", "u4-jev-ranking.csv",
     "irrelevant sections in the rollout measurement",
     u4.compute, u4.VALUES["N058"], lambda v: _num(v["irrelevant_total"]),
     "data/u4-jev/referenz.json", "python3 scripts/numbers/u4_metrics.py N058"),
    ("N094", "u4-jev-ranking.csv",
     "blind reference judges per task",
     u4.compute, u4.VALUES["N094"], lambda v: _num(v["judges"]),
     "data/u4-jev/referenz/beurteiler-1.json;data/u4-jev/referenz/beurteiler-2.json",
     "python3 scripts/numbers/u4_metrics.py N094"),

    ("N042", "dedup-run.csv",
     "note pairs evaluated in the full duplicate run",
     dedup.compute, dedup.VALUES["N042"], lambda v: _num(v["pairs"]),
     "data/dedup-run/jev-dublettenlauf-2026-09-21.json",
     "python3 scripts/numbers/dedup_counts.py N042"),
    ("N044", "dedup-run.csv",
     "related note pairs not yet linked",
     dedup.compute, dedup.VALUES["N044"], lambda v: _num(v["unlinked"]),
     "data/dedup-run/jev-unverlinkte-paare-2026-09-21.json",
     "python3 scripts/numbers/dedup_counts.py N044"),
    ("N061", "dedup-run.csv",
     "notes in the duplicate-run pair file",
     dedup.compute, dedup.VALUES["N061"], lambda v: _num(v["notes"]),
     "data/dedup-run/jev-dublettenlauf-2026-09-21.json",
     "python3 scripts/numbers/dedup_counts.py N061"),

    ("N068", "team-channel-ratings.csv",
     "team-channel scenarios rated",
     tc.compute, tc.VALUES["N068"], lambda v: _num(v["scenarios"]),
     "data/team-channel/jev_teamkanal_ergebnis.json",
     "python3 scripts/numbers/team_channel_scores.py N068"),
    ("N069", "team-channel-ratings.csv",
     "rating cost in cents (tokens x list price)",
     tc.compute, tc.VALUES["N069"], lambda v: _num(v["cost_cents"]),
     "data/team-channel/jev_teamkanal_ergebnis.json;data/team-channel/vendor-list-price.json",
     "python3 scripts/numbers/team_channel_scores.py N069"),
    ("N099", "team-channel-ratings.csv",
     "scenarios selected for implementation",
     tc.compute, tc.VALUES["N099"], lambda v: _num(v["selected"]),
     "data/team-channel/ausgewaehlt.json",
     "python3 scripts/numbers/team_channel_scores.py N099"),

    ("N007", "devin-runs.csv",
     "Devin runs up to U6 (counted from the run list)",
     devin_counts, lambda v: f"{v['runs']}", lambda v: _num(v["runs"]),
     DEVIN_RUNS, "python3 scripts/make_results.py N007"),
    ("N008", "devin-runs.csv",
     "packages in the Devin runs up to U6 (distinct packages)",
     devin_counts, lambda v: f"{v['packages']}", lambda v: _num(v["packages"]),
     DEVIN_RUNS, "python3 scripts/make_results.py N008"),
    ("N009", "devin-runs.csv",
     "Devin runs stopped early because the context package was outdated",
     devin_counts, lambda v: f"{v['stopped']}", lambda v: _num(v["stopped"]),
     DEVIN_RUNS, "python3 scripts/make_results.py N009"),

    ("N023", "u7-timing.csv",
     "wall-clock duration of package U7 (minutes between the two commits)",
     u7.compute, u7.VALUES["N023"], lambda v: f"{_num(v['minutes'])} minutes",
     "data/reviews/u7-timing.json", "python3 scripts/numbers/u7_duration.py N023"),
    ("N034", "wegweiser-index-share.csv",
     "share of the oversized project index the tool read (percent)",
     weg.compute, weg.VALUES["N034"], lambda v: _num(v["share"]),
     "data/reviews/wegweiser-bytes.json", "python3 scripts/numbers/wegweiser_share.py N034"),

    ("N002", "memory-notes.csv",
     "notes in the shared memory on 5 Oct 2026 (counted from the snapshot)",
     memory_note_count, lambda v: f"{v['notes']}", lambda v: _num(v["notes"]),
     MEMORY_SNAPSHOT, "python3 scripts/make_results.py N002"),

    ("N017", "review-findings.csv",
     "findings in the second U3 review (counted from the one-by-one listing)",
     review_finding_counts, lambda v: f"{v['u3_findings']}", lambda v: _num(v["u3_findings"]),
     REPORT_EXTRACTS, "python3 scripts/make_results.py N017"),
    ("N018", "review-findings.csv",
     "findings in the U2 review (counted from the one-by-one listing)",
     review_finding_counts, lambda v: f"{v['u2_findings']}", lambda v: _num(v["u2_findings"]),
     REPORT_EXTRACTS, "python3 scripts/make_results.py N018"),
    ("N019", "review-findings.csv",
     "U2 findings marked leak (counted from the one-by-one listing)",
     review_finding_counts, lambda v: f"{v['u2_leaks']}", lambda v: _num(v["u2_leaks"]),
     REPORT_EXTRACTS, "python3 scripts/make_results.py N019"),

    ("N102", "review-findings.csv",
     "U2 leaks through which a key could have gone out (heading names a key)",
     u2_leak_kinds, lambda v: _word(v["key_leaks"]),
     lambda v: _num(v["key_leaks"]),
     REPORT_EXTRACTS, "python3 scripts/make_results.py N102"),
    ("N103", "review-findings.csv",
     "U2 leaks of a file path (heading names a path)",
     u2_leak_kinds, lambda v: _word(v["path_leaks"]),
     lambda v: _num(v["path_leaks"]),
     REPORT_EXTRACTS, "python3 scripts/make_results.py N103"),

    ("N101", "tool-tests.csv",
     "tests collected in the published tools (pytest --collect-only)",
     tool_test_count, lambda v: f"{v['tests']}", lambda v: _num(v["tests"]),
     TOOL_TESTS, "python3 scripts/make_results.py N101"),

    ("N078", "review-gaps.csv",
     "team-channel review gaps fixed (of the four gaps)",
     review.compute, review.VALUES["N078"], lambda v: f"{v['gaps_fixed']} of {v['gaps_total']}",
     REPORT_EXTRACTS, "python3 scripts/numbers/review_counts.py N078"),
    ("N087", "review-gaps.csv",
     "outside-review findings on the working-copy mode (one by one in the extracts)",
     outside_findings, lambda v: f"{v['findings']}", lambda v: _num(v["findings"]),
     REPORT_EXTRACTS, "python3 scripts/make_results.py N087"),

    ("N082", "literature-sources.csv",
     "sources opened and read (count rounded to the printed precision)",
     sources.compute, sources.VALUES["N082"], lambda v: _num(v["n"]),
     "data/reviews/literature-sources.csv", "python3 scripts/numbers/count_sources.py N082"),
]

BY_ID = {row[0]: row for row in ROWS}


def row_record(spec):
    """One results row: computed values only, plus inputs and command."""
    rid, name, quantity, compute, fmt_paper, fmt_raw, inputs, command = spec
    values = compute()
    return {
        "id": rid,
        "quantity": quantity,
        "value_as_in_paper": fmt_paper(values),
        "value_raw": fmt_raw(values),
        "inputs": inputs,
        "command": command,
        "_file": name,
    }


def check_against_numbers_csv(records) -> None:
    """The printed value must equal the value numbers.csv lists for the id."""
    with (ROOT / "numbers.csv").open(encoding="utf-8", newline="") as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}
    for rec in records:
        row = rows.get(rec["id"])
        if row is None:
            raise ValueError(f"{rec['id']}: no numbers.csv row")
        if row["value"].strip() != rec["value_as_in_paper"]:
            raise ValueError(
                f"{rec['id']}: numbers.csv value {row['value']!r} != "
                f"computed {rec['value_as_in_paper']!r}")
        if not (row["method"] or "").strip().startswith("script:"):
            raise ValueError(f"{rec['id']}: numbers.csv method is not script:")


def write_results(output: Path) -> list:
    """Write one CSV per measurement; byte-stable (sorted rows, LF endings)."""
    records = [row_record(spec) for spec in ROWS]
    check_against_numbers_csv(records)
    output.mkdir(parents=True, exist_ok=True)
    by_file = {}
    for rec in records:
        by_file.setdefault(rec.pop("_file"), []).append(rec)
    written = []
    for name in sorted(by_file):
        path = output / name
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADER, lineterminator="\n")
            writer.writeheader()
            for rec in sorted(by_file[name], key=lambda r: r["id"]):
                writer.writerow(rec)
        written.append((name, len(by_file[name])))
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Build the results/ files from data/ (computed values only).")
    ap.add_argument("--output", help=f"target directory (default: <root>/{RESULTS})")
    ap.add_argument("id", nargs="?", help="print this row's value on one line and exit")
    args = ap.parse_args(argv)

    if args.id:
        spec = BY_ID.get(args.id)
        if spec is None or args.output:
            print(f"unknown id: {args.id}", file=sys.stderr)
            return 2
        print(row_record(spec)["value_as_in_paper"])
        return 0

    output = Path(args.output).resolve() if args.output else ROOT / RESULTS
    written = write_results(output)
    for name, count in written:
        print(f"wrote {RESULTS}/{name}: {count} row(s)")
    print(f"rows: {sum(c for _, c in written)} in {len(written)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
