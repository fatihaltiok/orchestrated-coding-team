"""Tests for scripts/make_results.py.

Hard gates: rebuilding into a temporary directory must reproduce the checked-in
``results/`` files byte for byte; every value written there must sit in a
``numbers.csv`` row with method ``script:`` and the same value; and every row
must be where ``numbers.csv``'s ``source_file`` anchor says it is. Negative
controls: a hand-edited value in a copy of ``results/`` must be detected, and
changing a raw one-by-one listing in a copy of ``data/`` must change the
computed value and fail the comparison.
"""
import csv
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mr = load_module("make_results", ROOT / "scripts" / "make_results.py")


def result_files(directory: Path):
    return sorted(p.name for p in directory.glob("*.csv"))


def diff_results(generated: Path, reference: Path):
    """List of human-readable differences (empty = byte for byte equal)."""
    problems = []
    gen, ref = result_files(generated), result_files(reference)
    if gen != ref:
        problems.append(f"file lists differ: {gen} != {ref}")
        return problems
    for name in gen:
        a = (generated / name).read_bytes()
        b = (reference / name).read_bytes()
        if a != b:
            problems.append(f"{name}: bytes differ ({len(a)} vs {len(b)})")
    return problems


def read_numbers():
    with (ROOT / "numbers.csv").open(encoding="utf-8", newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def read_results_rows():
    """Yield (file name, 1-based line number, row dict) for every results row."""
    for name in result_files(RESULTS):
        with (RESULTS / name).open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for line_no, row in enumerate(reader, start=2):
                yield name, line_no, row


def test_regeneration_is_byte_for_byte(tmp_path):
    written = mr.write_results(tmp_path)
    assert sum(c for _, c in written) == len(mr.ROWS)
    assert diff_results(tmp_path, RESULTS) == []


def test_negative_control_hand_edited_value_is_detected(tmp_path):
    """A hand-edited value in a copy of results/ must break the comparison."""
    mr.write_results(tmp_path)
    assert diff_results(tmp_path, RESULTS) == []
    victim = tmp_path / "review-gaps.csv"
    rows = list(csv.DictReader(victim.read_text(encoding="utf-8").splitlines()))
    rows[0]["value_as_in_paper"] = "4 of 4"  # was "3 of 4" — by hand, not computed
    with victim.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    problems = diff_results(tmp_path, RESULTS)
    assert problems, "hand-edited results/ copy was not detected"


def test_every_results_value_has_a_script_row(tmp_path):
    numbers = read_numbers()
    count = 0
    for name, line_no, row in read_results_rows():
        count += 1
        rid = row["id"]
        assert rid in numbers, f"{name}:{line_no}: {rid} missing in numbers.csv"
        entry = numbers[rid]
        assert (entry["method"] or "").strip().startswith("script:"), (
            f"{rid}: method {entry['method']!r} is not script:")
        assert entry["value"].strip() == row["value_as_in_paper"], (
            f"{rid}: numbers.csv {entry['value']!r} != "
            f"results {row['value_as_in_paper']!r}")
        assert row["value_raw"] and row["inputs"] and row["command"], (
            f"{name}:{line_no}: empty value_raw/inputs/command")
    assert count == len(mr.ROWS)


def test_source_anchors_point_at_the_results_rows():
    """numbers.csv#L<n> must name the line the row really sits on (order rule)."""
    numbers = read_numbers()
    for name, line_no, row in read_results_rows():
        want = f"results/{name}#L{line_no}"
        got = (numbers[row["id"]]["source_file"] or "").strip()
        assert got == want, f"{row['id']}: source_file {got!r} != {want!r}"


def test_readme_names_every_file_command_and_the_recorded_note():
    readme = (RESULTS / "README.md").read_text(encoding="utf-8")
    for name in result_files(RESULTS):
        assert name in readme, f"results/README.md does not mention {name}"
    assert "data/recorded/" in readme
    for spec in mr.ROWS:
        assert spec[7] in readme, f"command for {spec[0]} not in results/README.md"


# ---------------------------------------------------------------------------
# negative controls on the raw one-by-one listings (R-N1 requirement)
# ---------------------------------------------------------------------------

def checked_in_value(rid: str) -> str:
    for name, line_no, row in read_results_rows():
        if row["id"] == rid:
            return row["value_as_in_paper"]
    raise AssertionError(f"{rid} not in checked-in results/")


def copy_data_tree(tmp_path):
    """data/ and numbers.csv copied, so compute() can run against the copy."""
    shutil.copytree(ROOT / "data", tmp_path / "data")
    shutil.copy(ROOT / "numbers.csv", tmp_path / "numbers.csv")
    return tmp_path


def test_removing_a_devin_run_changes_the_value_and_fails_the_comparison(
        tmp_path, monkeypatch):
    """Drop one run from a copy of the run list: N007 must compute 5 (was 6)
    and the rebuild-vs-numbers.csv comparison must fail."""
    root = copy_data_tree(tmp_path)
    runs_path = root / "data" / "reviews" / "devin-runs.json"
    data = json.loads(runs_path.read_text(encoding="utf-8"))
    assert data["runs"], "run list unexpectedly empty"
    data["runs"].pop()
    runs_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    monkeypatch.setattr(mr, "ROOT", root)
    got = mr.row_record(mr.BY_ID["N007"])["value_as_in_paper"]
    assert got == "5"
    assert got != checked_in_value("N007")
    with pytest.raises(ValueError):
        mr.write_results(root / "out")


def test_removing_a_working_copy_finding_line_changes_the_value(
        tmp_path, monkeypatch):
    """Drop one `> Finding` line from a copy of the extracts: N087 counts 2."""
    root = copy_data_tree(tmp_path)
    ext = root / "data" / "reviews" / "report-extracts.md"
    lines = ext.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not ln.startswith("> Finding 1 (critical)")]
    assert len(kept) == len(lines) - 1
    ext.write_text("".join(kept), encoding="utf-8")
    monkeypatch.setattr(mr, "ROOT", root)
    got = mr.row_record(mr.BY_ID["N087"])["value_as_in_paper"]
    assert got == "2"
    assert got != checked_in_value("N087")
    with pytest.raises(ValueError):
        mr.write_results(root / "out")


