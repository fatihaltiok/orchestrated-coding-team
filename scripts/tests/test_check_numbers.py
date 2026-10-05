"""Tests for scripts/check_numbers.py, with negative controls.

The mandatory mutation probe reads the **real** numbers.csv and corrupts every
row with several mutant kinds (value +1, sign flip, unit swap, source swap,
number word bump); check_row must FAIL for every corrupted row — 0 survivors
expected. Rows that no mutant applies to are skipped with a reason — at most
three.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "check_numbers.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cn = load_module("check_numbers", TOOL)

HEADER = "id,value,paper_section,paper_quote,source_file,method,note"
# prints the value of the given numbers.csv id (one line), exit 2 on unknown
# ids; the SOURCE line is the mechanical proof that the script reads its source
SCRIPT_SRC = (
    "import sys\n"
    "SOURCE = {source!r}\n"
    "VALUES = {values}\n"
    "key = sys.argv[1] if len(sys.argv) > 1 else ''\n"
    "if key not in VALUES:\n"
    "    raise SystemExit(2)\n"
    "print(VALUES[key])\n"
)


def write_csv(root, rows, header=HEADER):
    import csv as _csv
    import io
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(header.split(","))
    for row in rows:
        w.writerow(row)
    (root / "numbers.csv").write_text(buf.getvalue(), encoding="utf-8")


def write_script(root, name, values, source="data/reviews/a.txt"):
    body = SCRIPT_SRC.format(values=repr(values), source=source)
    (root / "scripts" / name).write_text(body, encoding="utf-8")


def make_root(tmp_path):
    (tmp_path / "data" / "recorded").mkdir(parents=True)
    (tmp_path / "data" / "reviews").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    return tmp_path


def run(tmp_path, capsys):
    code = cn.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    return code, out


def test_all_green_exit_0(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text(
        "size 76983 bytes\nnext line 41\n", encoding="utf-8")
    (root / "data" / "recorded" / "b.md").write_text(
        "found 181 notes (2026-09-27)\n", encoding="utf-8")
    write_script(root, "calc.py", {"N003": "42"})
    write_csv(root, [
        ["N001", "76,983", "S.1", "a short quote", "data/reviews/a.txt#L1", "literal", "x"],
        ["N002", "181", "S.1", "a short quote", "data/recorded/b.md#L1", "recorded", "log 2026-09-27"],
        ["N003", "42", "S.2", "a short quote", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
        ["N004", "76983", "S.1", "a short quote", "data/reviews/a.txt#L1", "literal", "x"],
        ["N005", "bytes", "S.1", "a short quote", "data/reviews/a.txt#L1", "literal", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 0
    assert out.count("OK") >= 3
    assert "RESULT: OK" in out


def test_wrong_value_fails(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("size 76983 bytes\n", encoding="utf-8")
    write_csv(root, [["N001", "76,984", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"]])
    code, out = run(root, capsys)
    assert code == 1
    assert "FAIL" in out and "RESULT: FAIL" in out


def test_missing_source_fails(tmp_path, capsys):
    root = make_root(tmp_path)
    write_csv(root, [["N001", "5", "S.1", "q", "data/reviews/missing.txt#L1", "literal", "x"]])
    code, out = run(root, capsys)
    assert code == 1
    assert "source missing" in out


def test_anchor_required_fails(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("5\n", encoding="utf-8")
    write_csv(root, [["N001", "5", "S.1", "q", "data/reviews/a.txt", "literal", "x"]])
    code, out = run(root, capsys)
    assert code == 1
    assert "anchor required" in out


def test_value_must_be_in_the_anchored_line(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("nothing here\nsize 76983\n", encoding="utf-8")
    write_csv(root, [["N001", "76983", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"]])
    code, out = run(root, capsys)
    assert code == 1
    assert "not in place" in out


def test_json_pointer_anchor(tmp_path, capsys):
    import json
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.json").write_text(
        json.dumps({"before": {"bytes": 76983}, "note": "16542 of 200"}),
        encoding="utf-8")
    write_csv(root, [
        ["N001", "76,983", "S.1", "q", "data/reviews/a.json#/before/bytes", "literal", "x"],
        ["N002", "76,984", "S.1", "q", "data/reviews/a.json#/before/bytes", "literal", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "N001  OK" in out
    assert "N002  FAIL" in out


def test_whole_number_tokens_only(tmp_path, capsys):
    """6 must not match 16, 6.5 or the 06 of the date 2026-09-06."""
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text(
        "2026-09-06 v16 6.5\n", encoding="utf-8")
    write_csv(root, [["N001", "6", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"]])
    code, out = run(root, capsys)
    assert code == 1
    assert "FAIL" in out
    (root / "data" / "reviews" / "a.txt").write_text("count 6 done\n", encoding="utf-8")
    code, out = run(root, capsys)
    assert code == 0


def test_sign_belongs_to_the_number_token(tmp_path, capsys):
    """-499 must not pass as 499 (a leading minus is part of the token)."""
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("covered by 499 tests\n", encoding="utf-8")
    write_csv(root, [
        ["N001", "499", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"],
        ["N002", "-499", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"],
        ["N003", "-499", "S.1", "q", "data/reviews/a.json#/n", "literal", "x"],
    ])
    (root / "data" / "reviews" / "a.json").write_text(json.dumps({"n": 499}), encoding="utf-8")
    code, out = run(root, capsys)
    assert code == 1
    assert "N001  OK" in out
    assert "N002  FAIL" in out
    assert "N003  FAIL" in out
    # the range 1-2 keeps two positive tokens: a hyphen between digits is not a sign
    (root / "data" / "reviews" / "a.txt").write_text("review about 1-2 dollars\n", encoding="utf-8")
    write_csv(root, [["N001", "1-2 dollars", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"]])
    code, out = run(root, capsys)
    assert code == 0


def test_sign_matters_for_script_output_too(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_script(root, "calc.py", {"N001": "499", "N002": "499"})
    write_csv(root, [
        ["N001", "499", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
        ["N002", "-499", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "N001  OK" in out
    assert "N002  FAIL" in out


def test_words_of_the_value_must_stand_in_the_line(tmp_path, capsys):
    """A wrong unit word must fail even when the numbers match."""
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("took 40 seconds\n", encoding="utf-8")
    write_csv(root, [
        ["N001", "40 seconds", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"],
        ["N002", "40 minutes", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "N001  OK" in out
    assert "N002  FAIL" in out
    assert "minutes" in out


def test_script_output_must_match_the_value_as_a_whole(tmp_path, capsys):
    """0.06 dollars against the output 0.06 cents is a FAIL; only rounding,
    case, whitespace and thousand separators may differ."""
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_script(root, "calc.py", {
        "N001": "0.06 cents",
        "N002": "0.06 cents",
        "N003": "42.6 %",
        "N004": "3541",
    })
    write_csv(root, [
        ["N001", "0.06 cents", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
        ["N002", "0.06 dollars", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
        ["N003", "43 %", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
        ["N004", "3,541", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "N001  OK" in out
    assert "N002  FAIL" in out  # dollars != cents, numbers alone are not enough
    assert "N003  OK" in out  # rounded to the printed precision of the value
    assert "N004  OK" in out  # thousand separators normalize away


def test_pointer_unit_must_fit_the_pointer(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.json").write_text(json.dumps({
        "wait_minutes": 9,
        "wait": 9,
        "other": {"wait": 9, "unit": "minutes"},
        "plain": 9,
    }), encoding="utf-8")
    write_csv(root, [
        ["N001", "9 minutes", "S.1", "q", "data/reviews/a.json#/wait_minutes", "literal", "x"],
        ["N002", "9 minutes", "S.1", "q", "data/reviews/a.json#/plain", "literal", "x"],
        ["N003", "9 minutes", "S.1", "q", "data/reviews/a.json#/other/wait", "literal", "x"],
        ["N004", "9", "S.1", "q", "data/reviews/a.json#/plain", "literal", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "N001  OK" in out  # unit word in the pointer path
    assert "N002  FAIL" in out  # no unit in the path, no unit field
    assert "N003  OK" in out  # neighbour field "unit" names the unit
    assert "N004  OK" in out  # no unit in the value: nothing to fit
    assert "unit" in out and "fits neither" in out


def test_script_must_read_its_source(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    (root / "data" / "reviews" / "b.txt").write_text("1\n", encoding="utf-8")
    write_script(root, "calc.py", {"N001": "42"})  # names only data/reviews/a.txt
    write_csv(root, [
        ["N001", "42", "S.1", "q", "data/reviews/b.txt", "script:scripts/calc.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "script does not read its source" in out
    # the file the script does name verifies
    write_csv(root, [
        ["N001", "42", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 0


def test_script_source_under_results_needs_a_line_anchor(tmp_path, capsys):
    """results/ sources point at a regenerated row: the anchor is required."""
    root = make_root(tmp_path)
    (root / "results").mkdir()
    (root / "results" / "out.csv").write_text("N001,42\n", encoding="utf-8")
    write_script(root, "calc.py", {"N001": "42"}, source="results/out.csv")
    write_csv(root, [
        ["N001", "42", "S.1", "q", "results/out.csv", "script:scripts/calc.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "line anchor" in out
    write_csv(root, [
        ["N001", "42", "S.1", "q", "results/out.csv#/a", "script:scripts/calc.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "line anchor" in out


def test_script_need_not_name_a_results_source_but_the_line_is_checked(tmp_path, capsys):
    """A results/ source skips the name check (rebuilt by make_results and the
    byte-for-byte test) but the anchored CSV row must carry this id and value."""
    root = make_root(tmp_path)
    (root / "results").mkdir()
    (root / "data" / "reviews").mkdir(parents=True, exist_ok=True)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    (root / "results" / "out.csv").write_text(
        "id,quantity,value_as_in_paper,value_raw,inputs,command\n"
        "N001,q,42,42,data/reviews/a.txt,python3 scripts/calc.py N001\n"
        "N002,q,41,41,data/reviews/a.txt,python3 scripts/calc.py N002\n",
        encoding="utf-8")
    write_script(root, "calc.py", {"N001": "42"})  # names data/reviews/a.txt only
    write_csv(root, [
        ["N001", "42", "S.1", "q", "results/out.csv#L2", "script:scripts/calc.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 0
    assert "N001  OK" in out
    # the anchor points at another row's line
    write_csv(root, [
        ["N001", "42", "S.1", "q", "results/out.csv#L3", "script:scripts/calc.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "row id" in out


HARD_DUMPER_SRC = (
    "import sys\n"
    "print('42' if len(sys.argv) > 1 and sys.argv[1] == 'N001' else '42')\n"
)


def write_results_row(root, line):
    (root / "results" / "out.csv").write_text(
        "id,quantity,value_as_in_paper,value_raw,inputs,command\n" + line,
        encoding="utf-8")


def test_hardcoded_dumper_fails_when_command_names_another_script(tmp_path, capsys):
    """R-N1 counterexample: a script that only prints a hard-coded value and
    reads nothing must FAIL when the results row's command cell runs another
    script than the one method names."""
    root = make_root(tmp_path)
    (root / "results").mkdir()
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_results_row(
        root, "N001,q,42,42,data/reviews/a.txt,python3 scripts/real.py N001\n")
    (root / "scripts" / "dumper.py").write_text(HARD_DUMPER_SRC, encoding="utf-8")
    write_csv(root, [
        ["N001", "42", "S.1", "q", "results/out.csv#L2",
         "script:scripts/dumper.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "command cell runs" in out


def test_hardcoded_dumper_fails_when_it_reads_no_inputs(tmp_path, capsys):
    """Same dumper, command cell agreeing: it still FAILs because none of the
    row's input files appears in the script text."""
    root = make_root(tmp_path)
    (root / "results").mkdir()
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_results_row(
        root, "N001,q,42,42,data/reviews/a.txt,python3 scripts/dumper.py N001\n")
    (root / "scripts" / "dumper.py").write_text(HARD_DUMPER_SRC, encoding="utf-8")
    write_csv(root, [
        ["N001", "42", "S.1", "q", "results/out.csv#L2",
         "script:scripts/dumper.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "does not read its inputs" in out


def test_script_that_names_inputs_and_command_verifies(tmp_path, capsys):
    """Positive control for the hardened results/ checks."""
    root = make_root(tmp_path)
    (root / "results").mkdir()
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_results_row(
        root, "N001,q,42,42,data/reviews/a.txt,python3 scripts/calc.py N001\n")
    write_script(root, "calc.py", {"N001": "42"})  # names data/reviews/a.txt
    write_csv(root, [
        ["N001", "42", "S.1", "q", "results/out.csv#L2",
         "script:scripts/calc.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 0
    assert "N001  OK" in out


def test_line_form_counts_tokens(tmp_path, capsys):
    """The line offers its tokens once each: 2-2 needs two tokens 2."""
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text(
        "review about 1-2 dollars\n", encoding="utf-8")
    write_csv(root, [
        ["N001", "1-2 dollars", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"],
        ["N002", "2-2 dollars", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "N001  OK" in out
    assert "N002  FAIL" in out


def test_script_gets_the_row_id(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_script(root, "calc.py", {"N001": "42", "N003": "42\n43"})
    write_csv(root, [
        ["N001", "42", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
        ["N002", "42", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
        ["N003", "42", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "N001  OK" in out
    assert "N002  FAIL" in out and "script exit 2" in out  # unknown id
    assert "N003  FAIL" in out  # more than one line printed


def test_script_output_mismatch_fails(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_script(root, "calc.py", {"N001": "41"})
    write_csv(root, [["N001", "42", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"]])
    code, out = run(root, capsys)
    assert code == 1
    assert "!= script output" in out


def test_script_nonzero_exit_fails(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    (root / "scripts" / "calc.py").write_text(
        "SOURCE = 'data/reviews/a.txt'\nraise SystemExit(3)\n", encoding="utf-8")
    write_csv(root, [["N001", "42", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"]])
    code, out = run(root, capsys)
    assert code == 1
    assert "script exit" in out


def test_script_numbers_in_order_and_count(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_script(root, "calc.py", {"N001": "31 of 137", "N002": "31 of 137", "N003": "42.6 %"})
    write_csv(root, [
        ["N001", "31 of 137", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
        ["N002", "137 of 31", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
        ["N003", "43 %", "S.1", "q", "data/reviews/a.txt", "script:scripts/calc.py", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "N001  OK" in out
    assert "N002  FAIL" in out  # wrong order
    assert "N003  OK" in out  # rounded to the printed precision


def test_too_many_recorded_fails_even_when_all_rows_ok(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("41\n", encoding="utf-8")
    (root / "data" / "recorded" / "b.md").write_text("9 (2026-09-21)\n", encoding="utf-8")
    (root / "data" / "recorded" / "c.md").write_text("8 (2026-09-21)\n", encoding="utf-8")
    write_csv(root, [
        ["N001", "41", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"],
        ["N002", "9", "S.1", "q", "data/recorded/b.md#L1", "recorded", "log 2026-09-21"],
        ["N003", "8", "S.1", "q", "data/recorded/c.md#L1", "recorded", "log 2026-09-21"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "too many recorded" in out


def test_recorded_needs_date_in_note(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "recorded" / "b.md").write_text("181 notes\n", encoding="utf-8")
    write_csv(root, [["N001", "181", "S.1", "q", "data/recorded/b.md#L1", "recorded", "no date here"]])
    code, out = run(root, capsys)
    assert code == 1
    assert "date" in out


def test_recorded_must_live_under_data_recorded(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("181\n", encoding="utf-8")
    write_csv(root, [["N001", "181", "S.1", "q", "data/reviews/a.txt#L1", "recorded", "log 2026-09-27"]])
    code, out = run(root, capsys)
    assert code == 1
    assert "data/recorded" in out


def test_unknown_method_is_usage_error(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_csv(root, [["N001", "1", "S.1", "q", "data/reviews/a.txt#L1", "count", "x"]])
    code, out = run(root, capsys)
    assert code == 2


def test_bad_header_is_usage_error(tmp_path, capsys):
    root = make_root(tmp_path)
    write_csv(root, [["N001", "1"]], header="id,value")
    code, out = run(root, capsys)
    assert code == 2


def test_duplicate_id_is_usage_error(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_csv(root, [
        ["N001", "1", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"],
        ["N001", "1", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 2


def test_long_paper_quote_fails(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    quote = " ".join(["word"] * 16)
    write_csv(root, [["N001", "1", "S.1", quote, "data/reviews/a.txt#L1", "literal", "x"]])
    code, out = run(root, capsys)
    assert code == 1
    assert "paper_quote" in out


def test_normalization_separators_and_decimal_comma(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text(
        "size 76983\nshare 0,5\ncost 305000\n", encoding="utf-8")
    write_csv(root, [
        ["N001", "76,983", "S.1", "q", "data/reviews/a.txt#L1", "literal", "x"],
        ["N002", "0.5", "S.1", "q", "data/reviews/a.txt#L2", "literal", "x"],
        ["N003", "305,000", "S.1", "q", "data/reviews/a.txt#L3", "literal", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 0


# ---------------------------------------------------------------------------
# paper quotes (optional paper/paper.txt)
# ---------------------------------------------------------------------------

def write_paper(root, text):
    (root / "paper").mkdir(exist_ok=True)
    (root / "paper" / "paper.txt").write_text(text, encoding="utf-8")


def test_paper_quotes_checked_when_paper_present(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("34 relevant sections\n", encoding="utf-8")
    write_paper(root, "In our test it put 34 relevant sections into the top ten.")
    write_csv(root, [
        ["N001", "34", "S.1", "In our test it put 34 relevant sections into the top ten",
         "data/reviews/a.txt#L1", "literal", "x"],
        ["N002", "34", "S.1", "a quote the paper never says", "data/reviews/a.txt#L1", "literal", "x"],
        ["N003", "35", "S.1", "In our test it put 34 relevant sections into the top ten",
         "data/reviews/a.txt#L1", "literal", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "N001  OK" in out
    assert "N002  FAIL" in out and "not found in paper" in out
    assert "N003  FAIL" in out and "not spelled in the quote" in out


def test_quote_number_words_are_mapped(tmp_path, capsys):
    """value 8 may stand in the quote as the word 'eight'."""
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("8\n", encoding="utf-8")
    write_paper(root, "eight in four by the end of that day")
    write_csv(root, [
        ["N001", "8", "S.1", "eight in four by the end of that day",
         "data/reviews/a.txt#L1", "literal", "x"],
        ["N002", "two", "S.1", "eight in four by the end of that day",
         "data/reviews/a.txt#L1", "literal", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 1
    assert "N001  OK" in out  # "eight" spells the value's 8
    assert "N002  FAIL" in out  # the word "two" is not in the quote


def test_quote_normalization_hyphen_breaks_and_typographic_quotes(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("40 seconds\n", encoding="utf-8")
    write_paper(root, "The full run over all 3,541 note pairs took 40 sec-\nonds. “Done,” he said.\n")
    write_csv(root, [
        ["N001", "40 seconds", "S.1", "took 40 seconds", "data/reviews/a.txt#L1", "literal", "x"],
    ])
    code, out = run(root, capsys)
    assert code == 0


def test_without_paper_quotes_are_not_checked(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_csv(root, [["N001", "1", "S.1", "a quote no paper contains",
                      "data/reviews/a.txt#L1", "literal", "x"]])
    code, out = run(root, capsys)
    assert code == 0
    assert "paper text not present, quotes not checked" in out


def test_require_paper_turns_missing_paper_into_fail(tmp_path, capsys):
    root = make_root(tmp_path)
    (root / "data" / "reviews" / "a.txt").write_text("1\n", encoding="utf-8")
    write_csv(root, [["N001", "1", "S.1", "q 1", "data/reviews/a.txt#L1", "literal", "x"]])
    code = cn.main(["--root", str(tmp_path), "--require-paper"])
    out = capsys.readouterr().out
    assert code == 1
    assert "paper text not present" in out
    write_paper(root, "q 1\n")
    code = cn.main(["--root", str(tmp_path), "--require-paper"])
    capsys.readouterr()
    assert code == 0


# ---------------------------------------------------------------------------
# pseudonymize_dedup: one shared mapping over both dedup files
# ---------------------------------------------------------------------------

PSEUDO = load_module("pseudonymize_dedup", ROOT / "scripts" / "numbers" / "pseudonymize_dedup.py")


def test_pseudonymize_same_note_gets_the_same_id_in_both_files():
    pairs = {"alpha||beta": {"keep": 1}, "beta||gamma": {"keep": 1}}
    rows = [[1.51, "beta", "delta", {"keep": 1}], [1.20, "alpha", "delta", {"keep": 1}]]
    mapping = PSEUDO.shared_mapping([pairs, rows])
    out_pairs = PSEUDO.apply_mapping(pairs, mapping)
    out_rows = PSEUDO.apply_mapping(rows, mapping)
    # "beta" and "alpha" appear in both files: one ID per note across files
    pair_slugs = {s for key in out_pairs for s in key.split("||")}
    assert out_rows[0][1] == mapping["beta"]
    assert out_rows[0][1] in pair_slugs
    assert out_rows[1][1] == mapping["alpha"]
    assert out_rows[1][1] in pair_slugs
    # distinct notes keep distinct IDs, sorted order
    assert mapping["alpha"] == "n001"
    assert mapping["beta"] == "n002"
    assert len(set(mapping.values())) == len(mapping)


def test_pseudonymize_shared_mapping_keeps_ids_stable_across_files():
    """The bug R-B3 found: per-file mappings gave the same note two IDs."""
    pairs = {"alpha||beta": {}}
    rows = [[1.0, "beta", "zzz-only-here", {}]]
    mapping = PSEUDO.shared_mapping([pairs, rows])
    shared_pairs = PSEUDO.apply_mapping(pairs, mapping)
    shared_rows = PSEUDO.apply_mapping(rows, mapping)
    beta_id = mapping["beta"]
    # "beta" is in both files and carries the same public ID in both
    assert beta_id in {s for key in shared_pairs for s in key.split("||")}
    assert shared_rows[0][1] == beta_id


def test_dedup_files_are_joinable_on_their_ids():
    """Real data: every note of the unlinked file keeps its ID from the pair file."""
    pairs_path = ROOT / "data" / "dedup-run" / "jev-dublettenlauf-2026-09-21.json"
    rows_path = ROOT / "data" / "dedup-run" / "jev-unverlinkte-paare-2026-09-21.json"
    if not pairs_path.is_file() or not rows_path.is_file():
        pytest.skip("dedup run files not present")
    pairs = json.loads(pairs_path.read_text(encoding="utf-8"))
    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    pair_slugs = {s for key in pairs for s in key.split("||")}
    row_slugs = {r[1] for r in rows} | {r[2] for r in rows}
    assert row_slugs <= pair_slugs  # same private note: same public ID in both


# ---------------------------------------------------------------------------
# mandatory mutation probe on the real numbers.csv
# ---------------------------------------------------------------------------

WORD_UP = {
    "one": "two", "two": "three", "three": "four", "four": "five", "five": "six",
    "six": "seven", "seven": "eight", "eight": "nine", "nine": "ten", "ten": "eleven",
    "eleven": "twelve", "twelve": "thirteen", "thirteen": "fourteen",
    "fourteen": "fifteen", "fifteen": "sixteen", "sixteen": "seventeen",
    "seventeen": "eighteen", "eighteen": "nineteen", "nineteen": "twenty",
    "twenty": "two", "hundred": "thousand", "thousand": "hundred",
}
UNIT_SWAPS = {
    "cents": "dollars", "cent": "dollars", "dollars": "cents", "dollar": "cents",
    "seconds": "minutes", "second": "minutes", "minutes": "seconds", "minute": "seconds",
    "s": "minutes", "hour": "minutes", "hours": "minutes", "%": "cents",
    "bytes": "tokens", "byte": "tokens", "tokens": "bytes", "token": "bytes",
    "notes": "pairs", "note": "pairs", "pairs": "notes", "pair": "notes",
    "tests": "runs", "test": "runs", "runs": "tests", "run": "tests",
    "projects": "notes", "project": "notes", "sources": "notes", "source": "notes",
    "rules": "notes", "rule": "notes", "findings": "notes", "finding": "notes",
    "gaps": "notes", "gap": "notes", "conditions": "notes", "condition": "notes",
    "scenarios": "notes", "scenario": "notes", "cases": "notes", "case": "notes",
    "conversations": "notes", "conversation": "notes", "messages": "notes",
    "message": "notes", "packages": "notes", "package": "notes", "lines": "notes",
    "line": "notes", "places": "notes", "place": "notes", "hits": "notes",
    "hit": "notes",
}


def digit_mutant(value):
    """First integer +1 (the probe's original corruption)."""
    m = re.search(r"\d+", value)
    if not m:
        return None
    return value[: m.start()] + str(int(m.group()) + 1) + value[m.end():]


def sign_mutant(value):
    """Flip the sign of the first number token (a leading minus is a token)."""
    m = re.search(r"-?\d+", value)
    if not m:
        return None
    token = m.group(0)
    flipped = token[1:] if token.startswith("-") else "-" + token
    return value[: m.start()] + flipped + value[m.end():]


def word_mutant(value):
    """Bump the first number word (one -> two …)."""
    for word, up in WORD_UP.items():
        m = re.search(r"\b" + word + r"\b", value, re.IGNORECASE)
        if m:
            return value[: m.start()] + up + value[m.end():]
    return None


def unit_mutant(value):
    """Replace the trailing unit word with a different unit."""
    stripped = value.strip()
    if not stripped:
        return None
    parts = stripped.split()
    last = parts[-1]
    if any(ch.isdigit() for ch in last):
        return None
    if not re.search(r"[^\W\d_]|[%$€]", last):
        return None
    if re.search(r"\b(" + "|".join(WORD_UP) + r")\b", last, re.IGNORECASE):
        return None  # number words are covered by word_mutant
    swapped = UNIT_SWAPS.get(last.lower(), "cents" if last.lower() != "cents" else "dollars")
    if swapped == last.lower():
        return None
    parts[-1] = swapped
    return " ".join(parts)


def source_mutant(row):
    """Swap the source for a different existing file (LICENSE keeps the anchor
    kind: #L1 for lines, the pointer stays for JSON)."""
    src = (row.get("source_file") or "").strip()
    if "#" in src:
        _, anchor = src.split("#", 1)
        if anchor.startswith("L"):
            return "LICENSE#L1"
        return "LICENSE#" + anchor
    return "LICENSE"


def mutants_for(row):
    """Yield (kind, mutated_row) for every applicable mutant kind."""
    value = (row.get("value") or "").strip()
    for kind, fn in (("value+1", digit_mutant), ("sign", sign_mutant),
                     ("unit", unit_mutant), ("word", word_mutant)):
        bad_value = fn(value)
        if bad_value is None or bad_value == value:
            continue
        bad = dict(row)
        bad["value"] = bad_value
        yield kind, bad
    bad = dict(row)
    bad["source_file"] = source_mutant(row)
    if bad["source_file"] != (row.get("source_file") or "").strip():
        yield "source", bad


def test_mutation_probe_on_real_numbers_csv():
    """Pflicht-Negativkontrolle: every corrupted row must FAIL.

    Mutant kinds per row: first integer +1, sign flip of the first number
    token, unit-word swap, source swap and a bumped number word. check_row
    must report FAIL for every mutant — 0 survivors expected. Rows that no
    mutant applies to are skipped with a reason (at most three).
    """
    rows = cn.load_rows(ROOT / "numbers.csv")
    counts, survivors, skipped = {}, [], []
    for row in rows:
        applied = 0
        for kind, bad in mutants_for(row):
            counts[kind] = counts.get(kind, 0) + 1
            applied += 1
            status, detail = cn.check_row(ROOT, bad)
            if status != "FAIL":
                survivors.append((row.get("id"), kind, row.get("value"),
                                  bad.get("value"), bad.get("source_file"), status, detail))
        if not applied:
            skipped.append((row.get("id"), row.get("value")))
    assert len(skipped) <= 3, (
        f"more than 3 rows without any applicable mutant: {skipped}")
    assert survivors == [], (
        f"{len(survivors)} of {sum(counts.values())} mutants survived "
        f"(kinds {counts}, skipped {len(skipped)}: {skipped}): {survivors}")


def test_real_numbers_csv_passes_when_present(tmp_path, capsys):
    """The repository's own numbers.csv must verify."""
    if not (ROOT / "numbers.csv").is_file():
        pytest.skip("numbers.csv not created yet")
    text = (ROOT / "numbers.csv").read_text(encoding="utf-8")
    if len(text.splitlines()) < 3:
        pytest.skip("numbers.csv still empty")
    code = cn.main(["--root", str(ROOT)])
    assert code == 0


def test_quote_frequency_and_fraction_words():
    paper = cn.normalize_quote("grep found it twice, the model only once; it cost half a cent")
    assert cn.check_quote("grep found it twice", "2", paper)[0]
    assert cn.check_quote("the model only once", "1", paper)[0]
    assert cn.check_quote("it cost half a cent", "0.5 cent", paper)[0]
    assert not cn.check_quote("grep found it twice", "3", paper)[0]


def test_row_with_extra_field_is_a_usage_error(tmp_path):
    csv_path = tmp_path / "numbers.csv"
    csv_path.write_text(",".join(cn.HEADER) + "\n"
                        "N001,5,p.1,five,data/x.json#/a,literal,note with, an unquoted comma\n",
                        encoding="utf-8")
    with pytest.raises(cn.UsageError):
        cn.load_rows(csv_path)


def test_real_numbers_csv_has_seven_fields_per_row():
    import csv
    rows = list(csv.reader((ROOT / "numbers.csv").read_text(encoding="utf-8").splitlines()))
    assert all(len(r) == len(cn.HEADER) for r in rows)
