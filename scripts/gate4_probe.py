#!/usr/bin/env python3
"""Estimate how many of the paper's numbers stand literally in data files.

Monperrus' Gate-4 checker runs no scripts: a number only counts for it when the
value stands **literally** in a data or results file. This probe simulates that
rule against ``numbers.csv`` — and says plainly that it is an **estimate**:

* a bare hit is weak evidence (a ``5`` stands in almost every file), and
* the real checker's matching is its own, unpublished judgment (it is an LLM,
  its 2026-10-05 run counted 40/60 and missed values that do stand in JSON
  fields). Nothing here is beautified to reach a quota.

For every ``numbers.csv`` row it asks: does the value (as printed in the paper,
or as a bare number — tokenized like ``scripts/check_numbers.py``, whole number
tokens, dates and times masked) stand literally in a file under ``data/`` or
``results/``? The search looks at each file as a whole and — so that adjacent
CSV cells like ``34,34`` do not glue into one number token — also at each line
and each CSV cell on its own.

Excluded from the search (like the real checker): ``data/recorded/`` (dated log
exits, no raw data), ``data/reviews/claims-ledger.csv`` (the claims ledger) and
``numbers.csv`` itself. ``results/README.md`` is documentation, not data.

Two variants, both reported without a target and without any claim about the
external review's verdict or the paper's 80 % hurdle — that judgment is the
review program's own:

* **loose** — pure token frequency: the value anywhere in any searched file.
  A hit here is a frequency statement and nothing more (a ``40`` also matches
  the ``R40`` of a rule id); it is **not** an upper bound for anything.
* **strict** — the value must stand at the **anchored place** ``source_file``
  names: in line ``#L<n>`` or at the JSON pointer ``#/…``. A value that only
  stands elsewhere in the same file (the ``R40`` trap) does not count. The
  file must lie in the searched set; a source outside ``data/`` and
  ``results/`` never counts here, and a row that names no anchor cannot be
  place-checked and misses this quote by definition. This is **not** a lower
  bound either.

A second pass reports only the 20 claims the PR #42 verdict marked
"not found", plus one counted line: how many of those 20 objections are now
backed by a computation in ``results/``. Exit 0 always (this probe reports
quotes; it gates nothing); exit 2 on usage errors. Standard library only.

Usage: ``python3 scripts/gate4_probe.py [--root DIR] [--csv PATH]``
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_PREFIXES = ("data/recorded/",)
EXCLUDE_FILES = ("data/reviews/claims-ledger.csv", "numbers.csv", "results/README.md")
SEARCH_ROOTS = ("data", "results")

# The 20 claims the automated PR #42 verdict (2026-10-05) marked "not found",
# in its table order; N005 appears twice because the verdict lists the same
# figure twice (summary and measurement section). The sentences are the
# verdict's wording of paper 1.2 and stay as it wrote them; paper 1.3 differs
# for two of them: N002 is recounted as 209 notes, and N019's three leaks are
# two of a key and one of a file path (N102/N103). The probe looks up the ids'
# current values in numbers.csv, not the numbers in these sentences.
GUTACHTEN_FEHLSTELLEN = [
    ("The shared team memory contained 181 notes.", "N002"),
    ("Keyword search placed 20 relevant sections in the top ten (initial).", "N005"),
    ("Devin completed six runs across three packages (initial period).", "N007"),
    ("A second review of package U3 found three issues.", "N017"),
    ("The review of package U2 found six issues.", "N018"),
    ("Three U2 findings involved leaks of a secret key.", "N019"),
    ("Five links between notes led nowhere (first memory check).", "N090"),
    ("The grep-vs-semantic comparison used three search questions.", "N037"),
    ("The full Jev run evaluated 3,541 note pairs.", "N042"),
    ("The full Jev run over all note pairs took 40 seconds.", "N043"),
    ("The pre-rollout Jev measurement used five real tasks.", "N046"),
    ("Keyword search placed five relevant sections in the top five.", "N051"),
    ("Keyword search placed 20 relevant sections in the top ten (five tasks).", "N005"),
    ("Jev gave the same judgment on 98.7% of repeat runs.", "N054"),
    ("Jev assigned a high score to 55 irrelevant sections.", "N057"),
    ("The initial memory-checker warning flagged 63 of 154 notes (63).", "N060"),
    ("The memory contained 154 notes (154).", "N061"),
    ("A sharper word list reduced the flagged-note count to 16.", "N062"),
    ("A context-package rule change put 21 decisions in the wrong category.", "N063"),
    ("The working-copy mode review identified six distinct gaps.", "N065"),
]


def _load_check_numbers():
    spec = importlib.util.spec_from_file_location(
        "check_numbers", ROOT / "scripts" / "check_numbers.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_numbers"] = mod
    spec.loader.exec_module(mod)
    return mod


def searched_files(root: Path):
    """Files under data/ and results/ the probe searches (relative paths)."""
    out = []
    for top in SEARCH_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel in EXCLUDE_FILES or rel.startswith(EXCLUDE_PREFIXES):
                continue
            out.append(rel)
    return out


def file_blob(text: str) -> str:
    """Search form of a file: the text, its lines and its CSV cells, joined.

    Cell-wise segments keep adjacent CSV cells (``34,34``) from gluing into one
    number token — the value stands literally in its cell.
    """
    parts = [text]
    for line in text.splitlines():
        parts.append(line)
        if "," in line:
            try:
                cells = next(csv.reader([line]))
            except (csv.Error, StopIteration):
                continue
            if len(cells) > 1:
                parts.extend(cells)
    return "\n".join(parts)


def value_hits(cn, value: str, text: str) -> bool:
    """Does the value stand literally in the text (paper spelling or bare)?

    Number tokens must all occur as whole tokens (like check_numbers);
    a value without numbers must occur as its normalized phrase, or its
    number word must occur as that word or as the digit.
    """
    low = cn.normalize(text)
    wanted = cn.number_tokens(value)
    if wanted:
        return cn.tokens_contain(cn.number_tokens(text), wanted)
    if cn.normalize(value) in low:
        return True
    for word, num in cn.NUMBER_WORDS.items():
        if re.search(r"\b" + word + r"\b", cn.normalize(value)):
            if re.search(r"\b" + word + r"\b", low):
                return True
            if cn.tokens_contain(cn.number_tokens(text), [str(num)]):
                return True
    return False


def anchor_hits(cn, root: Path, value: str, source_file: str, texts: dict) -> bool:
    """Strict hit: the value stands at the anchored place of ``source_file``.

    ``path#L<n>`` — in line n of the file; ``path.json#/pointer`` — at the
    pointer location. A hit elsewhere in the same file does not count, and a
    file outside the searched set never counts.
    """
    rel, kind, spec = cn.split_anchor(source_file)
    if rel not in texts or kind not in ("line", "pointer"):
        return False
    try:
        raw = (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if kind == "line":
        lines = raw.splitlines()
        if not 1 <= spec <= len(lines):
            return False
        return value_hits(cn, value, file_blob(lines[spec - 1]))
    try:
        located = cn.json_pointer_text(json.loads(raw), spec)
    except (cn.UsageError, ValueError, TypeError, KeyError, IndexError,
            json.JSONDecodeError):
        return False
    return value_hits(cn, value, located)


def evaluate(cn, rows, root: Path, texts: dict, strict: bool):
    """Return (found_ids, missing_rows).

    loose: pure token frequency over every searched file; strict: only a hit
    at the anchored place of the row's own ``source_file`` counts.
    """
    found, missing = [], []
    for row in rows:
        value = (row.get("value") or "").strip()
        rid = (row.get("id") or "").strip()
        hit = False
        if strict:
            hit = anchor_hits(cn, root, value,
                              (row.get("source_file") or "").strip(), texts)
        else:
            for rel, text in texts.items():
                if value_hits(cn, value, text):
                    hit = True
                    break
        (found if hit else missing).append(row if not hit else rid)
    return found, missing


def load_texts(root: Path, rels):
    """Search texts per file (file_blob: whole text, lines and CSV cells)."""
    texts = {}
    for rel in rels:
        try:
            texts[rel] = file_blob((root / rel).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return texts


def report_variant(title: str, found_count: int, total: int, missing) -> None:
    share = found_count * 100.0 / total if total else 0.0
    print(f"== {title} ==")
    print(f"found: {found_count}/{total} ({share:.1f} %)")
    print(f"not found: {len(missing)} (id, value, method)")
    for row in missing:
        print(f"  {row['id']}  {row['value']}  {row['method']}")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Estimate the share of paper numbers standing literally in data files.")
    ap.add_argument("--root", help="repository root (default: parent of scripts/)")
    ap.add_argument("--csv", help="path to numbers.csv (default: <root>/numbers.csv)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else ROOT
    csv_path = Path(args.csv) if args.csv else root / "numbers.csv"
    cn = _load_check_numbers()
    try:
        rows = cn.load_rows(csv_path)
    except cn.UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    rels = searched_files(root)
    texts = load_texts(root, rels)
    total = len(rows)
    by_id = {r["id"]: r for r in rows}

    print("Gate4 probe — estimate only, no target is asserted.")
    print("A bare hit is weak evidence: a 5 stands in almost every file, and the")
    print("real checker's matching is its own unpublished LLM judgment (its")
    print("2026-10-05 run counted 40/60 and missed values standing in JSON fields).")
    print("Neither quote below is a prediction of that review's verdict, and")
    print("neither shows any 80% hurdle passed — that judgment is the review's")
    print("own (it grades 60 paper claims, not these rows).")
    print(f"Searched: {SEARCH_ROOTS[0]}/ and {SEARCH_ROOTS[1]}/ "
          f"without {', '.join(EXCLUDE_PREFIXES + EXCLUDE_FILES)} "
          f"({len(rels)} files).")
    print("Value match: paper spelling or bare number, whole number tokens,")
    print("normalized like scripts/check_numbers.py.")
    print()

    loose_found, loose_missing = evaluate(cn, rows, root, texts, strict=False)
    report_variant("Loose variant (pure token frequency — value anywhere in "
                   "a searched file)",
                   len(loose_found), total, loose_missing)

    print("== Second pass: the 20 claims the PR #42 verdict marked 'not found' ==")
    for strict, (label, found_ids, missing_rows) in (
            (False, ("loose", *evaluate(cn, rows, root, texts, strict=False))),
            (True, ("strict", *evaluate(cn, rows, root, texts, strict=True)))):
        found_set = set(found_ids)
        hit = 0
        print(f"-- {label} --")
        for claim, rid in GUTACHTEN_FEHLSTELLEN:
            row = by_id.get(rid)
            ok = rid in found_set
            hit += 1 if ok else 0
            mark = "found    " if ok else "not found"
            value = row["value"] if row else "?"
            print(f"  {mark}  {claim}  [{rid} = {value}]")
        print(f"  -> {hit}/20 of the verdict's gaps found ({label})")
        print()

    # counted, not typed in: an objection counts as backed only when its row is
    # computed into results/ (method script:, source under results/)
    backed = sum(
        1 for _, rid in GUTACHTEN_FEHLSTELLEN
        if (by_id.get(rid, {}).get("method") or "").startswith("script:")
        and (by_id.get(rid, {}).get("source_file") or "").startswith("results/"))
    print("von den 20 Beanstandungen des Gutachtens jetzt durch Rechnung in "
          f"`results/` belegt: {backed}")
    print()

    strict_found, strict_missing = evaluate(cn, rows, root, texts, strict=True)
    report_variant("Strict variant (value at the anchored place named by "
                   "source_file)",
                   len(strict_found), total, strict_missing)
    anchorless = [r["id"] for r in rows if "#" not in (r.get("source_file") or "")]
    if anchorless:
        print(f"note: {len(anchorless)} row(s) name no #L/… anchor "
              f"({', '.join(anchorless)}) — their place cannot be checked, "
              f"so they miss the strict quote by definition.")
        print()
    print("Both quotes are reported as measured. The loose quote is pure token")
    print("frequency (not an upper bound), the strict quote checks only the")
    print("anchored place (not a lower bound). Nothing was reworded to make")
    print("the probe find a number it does not compute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