def test_removing_a_u2_finding_line_changes_the_value(tmp_path, monkeypatch):
    """Drop one U2 finding line from a copy of the extracts: N018 counts 5."""
    root = copy_data_tree(tmp_path)
    ext = root / "data" / "reviews" / "report-extracts.md"
    lines = ext.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not ln.startswith("> Finding 4 (important)")]
    assert len(kept) == len(lines) - 1
    ext.write_text("".join(kept), encoding="utf-8")
    monkeypatch.setattr(mr, "ROOT", root)
    got = mr.row_record(mr.BY_ID["N018"])["value_as_in_paper"]
    assert got == "5"
    assert got != checked_in_value("N018")
    with pytest.raises(ValueError):
        mr.write_results(root / "out")


def test_u2_leak_split_follows_the_headings(tmp_path, monkeypatch):
    """Rename the path leak's heading to a key: N102 counts three, N103 none."""
    root = copy_data_tree(tmp_path)
    edit_extracts(root, "an absolute path in `quelle`", "a key in `quelle`")
    monkeypatch.setattr(mr, "ROOT", root)
    assert mr.row_record(mr.BY_ID["N102"])["value_as_in_paper"] == "three"
    assert mr.row_record(mr.BY_ID["N103"])["value_as_in_paper"] == "zero"
    assert checked_in_value("N102") == "two" and checked_in_value("N103") == "one"


def run_review_counts(root: Path) -> "subprocess.CompletedProcess":
    """Run a copy of review_counts.py inside a copied tree (it reads from its own root)."""
    import subprocess
    script = root / "scripts" / "numbers" / "review_counts.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "scripts" / "numbers" / "review_counts.py", script)
    return subprocess.run([sys.executable, str(script), "N078"],
                          capture_output=True, text=True, cwd=root)


def edit_extracts(root: Path, old: str, new: str) -> None:
    ext = root / "data" / "reviews" / "report-extracts.md"
    text = ext.read_text(encoding="utf-8")
    assert text.count(old) == 1
    ext.write_text(text.replace(old, new), encoding="utf-8")


def test_n078_follows_the_gap_lines(tmp_path):
    """N078 is computed from the four gap lines: change them, the value changes.

    Guards against a script that keeps the file name but stops reading it
    (e.g. hard-coded totals), which the name check in check_numbers.py
    cannot see.
    """
    root = copy_data_tree(tmp_path)
    assert run_review_counts(root).stdout.strip() == "3 of 4"
    edit_extracts(root,
                  "silently skip the done message — status: fixed",
                  "silently skip the done message — status: open (deliberate boundary)")
    got = run_review_counts(root)
    assert got.returncode == 0 and got.stdout.strip() == "2 of 4"
    lines = (root / "data" / "reviews" / "report-extracts.md").read_text(
        encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not ln.startswith("> Gap R12-B2:")]
    (root / "data" / "reviews" / "report-extracts.md").write_text("".join(kept), encoding="utf-8")
    assert run_review_counts(root).stdout.strip() == "1 of 3"


@pytest.mark.parametrize("old,new,message", [
    ("the queue delivery failed — status: fixed",
     "the queue delivery failed — status: pending", "unknown gap status"),
    ("> Gap R13-B2:", "> Gap R13-B1:", "gap listed twice"),
])
def test_n078_rejects_unknown_status_and_duplicates(tmp_path, old, new, message):
    """A status other than fixed/open, or a duplicated gap id, is an error — never "fixed"."""
    root = copy_data_tree(tmp_path)
    edit_extracts(root, old, new)
    got = run_review_counts(root)
    assert got.returncode != 0
    assert message in got.stderr
