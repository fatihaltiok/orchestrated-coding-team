# The Orchestrated Coding Team — companion repository

Code and data behind the paper *The Orchestrated Coding Team: How I build working software with a team of
AI agents without being a developer* (Fatih Altiok, version 1.2, 5 October 2026). The paper is on Zenodo:
[doi:10.5281/zenodo.23086546](https://doi.org/10.5281/zenodo.23086546) always points to its latest version.

The paper describes how one person who is not a developer runs several AI coding agents from different
vendors as a team: one model plans and reviews, others build, and a model from a different vendor always
checks the work. This repository holds the tools that make that work and the data behind every number
the paper reports.

## Check every number with one command

```sh
make check          # = python3 scripts/check_numbers.py --require-paper
```

`numbers.csv` lists every empirical number in the paper, one row each: the value as printed, where it
appears, a short quote, the file it comes from and how it is obtained. `check_numbers.py` confirms each
row against its source and exits 0 only if all rows check out.

Each row points to an exact place: a line in a file (`file#L12`), a field in a JSON file
(`file.json#/path/to/field`), or a script that prints the value for that row (`python3 script.py N013`).
A test changes every value in turn (number, sign, unit, source) and makes sure the check then fails.

Three kinds of evidence, named in the `method` column:

| method | meaning |
|---|---|
| `literal` | the value stands at the named place in a data file |
| `script:<path>` | the script computes the value from the data files and prints it |
| `recorded` | no raw data is left (for example a note folder that was not under version control); the file under `data/recorded/` is a dated excerpt from the project log written on the day |

At least 80 % of the rows must be `literal` or `script`; `make check` fails otherwise. The check also
reads the paper text (`paper/paper.txt`, extracted from `paper/the-orchestrated-coding-team-1.2.pdf`): every
quote in `numbers.csv` must stand there word for word, with its number written out. The column
`paper_section` gives the page in that PDF and the section.

## How the paper maps to this repository

| In the paper | Here |
|---|---|
| run watcher ("Laufwächter"): starts agent runs, notices when they stop | `tools/laufwaechter.py` |
| working copy per agent run | `tools/arbeitskopie.py` |
| context package ("Kontextpaket"): conditions and decisions handed to every agent | `tools/kontextpaket.py` |
| finish check before every hand-over | `tools/abschluss_check.py` |
| memory ("Merkzettel") and its form check | `tools/merkzettel_check.py` |
| Jev, the judging model (TypeSafe) that ranks context | `tools/jev.py`; measurement in `scripts/measurements/`, data in `data/u4-jev/` |
| one rule source for Claude and Codex | `tools/regeln.py` |
| team channel between two lead sessions | `tools/teamkanal.py`, `tools/teamkanal_start.py`, `tools/team.py` |
| literature questions (Edison) | `tools/edison.py` |
| work packages U0–U7 of the upgrade (26–27 Sept 2026) and their reviews | `data/reviews/package-list.json`, `data/reviews/findings-counts.json` |

## What is in here

| Path | Content |
|---|---|
| `paper/` | the paper, version 1.2 (PDF, CC BY-NC-ND 4.0) and its plain text for the quote check |
| `numbers.csv` | every empirical number of the paper and its source |
| `data/` | measurement data, review counts and dated log excerpts — see [`data/README.md`](data/README.md) |
| `scripts/numbers/` | small scripts that compute numbers from the data |
| `scripts/measurements/` | the measurement scripts of the judging-model test (27 Sept 2026); rerunning the measurement needs a TypeSafe API key and network, recomputing the reported numbers from the stored results does not |
| `scripts/check_numbers.py` | the check above |
| `scripts/check_public.py` | the guard that ran before publication: it scans every file for private content |
| `tools/` | the team's tools — see [`tools/README.md`](tools/README.md) |

## Run the tests

```sh
make test           # tools/tests: needs pytest and git; the run-watcher tests start real
                    # `systemd-run --user` units, so they need a Linux systemd user session
make test-scripts   # scripts/tests: needs pytest only
```

`make public-check` is the publication guard. It needs a private deny-list and the private note folder,
which are not part of this repository, so it cannot be rerun from outside; it ran with exit 0 before
publishing, and its own tests (`scripts/tests/test_check_public.py`) show what it catches.

## Words you will meet

The tools and parts of the data are German, because that is the language the author works in.

| Word | Meaning |
|---|---|
| Orchestrator | the model (Claude) that plans, delegates and reviews; it does not write large code itself |
| Laufwächter | run watcher |
| Arbeitskopie | working copy (a Git worktree per agent run) |
| Kontextpaket | context package |
| Merkzettel | the team's memory: an index plus one note per lesson |
| Jev | a judging model by TypeSafe, used to rank context material |
| U0 … U7 | the eight work packages of the upgrade on 26–27 Sept 2026 |
| Gate | a check that must be green before work is accepted |
| Befund | finding |

Personal paths, accounts and notes are not part of this repository. Names of private projects are
generalized, and names of private notes are replaced by `n001`, `n002`, ….

## License

Code (`tools/`, `scripts/`): MIT, see [`LICENSE`](LICENSE). Data (`data/`, `numbers.csv`): CC BY 4.0, see
[`LICENSE-data`](LICENSE-data). The paper itself is published on Zenodo under CC BY-NC-ND 4.0.

Large parts of the tools and of this repository were written by AI agents under the author's direction,
as the paper describes; the author is responsible for all content.
