# Report extracts — evidence lines for the review and test counts

Cleaned extracts from the project's internal review reports (kept in the private
team repository). Only the lines that carry a number of the paper are reproduced;
names of private projects and private paths were removed or generalized. The
reports themselves are not published.

## Memory checker, 21 Sept 2026 (paper: "117 of 117")

From the memory-checker build report (G1):

> before 88 passed, after 117 passed (88 + 29), Exit 0, all green.

Second gate run after the rollback probes: 117 passed, Exit 0.

## Working-copy mode, 19 Sept 2026 (paper: "67 of 67", "six gaps")

From the working-copy build report (P4c), gate run G9:

> 67 passed in 13.88s

Findings behind the "six gaps" figure (project log of the tools project, entry
of 19 Sept 2026):

> Second opinion (Muse) found 3 gaps, the orchestrator's own review found 4
> (among them: a symlink to a virtual-environment folder does not satisfy the
> ignore pattern of a folder, so the agent could write into the main project) —
> all closed in the follow-up contract section, each with a test and a
> counter-test.

One of the four overlaps with a finding of the outside review (the symlink
gap), so the two reviews reported seven findings for six distinct gaps.

The three findings of that outside review (second opinion on the working-copy
mode, 19 Sept 2026), listed one by one (cleaned; the report itself is not
published):

> Finding 1 (critical): after the gates nothing is checked again — a gate could
> commit or change the working tree and the result is taken over regardless.
> Finding 2 (critical): any `--basis` value brings foreign history into the main
> repository unseen.
> Finding 3 (medium): `--verlinke` symlinks let the agent write straight into
> the main repository — invisible to diff, gates and the clean check.

3 findings, two of them critical. Finding 3 is the symlink gap that the
orchestrator's own review found as well.

## Package U7-1b, 27 Sept 2026 (paper: "all 499 tests passed")

From the U7-1b fix report, gate output:

> 499 passed in 32.81s

This was the state of the tool suite on 27 Sept 2026 (commit `2ca90d4`).
On 1 Oct 2026, after the team-channel packages, the suite reached 752 passed;
the last measurement in the private repository on 1 Oct showed 798 passed.

## U7 review (paper: "2, both real")

From the U7 review report (Muse): B1 (medium) and B2 (small) were confirmed as
real findings and reproduced by the orchestrator. The report lists a third item
B3 (small); the project log of 27 Sept 2026 keeps it as a memo item only:

> B3 (conftest/subprocess) only a memo item.

So the paper's "2, both real" means two confirmed findings, with one item kept
as a memo.

## Team-channel reviews (paper: "each found two gaps … three of the four fixed")

Review of the channel part (MiMo Pro, report R12): findings B1 (no resume path
for a question answer) and B2 (questions longer than about 15,870 characters
cannot be answered) — two gaps, none blocking.

Review of the run-watcher part (Devin, report R13): gaps B1 (a re-run under the
same name can silently skip the done message) and B2 (the log line says "sent"
even when the queue delivery failed) — two gaps. Two further entries B3/B4 are
explicitly remarks without a defect.

Follow-up record: B1 of R12 was documented as a deliberate boundary, the other
three gaps were fixed — three of four.

Machine-readable listing of the four gaps (each line only repeats what the
paragraphs above say — nothing added):

> Gap R12-B1: no resume path for a question answer — status: open (deliberate boundary)
> Gap R12-B2: questions longer than about 15,870 characters cannot be answered — status: fixed
> Gap R13-B1: a re-run under the same name can silently skip the done message — status: fixed
> Gap R13-B2: the log line says "sent" even when the queue delivery failed — status: fixed

## U2 Jev-module review, 26 Sept 2026 (paper: "6, including 3 leaks")

From the Jev-module review report (second opinion on the Jev module, 26 Sept
2026), the six findings listed one by one — number, severity (blocking /
important / small) and the finding's heading only, no content beyond the
headings; headings translated from German (the report itself is not published).
"leak" marks exactly those findings where the report itself describes data
escaping to the outside:

> Finding 1 (blocking; leak): key from the server response escapes externally via the `modell` field
> Finding 2 (blocking; leak): an unrequested answer key is output unmasked in `ungueltig`
> Finding 3 (blocking; leak): an absolute path in `quelle` is passed to the transport
> Finding 4 (important): invalid score-probability keys count as evaluated
> Finding 5 (important): the test file's network lock does not protect its subprocesses
> Finding 6 (small): the test of §12.8 point 3 only checks its own strings

## U3 small-parts review, 27 Sept 2026 (paper: "3")

From the small-parts review report (second U3 review, 27 Sept 2026), the three
findings listed one by one — number, severity and the finding's heading only,
no content beyond the headings; headings translated from German (the report
itself is not published). None of the three describes data escaping to the
outside, so none is marked leak:

> Finding R4-1 (small): the TEILWEISE_REVIDIERT message takes over the whole header-line remainder unshortened
> Finding R4-2 (small): control characters from the header line end up raw in the message
> Finding R4-3 (small): `jev ordne -` rejects UTF-8 with a BOM

## Second literature search, 1 Oct 2026 (paper: "Two hits we couldn't find anywhere")

From the evaluation of the literature-agent answers (Edison run ED1), section
"Not found (do not cite)":

> No. 5 Pacini, *Orchestrating AI for Secure Software Delivery* (Edison:
> "Unknown journal, Unknown year"); No. 11 Villani & Kellogg, *Command Coding*
> (Edison: "Unknown journal, 2026"). Searched via web search, arXiv, Zenodo,
> Crossref.

Two of the hits could not be found anywhere and are left out of the paper.
The other listed hits were each looked up on their original page (12 of 14
confirmed as cited; see the paper's sources).
