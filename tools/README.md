# Tools

These tools use German command names, messages, and output fields. Run the examples from the repository root with Python 3; the test suite also requires `pytest` and `git`. The worktree and watchdog integration tests require a working Linux `systemd --user` session. Tests use disposable directories and simulated clients; they do not require agent credentials or network access.

**`abschluss_check.py`** reports uncommitted changes, worktrees, missing remotes, unusual commit trailers and rule drift without fetching. Run `python3 tools/abschluss_check.py .`; use `--ohne-regeln` if you have not configured a rule source. `--alle` reads your local `tools/repos.json` (not distributed).

**`arbeitskopie.py`** supplies the Git worktree and integration operations used by `laufwaechter.py`; it is a library, not a standalone command. Import it with `import arbeitskopie` from `tools/` or use the watchdog commands below.

**`edison.py`** previews literature-agent requests offline by default and writes a sourced answer on explicit live requests. Run `python3 tools/edison.py frage --agent literatur --frage 'Example question'` or `python3 tools/edison.py zaehler`. Live requests additionally need the optional `edison-client` Python package and a valid key.

**`jev.py`** ranks additional context candidates without removing any candidates; offline is the default. Run `python3 tools/jev.py ordne candidates.json` or `python3 tools/jev.py schaetze candidates.json`. The optional `--live` mode contacts the configured provider.

**`kontextpaket.py`** builds, validates, and audits a project's context package. Run `python3 tools/kontextpaket.py bau PROJECT --auftrag 'Task'`, `python3 tools/kontextpaket.py pruefe package.md`, or `python3 tools/kontextpaket.py protokoll PROJECT`. `kandidaten` emits candidate JSON. `--alle` accepts `--repos` for a local repository list.

**`laufwaechter.py`** starts and monitors agent commands in user-level systemd units and optionally isolates Git worktrees. Run `python3 tools/laufwaechter.py start NAME --ordner PROJECT --marke marker --log run.log -- COMMAND`, then `python3 tools/laufwaechter.py warte NAME` and `python3 tools/laufwaechter.py ergebnis NAME`. Integration requires `systemd-run --user` and `systemctl --user`.

**`merkzettel_check.py`** validates the structure and references of a notes directory, without modifying it. Run `python3 tools/merkzettel_check.py NOTES_DIRECTORY`; without an argument it checks `~/.config/orchestrated-team/memory`.

**`regeln.py`** renders, checks, installs and restores rule files for Claude and Codex. Run `python3 tools/regeln.py erzeuge --ziel claude --quelle RULE_DIRECTORY` or `python3 tools/regeln.py pruefe --quelle RULE_DIRECTORY`. A rule source contains `CLAUDE.md` and `ersetzungen-codex.json`. No personal rule source is included; the neutral default is `~/.config/orchestrated-team/regeln`. Installing requires `--ja` and modifies user files, so inspect the destination first.

**`teamkanal.py`** implements a local SQLite-backed team mailbox and a stdio MCP server. Run `python3 tools/teamkanal.py --help` for the CLI; `--db PATH` selects a database. The neutral default is `~/.local/state/orchestrated-team/teamkanal.sqlite3`.

**`teamkanal_start.py`** registers a mailbox participant, creates a temporary MCP session configuration and launches a Claude or Codex session. Run `python3 tools/teamkanal_start.py claude --projekt PROJECT --trocken` to preview the action without launching an agent.

**`team.py`** is a project picker and short launcher for `teamkanal_start.py`. Run `python3 tools/team.py` to list projects and `python3 tools/team.py PROJECT --trocken` to preview one. It searches `~/projects` by default.

`repos.beispiel.json` shows the shape of the local `tools/repos.json` used by the repository-wide commands. It contains only generic paths; do not publish your real repository inventory.

Environment variables: `TEAM_PROJEKTORDNER` is a colon-separated list of project roots (default `~/projects`); `TEAM_EIGENES_STARTPROJEKT` selects a project to launch from its own root. `REGELN_QUELLE` overrides the default rule-source directory and `REGELN_HOME` redirects installed rules and backups to a different home. `KONTEXTPAKET_MERKZETTEL` selects an optional notes directory (or use `--merkzettel` / `--ohne-merkzettel`). `TYPESAFE_API_KEY` is needed only for live Jev requests. `EDISON_API_KEY` is needed only for live Edison requests; alternatively use `--schluesseldatei`, whose neutral default is `~/.config/orchestrated-team/edison.env`. The counter file defaults to `~/.local/state/orchestrated-team/edison-aufrufe.jsonl` and can be changed with `--zaehldatei`. `LAUFWAECHTER_ZUSTAND` and `LAUFWAECHTER_ARBEITSKOPIEN` override state and worktree directories, `LAUFWAECHTER_TAKT` changes the polling interval, and `LAUFWAECHTER_AUTOR_NAME` / `LAUFWAECHTER_AUTOR_MAIL` override the neutral Git identity of the watchdog's own commits.

Run the offline test suite with `make test` (equivalent to `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tools/tests -q -p no:cacheprovider`). No private rules, notes, `repos.json`, network, or real agents are required for the tests.
