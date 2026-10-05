# First run of the memory checker (recorded excerpts)

Source: project log of the tools repository, 21 Sept 2026, section on the
memory-checker measurement table. Dated excerpts, cleaned:

> `PFAD_TOT` | 10 | notes point to files that no longer existed

> As substrings the four original markers hit 63 of 154 notes, with word
> boundaries still 43; the sharpened list hits 16 of 154, and these 16 are
> real archive candidates.

The paper's "ten notes pointing to files that no longer existed" refers to the
10 dead-path findings of that first run (plus 5 dead note links, which the
paper does not name). "63 of 154 notes" and "16 notes instead of 63" are the
archive-warning counts of the same day's note base (154 notes on 21 Sept 2026,
also visible as the distinct note slugs in `data/dedup-run/`).

Same log, 21 Sept 2026, directly below the table:

> Open since then are 15 hard findings (10 dead paths and 5 dead note links).

Same run, same log entry (paper: "Two kinds of false alarm"):

> the real-run against the notes found two false-alarm classes, both caused by
> gaps in the contract text, not by the builder

The two classes were the ASCII ellipsis `...`, which the placeholder list did
not know, and command lines in backticks that counted as dead paths. The
paper's "Two kinds of false alarm" is that count of classes, not a count of
flagged notes.
