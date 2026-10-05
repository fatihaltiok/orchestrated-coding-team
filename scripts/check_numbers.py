#!/usr/bin/env python3
"""Check every empirical number of the paper against its evidence.

Reads ``numbers.csv`` (columns id,value,paper_section,paper_quote,source_file,
method,note) and verifies each row **at its anchored place**:

* ``literal``      — ``source_file`` carries a place: ``path#L<n>`` (the value
                     must stand in line n) or ``path.json#/json/pointer``
                     (RFC 6901, the value at that location must be equal).
                     Number comparison uses whole number tokens only: ``6``
                     does not match ``16``, ``6.5`` or the ``06`` of the date
                     ``2026-09-06``. A leading minus belongs to its token:
                     ``-499`` is not ``499``, while the hyphen of the range
                     ``1-2`` is a separator. All numbers of the value must
                     occur in the anchored line (line form, each token at most
                     once); the pointer form requires the number tokens of the
                     value and of the location to be equal in order and count.
                     In line form every **word** of the value (``minutes``,
                     ``cents``, ``%``, …) must stand in that line as well. In
                     pointer form a trailing **unit** of the value must fit the
                     pointer: either the unit word appears in the pointer path
                     (``/minutes``) or the object carries a neighbour field
                     ``unit`` naming it — otherwise the row fails. Without a
                     place the row fails ("anchor required").
* ``script:<path>`` — running ``python3 <path> <id>`` in the repository root
                     prints exactly the value of that row (one line) and
                     exits 0; an unknown id exits 2. The script must read the
                     file named in ``source_file``: that path has to appear in
                     the script text ("script does not read its source"). The
                     output must match the value **as a whole** (normalized:
                     case, whitespace, thousand separators, numbers rounded to
                     the precision printed in the paper) — ``0.06 dollars``
                     against the output ``0.06 cents`` is a FAIL.
* ``recorded``     — like ``literal``, but the value is only a dated sentence
                     of the contemporaneous project log; ``source_file`` must
                     lie under ``data/recorded/`` and ``note`` must carry the
                     date of the original note.

If ``paper/paper.txt`` exists, every ``paper_quote`` must occur in it
(normalized: whitespace, hyphen line breaks, typographic quotes) and each
number or number word of the value must stand in the quote. Without the paper
text the quotes are not checked (hint only); ``--require-paper`` turns the
missing paper into a FAIL.

Prints OK/FAIL per row, then the counts per method and the share of rows that
are *not* ``recorded``. Exit 0 only if every row is OK **and** that share is
at least 80 %; exit 1 otherwise; exit 2 on usage errors (bad CSV, unknown
method, source outside the repository).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HEADER = ["id", "value", "paper_section", "paper_quote", "source_file", "method", "note"]
METHODS = {"literal", "recorded"}
RECORDED_PREFIX = "data/recorded/"
MIN_NON_RECORDED_SHARE = 80.0
MAX_QUOTE_WORDS = 15
PAPER_REL = "paper/paper.txt"
PAPER_HINT = "paper text not present, quotes not checked"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# A leading minus belongs to the number token ("-499" is not "499"), but only
# when it is glued to the digits and not preceded by another digit or decimal
# mark — so the range "1-2" keeps two positive tokens while ",1-2" still finds
# its "1".
NUM_RE = re.compile(r"(?<![\d.,])-\d+(?:[.,]\d+)*|\d+(?:[.,]\d+)*")
# Dates and clock times are single tokens, not numbers: "2026-09-06" must not
# offer a "6" and "13:45" no "13" or "45".
MASK_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?)?|\b\d{1,2}:\d{2}(?::\d{2})?\b"
)
LINE_ANCHOR_RE = re.compile(r"L(\d+)$")
# Words of a value: letter runs and the unit symbols %, $, €.
WORD_RE = re.compile(r"[^\W\d_]+|[%$€]", re.UNICODE)
# Number words for the paper-quote comparison (value "two" may stand in the
# quote as "two" or as "2").
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "hundred": 100, "thousand": 1000,
    # frequency and fraction words the paper uses for counted values
    "once": 1, "twice": 2, "half": 0.5,
}
QUOTE_TRANSLATION = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‹": '"', "›": '"',
    "–": "-", "—": "-", "−": "-", "…": "...", " ": " ",
}


class UsageError(Exception):
    pass


def normalize(text: str) -> str:
    out = text.replace("\xa0", " ").replace("–", "-").replace("—", "-")
    out = " ".join(out.split())
    return out.lower()


def parse_number(token: str):
    """Return (value, printed precision) for one numeric token (sign-aware)."""
    sign = 1.0
    if token.startswith("-"):
        sign, token = -1.0, token[1:]
    if "," in token and "." in token:
        # the last separator is the decimal mark
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        head, _, tail = token.rpartition(",")
        if len(tail) == 3 and head.isdigit():
            token = head + tail  # thousand separator
        else:
            token = head + "." + tail
    if "." in token:
        integer, _, frac = token.partition(".")
        return sign * float(integer + "." + frac), len(frac)
    return sign * float(token), 0


def numbers_in(text: str):
    """(value, precision) pairs of a text; dates and times are masked out."""
    return [parse_number(m.group(0)) for m in NUM_RE.finditer(MASK_RE.sub(" ", text))]


def canon_token(token: str) -> str:
    """Canonical form of one number token (sign, thousand separators, mark).

    Tokens compare as whole tokens: "6" is not "06", "16" or "6.5", and
    "305,000" is the same token as "305000"; "-499" is not "499".
    """
    sign = ""
    if token.startswith("-"):
        sign, token = "-", token[1:]
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        head, _, tail = token.rpartition(",")
        if len(tail) == 3 and head.isdigit():
            token = head + tail  # thousand separator
        else:
            token = head + "." + tail
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return sign + token


def number_tokens(text: str):
    """Whole number tokens of a text (dates and times masked out)."""
    return [canon_token(m.group(0)) for m in NUM_RE.finditer(MASK_RE.sub(" ", text))]


def value_words(text: str):
    """Words of a value: letter runs and unit symbols, lowercased."""
    return [w.lower() for w in WORD_RE.findall(text)]


def word_in_place(word: str, low_text: str) -> bool:
    if word in "%$€":
        return word in low_text
    return re.search(r"\b" + re.escape(word) + r"\b", low_text) is not None


def tokens_contain(available, wanted) -> bool:
    """Every wanted token must be available (each token used at most once)."""
    have = Counter(available)
    for token in wanted:
        if have[token] <= 0:
            return False
        have[token] -= 1
    return True


def value_in_text(value: str, text: str) -> tuple:
    """Line-form match: numbers and words of the value in the text.

    A value without numbers must itself stand in the text; in any case every
    word of the value (``minutes``, ``cents``, ``%``, …) must stand there too.
    """
    low = normalize(text)
    wanted = number_tokens(value)
    if wanted:
        if not tokens_contain(number_tokens(text), wanted):
            return False, f"number tokens {wanted} not in place"
    elif normalize(value) not in low:
        return False, f"value {value!r} not at its place"
    for word in value_words(value):
        if not word_in_place(word, low):
            return False, f"word {word!r} of the value not in place"
    return True, ""


def value_equals_text(value: str, text: str) -> tuple:
    """Pointer-form match: the value at the location must be equal."""
    wanted, found = number_tokens(value), number_tokens(text)
    if wanted or found:
        if wanted == found:
            return True, ""
        return False, f"value tokens {wanted} != location tokens {found}"
    if normalize(value) == normalize(text):
        return True, ""
    return False, f"value {value!r} != location {text!r}"


def trailing_unit(value: str):
    """Unit of a value: its trailing word, if the value ends with one."""
    stripped = value.strip()
    if not stripped:
        return None
    last = stripped.split()[-1]
    if any(ch.isdigit() for ch in last):
        return None
    words = value_words(last)
    return last.lower() if words else None


def unit_fits_pointer(unit: str, pointer: str, parent) -> tuple:
    """A unit must appear in the pointer path or in the neighbour field 'unit'."""
    if unit is None:
        return True, ""
    if unit in pointer.lower():
        return True, ""
    if isinstance(parent, dict):
        named = parent.get("unit")
        if isinstance(named, str) and unit in named.lower():
            return True, ""
    return False, f"unit {unit!r} fits neither pointer {pointer!r} nor a 'unit' field"


def split_phrase(text: str):
    """Split into ('text', s) / ('num', token, value, precision) parts."""
    masked = MASK_RE.sub(" ", text)
    parts, pos = [], 0
    for m in NUM_RE.finditer(masked):
        if m.start() > pos:
            parts.append(("text", masked[pos:m.start()]))
        value, prec = parse_number(m.group(0))
        parts.append(("num", m.group(0), value, prec))
        pos = m.end()
    if pos < len(masked):
        parts.append(("text", masked[pos:]))
    return parts


def value_matches_script_output(value: str, output: str) -> bool:
    """Output must match the value as a whole (normalized: case, whitespace,
    thousand separators, rounding to the precision printed in the value)."""
    v_parts, o_parts = split_phrase(value), split_phrase(output)
    if len(v_parts) != len(o_parts):
        return False
    for vp, op in zip(v_parts, o_parts):
        if vp[0] != op[0]:
            return False
        if vp[0] == "text":
            if normalize(vp[1]) != normalize(op[1]):
                return False
        elif round(op[2], vp[3]) != round(vp[2], vp[3]):
            return False
    return True


def normalize_quote(text: str) -> str:
    """Normalize a quote/paper excerpt: quotes, hyphen line breaks, whitespace."""
    out = text
    for src, dst in QUOTE_TRANSLATION.items():
        out = out.replace(src, dst)
    out = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", out)  # hyphen line breaks
    out = out.replace("\n", " ")
    return " ".join(out.split()).lower()


def quote_markers(text: str):
    """Numeric markers of a text: number tokens and number words as values."""
    markers = [parse_number(m.group(0))[0] for m in NUM_RE.finditer(MASK_RE.sub(" ", text))]
    markers += [v for w, v in NUMBER_WORDS.items()
                if re.search(r"\b" + w + r"\b", text.lower())]
    return markers


def check_quote(quote: str, value: str, paper_norm: str) -> tuple:
    """Quote must occur in the paper; value numbers must stand in the quote."""
    if normalize_quote(quote) not in paper_norm:
        return False, "paper_quote not found in paper text"
    for marker in quote_markers(value):
        if not any(marker == m for m in quote_markers(quote)):
            return False, f"value number {marker} not spelled in the quote"
    return True, ""


def load_rows(csv_path: Path):
    try:
        raw = csv_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read {csv_path}: {exc}")
    reader = csv.DictReader(raw.splitlines())
    if reader.fieldnames != HEADER:
        raise UsageError(f"bad header {reader.fieldnames!r}, expected {HEADER}")
    rows = list(reader)
    if not rows:
        raise UsageError("numbers.csv has no rows")
    for row in rows:
        if None in row or any(v is None for v in row.values()):
            raise UsageError(f"{row.get('id')}: wrong number of fields "
                             f"(expected {len(HEADER)}; quote fields that contain commas)")
    return rows


def resolve_source(root: Path, rel: str) -> Path:
    if not rel or rel.startswith("/") or rel.startswith("~") or ".." in Path(rel).parts:
        raise UsageError(f"source_file must be a relative path inside the repository: {rel!r}")
    return root / rel


def split_anchor(source_file: str) -> tuple:
    """Split ``path#L<n>`` / ``path#/pointer`` into (path, kind, spec)."""
    if "#" not in source_file:
        return source_file, None, None
    rel, _, spec = source_file.partition("#")
    line = LINE_ANCHOR_RE.match(spec)
    if line:
        return rel, "line", int(line.group(1))
    if spec.startswith("/"):
        return rel, "pointer", spec
    return rel, "bad", spec


