# Data — evidence for the numbers in the paper

Every empirical number of the paper *The Orchestrated Coding Team* is listed in
`numbers.csv` together with the file it comes from. This folder holds those
files. `python3 scripts/check_numbers.py` verifies every row (exit 0 = all
numbers check out and at least 80 % of them are backed by data or a script,
not just by a dated log excerpt). Figures a script recomputes from the files
here are also written down literally in [`results/`](../results/README.md) —
regenerate them with `make results`; the `recorded` numbers are deliberately
not there (see that README).

## Layout

| Folder | What is in it |
|---|---|
| `data/u4-jev/` | Raw data of the judging-model measurement of 27 Sept 2026: task list, blind reference judgments, per-run results, the report and the raw run files. Candidate excerpts and answer sheets are **not** included — the candidates are identified by hash IDs in `referenz.json`, which is enough to recompute every figure. |
| `data/dedup-run/` | The 21 Sept 2026 duplicate run over all note pairs (pseudonymized, see below). |
| `data/reviews/` | Counts and measurement records from the cross-vendor reviews, test gates and small measurements: finding counts per review, the rule-file comparison matrix (classification only), run records, documented tool constants, a claims ledger for values that were only logged as sentences, the literature-search source table. |
| `data/team-channel/` | The 1 Oct 2026 rating of team-channel scenarios (scores, token count), the vendor list price used to recompute the rating cost, and the list of scenarios selected for implementation. |
| `data/memory-snapshot/` | A dated snapshot of the shared memory folder (5 Oct 2026): one entry per note with a neutral id (`m001` …), the note type, size in bytes and last-change date — no names, no content. Made by `python3 scripts/measurements/count_memory_notes.py <memory folder> <out.json>`; the folder itself is private, so this file is the record of the count. It replaces the logged count of 27 Sept, which could not be recounted. |
| `data/recorded/` | Dated excerpts from the contemporaneous project log for the few numbers whose raw data no longer exists (method `recorded` in `numbers.csv`). |

## How the data was cleaned

The raw material lives in the author's private repositories and note folders.
Before copying, every file was cleaned:

* note names of the private memory folder are not published; the duplicate-run
  files replace every note slug with `n001`, `n002`, … (deterministic, sorted
  order over the union of both files, so the same note keeps its ID in both
  files and they can be joined; mapping not stored — regenerate with
  `python3 scripts/numbers/pseudonymize_dedup.py --output data/dedup-run/
  --input <pair file> --input <unlinked file>`)
* names of private projects in judgment texts were generalized ("another
  project", "a client project"); the `grund` (reason) fields in `data/u4-jev/`
  are German — the original language of the measurement, kept untranslated on
  purpose
* private absolute paths, account data and prices of the author never appear;
  the company project is only referred to as a manufacturing quality-inspection
  context
* verbatim excerpts from task, contract or note files were not taken over —
  only counts, measurement values and short dated evidence sentences
* the publication guard `scripts/check_public.py` was run over everything kept
  here (exit 0)

Costs in the data are run costs of the measurements (what the runs actually
consumed), not prices or balances. The one vendor list price per million
tokens used to recompute the rating cost of a run is a data field with date and
origin (`data/team-channel/vendor-list-price.json`), not a constant in
`scripts/numbers/team_channel_scores.py`.

## Reading the numbers

Each `numbers.csv` row names the method:

* `literal` — the value stands at the place named in `source_file`
* `script:<path>` — running the named script with the row id from the
  repository root prints the value
* `recorded` — no raw data is left; the file under `data/recorded/` is a dated
  excerpt from the project log of that day (checked like `literal`)

### Places (`source_file` anchors)

`literal` and `recorded` rows carry their place in `source_file` — without one
the check fails with `anchor required`:

* `path#L<n>` — the value must stand in **line n** of that file. Numbers
  compare as whole number tokens only: `6` does not match `16`, `6.5` or the
  `06` of the date `2026-09-06`; `305,000` and `305000` are the same token.
  All numbers of the value must occur in that line (each token at most once).
  A value without numbers (e.g. `two`) must itself stand in the line.
* `path.json#/json/pointer` (RFC 6901) — the value at that JSON location must
  be equal: the number tokens of the value and of the location must be equal
  in order and count.

Dates (`2026-09-06`) and clock times are masked before tokenizing — they are
single tokens and never offer their digits to a number.

### Script rows

The check runs `python3 <script> <id>` (e.g.
`python3 scripts/numbers/review_counts.py N013`). The script prints **exactly
the value of that row** on one line and exits 0; an unknown id exits 2.
Without an argument each script still prints its overview. The numbers of the
value and of the output must agree in order and count (rounded to the
precision printed in the paper); extra text ("%", "minutes") may differ, extra
numbers may not.

Rows whose wording changed in paper version 1.2 (a date or a short clarification
added, no result changed) carry `paper-1.2` or "added in paper 1.2" in the note.
