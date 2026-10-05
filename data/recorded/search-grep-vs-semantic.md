# Grep vs. semantic search on the real notes (recorded excerpts)

Source: project decision log of the tools repository, entry E-002 of
21 Sept 2026 ("no vector database"), section "Reason". Dated excerpts,
cleaned (the example note name was removed):

> Own measurement on the real 154 notes, three questions, each looking for one
> specific note — right note among the top four hits:
> semantic (all-MiniLM-L6-v2, English) | 0 of 3 | 2 s
> semantic (multilingual-e5-large) | 1 of 3 | 175 s
> `grep` | 2
> of 3 | < 1 s

The paper's "For three search questions, plain grep found the right note in the
top four twice, the best meaning-based model only once" is this table. The
comparison script lived in the session scratchpad and is gone; the log entry
says explicitly that the measurement is not reproducible by a command. Three
questions are a hint, not proof — the entry says so on purpose.
