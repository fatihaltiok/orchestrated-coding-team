"""Tests for scripts/gate4_probe.py (the honest Gate-4 estimate).

The probe must search data/ and results/ without the recorded excerpts, the
claims ledger and numbers.csv; match whole number tokens like check_numbers;
name the loose quote as pure token frequency and check the anchor in the
strict quote (no R40-class false hits); report both quotes plus the 20 PR #42
gaps and the counted "backed by computation in results/" line; and never
pretend to be more than an estimate — no prediction of the review, no 80 %
hurdle claim.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gp = load_module("gate4_probe", ROOT / "scripts" / "gate4_probe.py")
cn = load_module("check_numbers_for_probe", ROOT / "scripts" / "check_numbers.py")


def test_value_hit_uses_whole_tokens_and_bare_numbers():
    assert gp.value_hits(cn, "6", "the count was 6 of 9")
    assert not gp.value_hits(cn, "6", "the count was 16 of 19")
    assert not gp.value_hits(cn, "6", "the date 2026-09-06 only")
    assert gp.value_hits(cn, "3,541", "3541 pairs were checked")
    assert gp.value_hits(cn, "3,541", "3,541 pairs were checked")
    assert gp.value_hits(cn, "98.7 %", "so 98.7 percent — 98.7 is the value")
    assert gp.value_hits(cn, "40 seconds", "40 seconds flat")  # bare number ok
    assert not gp.value_hits(cn, "40 seconds", "41 seconds flat")
    assert gp.value_hits(cn, "two", "two findings")
    assert gp.value_hits(cn, "two", "2 findings")
    assert not gp.value_hits(cn, "two", "three findings")
    assert gp.value_hits(cn, "less than an hour", "took less than an hour today")
    assert not gp.value_hits(cn, "less than an hour", "took an hour today")


def test_excluded_files_are_not_searched(tmp_path):
    (tmp_path / "data" / "recorded").mkdir(parents=True)
    (tmp_path / "data" / "reviews").mkdir(parents=True)
    (tmp_path / "results").mkdir()
    (tmp_path / "data" / "recorded" / "log.md").write_text("181 notes\n", encoding="utf-8")
    (tmp_path / "data" / "reviews" / "claims-ledger.csv").write_text("181 notes\n", encoding="utf-8")
    (tmp_path / "data" / "reviews" / "real.json").write_text('{"n": 5}\n', encoding="utf-8")
    (tmp_path / "results" / "r.csv").write_text("id,n\nN1,5\n", encoding="utf-8")
    (tmp_path / "results" / "README.md").write_text("5 examples\n", encoding="utf-8")
    (tmp_path / "numbers.csv").write_text("181 notes\n", encoding="utf-8")
    rels = gp.searched_files(tmp_path)
    assert "data/reviews/real.json" in rels
    assert "results/r.csv" in rels
    assert "data/recorded/log.md" not in rels
    assert "data/reviews/claims-ledger.csv" not in rels
    assert "results/README.md" not in rels
    assert "numbers.csv" not in rels
    texts = gp.load_texts(tmp_path, rels)
    # the excluded files must not make a hit on their own
    found, missing = gp.evaluate(cn, [
        {"id": "N001", "value": "181", "method": "recorded",
         "source_file": "data/recorded/log.md#L1"},
        {"id": "N002", "value": "5", "method": "literal",
         "source_file": "data/reviews/real.json#/n"},
    ], tmp_path, texts, strict=False)
    assert found == ["N002"]
    assert [r["id"] for r in missing] == ["N001"]


def test_strict_variant_checks_the_anchor_not_the_file(tmp_path):
    """strict = value at the anchored place of source_file (line or pointer)."""
    (tmp_path / "data").mkdir()
    (tmp_path / "results").mkdir()
    (tmp_path / "data" / "a.json").write_text('{"n": 5}\n', encoding="utf-8")
    (tmp_path / "results" / "r.csv").write_text("id,n\nN1,7\n", encoding="utf-8")
    rels = gp.searched_files(tmp_path)
    texts = gp.load_texts(tmp_path, rels)
    rows = [
        {"id": "N001", "value": "7", "method": "script:scripts/make_results.py",
         "source_file": "results/r.csv#L2"},
        {"id": "N002", "value": "7", "method": "literal",
         "source_file": "data/a.json#/n"},
    ]
    # loose: 7 stands in results/r.csv, both rows hit
    found, _ = gp.evaluate(cn, rows, tmp_path, texts, strict=False)
    assert sorted(found) == ["N001", "N002"]
    # strict: N002's anchor /n is 5, the 7 elsewhere must not count
    found, missing = gp.evaluate(cn, rows, tmp_path, texts, strict=True)
    assert found == ["N001"]
    assert [r["id"] for r in missing] == ["N002"]


def test_strict_variant_kills_the_r40_false_hit(tmp_path):
    """The N043 trap: the value stands in the source file, but only in an
    unrelated place (an id cell like R40). Loose may hit (token frequency);
    strict must not."""
    (tmp_path / "data").mkdir()
    (tmp_path / "results").mkdir()
    (tmp_path / "data" / "u0-matrix.csv").write_text(
        "id,status\nR40,fehlt\nR41,ok\n", encoding="utf-8")
    rels = gp.searched_files(tmp_path)
    texts = gp.load_texts(tmp_path, rels)
    row = {"id": "N043", "value": "40", "method": "recorded",
           "source_file": "data/u0-matrix.csv#L3"}  # line 3 = R41,ok — no 40
    found, missing = gp.evaluate(cn, [row], tmp_path, texts, strict=False)
    assert found == ["N043"]  # pure token frequency: the 40 of R40
    found, missing = gp.evaluate(cn, [row], tmp_path, texts, strict=True)
    assert found == [] and [r["id"] for r in missing] == ["N043"]


def test_strict_variant_misses_rows_without_an_anchor(tmp_path):
    """No anchor -> no place to check -> no strict hit (documented in output)."""
    (tmp_path / "data").mkdir()
    (tmp_path / "results").mkdir()
    (tmp_path / "data" / "a.json").write_text('{"n": 5}\n', encoding="utf-8")
    rels = gp.searched_files(tmp_path)
    texts = gp.load_texts(tmp_path, rels)
    row = {"id": "N070", "value": "5", "method": "script:scripts/x.py",
           "source_file": "data/a.json"}
    found, _ = gp.evaluate(cn, [row], tmp_path, texts, strict=False)
    assert found == ["N070"]
    found, missing = gp.evaluate(cn, [row], tmp_path, texts, strict=True)
    assert found == [] and [r["id"] for r in missing] == ["N070"]


def test_gutachten_list_has_exactly_the_20_verdict_gaps():
    assert len(gp.GUTACHTEN_FEHLSTELLEN) == 20
    assert len({rid for _, rid in gp.GUTACHTEN_FEHLSTELLEN}) == 19  # N005 twice
    assert gp.GUTACHTEN_FEHLSTELLEN[0][1] == "N002"
    assert gp.GUTACHTEN_FEHLSTELLEN[19][1] == "N065"


def test_probe_on_the_real_repo_reports_both_quotes(capsys):
    code = gp.main(["--root", str(ROOT), "--csv", str(ROOT / "numbers.csv")])
    out = capsys.readouterr().out
    assert code == 0
    assert "estimate" in out.lower()
    assert "weak" in out.lower()
    # the loose quote is named for what it is: pure token frequency
    assert "Loose variant" in out and "pure token frequency" in out
    assert "Strict variant" in out and "anchored place" in out
    with (ROOT / "numbers.csv").open(encoding="utf-8") as f:
        n_rows = sum(1 for _ in f) - 1  # header line off
    assert "found:" in out and f"/{n_rows} (" in out
    assert "20 claims" in out
    assert "-> " in out and "/20 of the verdict's gaps found (loose)" in out
    assert "/20 of the verdict's gaps found (strict)" in out
    # the 20-gap pass names every claim with its numbers.csv id and value
    assert "[N002 = 209]" in out
    assert "[N065 = 6]" in out
    # no quote is sold as a prediction or as passing any 80% hurdle
    low = out.lower()
    assert "neither quote" in low and "prediction" in low
    assert "80%" in out and "hurdle" in out
    assert "obere schranke" not in low and "untere schranke" not in low
    assert low.count("upper bound") == low.count("not an upper bound")
    assert low.count("lower bound") == low.count("not a lower bound")
    # the counted line (n computed from the rows, not typed in)
    import csv as _csv
    rows = {r["id"]: r for r in _csv.DictReader(
        (ROOT / "numbers.csv").open(encoding="utf-8"))}
    backed = sum(
        1 for _, rid in gp.GUTACHTEN_FEHLSTELLEN
        if (rows[rid]["method"] or "").startswith("script:")
        and (rows[rid]["source_file"] or "").startswith("results/"))
    assert f"von den 20 Beanstandungen des Gutachtens jetzt durch Rechnung in `results/` belegt: {backed}" in out