def json_pointer_node(target, pointer: str):
    """Resolve an RFC 6901 pointer; return (node, parent of the node)."""
    node, parent = target, None
    if pointer not in ("", "/"):
        for part in pointer.lstrip("/").split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            parent = node
            if isinstance(node, list):
                node = node[int(part)]
            elif isinstance(node, dict) and part in node:
                node = node[part]
            else:
                raise UsageError(f"pointer {pointer!r} not found")
    return node, parent


def json_pointer_text(target, pointer: str):
    """Resolve an RFC 6901 pointer; return the located value as text."""
    node, _ = json_pointer_node(target, pointer)
    if isinstance(node, str):
        return node
    return json.dumps(node, ensure_ascii=False)


def check_row(root: Path, row: dict, paper_norm: str = None) -> tuple:
    """Return (status, detail). status is 'OK', 'FAIL' or raises UsageError."""
    value = (row.get("value") or "").strip()
    method = (row.get("method") or "").strip()
    source_file = (row.get("source_file") or "").strip()
    note = (row.get("note") or "").strip()
    quote = (row.get("paper_quote") or "").strip()
    if not value:
        raise UsageError(f"{row.get('id')}: empty value")
    if not source_file:
        raise UsageError(f"{row.get('id')}: empty source_file")
    if len(quote.split()) > MAX_QUOTE_WORDS:
        return "FAIL", f"paper_quote longer than {MAX_QUOTE_WORDS} words"
    if paper_norm is not None and quote:
        ok, detail = check_quote(quote, value, paper_norm)
        if not ok:
            return "FAIL", detail

    if method.startswith("script:"):
        rel, kind, _ = split_anchor(source_file)
        script_rel = method[len("script:"):]
        script = resolve_source(root, script_rel)
        if not script.is_file():
            return "FAIL", f"script missing: {script_rel}"
        if not resolve_source(root, rel).is_file():
            return "FAIL", f"source missing: {rel}"
        try:
            script_text = script.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return "FAIL", f"script missing: {exc}"
        if rel not in script_text:
            return "FAIL", f"script does not read its source: {rel}"
        try:
            proc = subprocess.run(
                [sys.executable, str(script), row.get("id") or ""],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "FAIL", f"script did not run: {exc}"
        if proc.returncode != 0:
            return "FAIL", f"script exit {proc.returncode}"
        out = proc.stdout.strip().splitlines()
        if len(out) != 1:
            return "FAIL", f"script must print exactly one line, got {len(out)}"
        if value_matches_script_output(value, out[0]):
            return "OK", f"script:{script_rel} {row.get('id')}"
        return "FAIL", f"value {value!r} != script output {out[0]!r}"

    if method not in METHODS:
        raise UsageError(f"{row.get('id')}: unknown method {method!r}")
    rel, kind, spec = split_anchor(source_file)
    if kind is None:
        return "FAIL", f"{source_file}: anchor required (path#L<n> or path#/pointer)"
    if kind == "bad":
        return "FAIL", f"{source_file}: bad anchor {spec!r}"
    src = resolve_source(root, rel)
    if not src.is_file():
        return "FAIL", f"source missing: {rel}"
    if method == "recorded":
        if not rel.startswith(RECORDED_PREFIX):
            return "FAIL", f"recorded rows must live under {RECORDED_PREFIX}"
        if not DATE_RE.search(note):
            return "FAIL", "recorded rows need the date of the original note in 'note'"

    if kind == "line":
        try:
            lines = src.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            return "FAIL", f"cannot read source: {exc}"
        if not 1 <= spec <= len(lines):
            return "FAIL", f"line {spec} outside {rel} ({len(lines)} lines)"
        ok, detail = value_in_text(value, lines[spec - 1])
        return ("OK", method) if ok else ("FAIL", f"{rel}#L{spec}: {detail}")

    # kind == "pointer"
    try:
        target = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return "FAIL", f"cannot read source as JSON: {exc}"
    try:
        node, parent = json_pointer_node(target, spec)
    except (UsageError, ValueError, IndexError, KeyError) as exc:
        return "FAIL", f"{rel}#{spec}: {exc}"
    ok, detail = unit_fits_pointer(trailing_unit(value), spec, parent)
    if not ok:
        return "FAIL", f"{rel}#{spec}: {detail}"
    located = node if isinstance(node, str) else json.dumps(node, ensure_ascii=False)
    ok, detail = value_equals_text(value, located)
    return ("OK", method) if ok else ("FAIL", f"{rel}#{spec}: {detail}")


def load_paper(root: Path):
    """Normalized paper text, or None when paper/paper.txt is absent."""
    path = root / PAPER_REL
    if not path.is_file():
        return None
    try:
        return normalize_quote(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"cannot read {PAPER_REL}: {exc}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify numbers.csv against its sources.")
    ap.add_argument("--csv", help="path to numbers.csv (default: <root>/numbers.csv)")
    ap.add_argument("--root", help="repository root (default: parent of scripts/)")
    ap.add_argument("--require-paper", action="store_true",
                    help=f"fail when {PAPER_REL} is missing (quotes unchecked otherwise)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    csv_path = Path(args.csv) if args.csv else root / "numbers.csv"
    if not root.is_dir():
        print(f"error: root not found: {root}", file=sys.stderr)
        return 2

    try:
        paper_norm = load_paper(root)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if paper_norm is None and args.require_paper:
        print(f"RESULT: FAIL ({PAPER_HINT}, but --require-paper is set)")
        return 1

    try:
        rows = load_rows(csv_path)
        seen = set()
        results = []
        for row in rows:
            rid = (row.get("id") or "").strip()
            if not rid or rid in seen:
                raise UsageError(f"duplicate or empty id: {rid!r}")
            seen.add(rid)
            status, detail = check_row(root, row, paper_norm)
            results.append((rid, status, detail, (row.get("method") or "").strip()))
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    failed = 0
    for rid, status, detail, method in results:
        print(f"{rid}  {status}  {method}  {detail}")
        if status != "OK":
            failed += 1

    counts = {}
    for row in rows:
        m = (row.get("method") or "").strip()
        key = m.split(":", 1)[0] if m.startswith("script:") else m
        counts[key] = counts.get(key, 0) + 1
    total = len(rows)
    non_recorded = total - counts.get("recorded", 0)
    share = non_recorded * 100.0 / total
    print("---")
    print("methods: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"rows: {total}")
    print(f"non-recorded share: {share:.1f} % (need >= {MIN_NON_RECORDED_SHARE:.0f} %)")
    if paper_norm is None:
        print(f"note: {PAPER_HINT}")

    if failed:
        print(f"RESULT: FAIL ({failed} row(s) not OK)")
        return 1
    if share < MIN_NON_RECORDED_SHARE:
        print("RESULT: FAIL (too many recorded rows)")
        return 1
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
