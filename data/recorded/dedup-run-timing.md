# Duplicate-run timing and repeatability (recorded excerpts)

Source: project log of the tools repository, 21 Sept 2026, section on the full
duplicate run. Dated excerpts, cleaned:

> Full run on 21 Sept (the human had argued that the full run with Jev is cheap
> anyway — he was right): all 3541 pairs with at least one shared word in
> 40 seconds, 5.3 m tokens. Result: 94 % "keep apart", 6 % (213) "show a
> human", zero "merge". [...] The memory contains not a single duplicate.

> Reproducibility, measured in passing: the 200 pairs of the pre-run were rated
> again in the full run — 98 % same level, mean deviation 0.016.

The paper's "took 40 seconds" and "The judgment was 98 % repeatable" come from
these sentences. The repeat-run raw data (the pre-run ratings) were not kept in
the repository; only the numbers above were logged. The pair data of the full
run itself is in `data/dedup-run/`.

Same log entry on the repeat comparison (paper: "one pair that looked like a
duplicate the first time flipped on the second run"):

> the one duplicate find of the pre-run flipped in the repeat run (1.51 to
> 1.38, from "merge" to "show a human")

That single counter-example is the paper's flip sentence; near a border the
judgment itself says to read the pair yourself. The pre-run ratings were not
kept, so the flip stands here as a dated log sentence only.
