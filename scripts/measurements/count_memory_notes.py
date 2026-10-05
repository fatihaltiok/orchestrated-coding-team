#!/usr/bin/env python3
"""Snapshot of the shared memory folder without names or content.

The memory folder is private and not version-controlled, so the paper's note
count cannot be recomputed by others from the notes themselves. This script
records a dated snapshot that can be published: one entry per note with a
neutral id, the note type from its front matter, its size in bytes and the
date it was last changed. File names and note text are never written out.

Usage:
    python3 scripts/measurements/count_memory_notes.py MEMORY_DIR OUT_JSON

The index file MEMORY.md is not a note and is not counted.
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

INDEX = "MEMORY.md"
TYPE_LINE = re.compile(r"^\s*type:\s*(\w+)\s*$")


def note_type(text: str) -> str:
    """Type from the front matter (between the first two '---' lines)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "unknown"
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = TYPE_LINE.match(line)
        if m:
            return m.group(1)
    return "unknown"


def snapshot(memory_dir: Path, today: str) -> dict:
    files = sorted(p for p in memory_dir.glob("*.md") if p.name != INDEX)
    notes = []
    for i, p in enumerate(files, start=1):
        text = p.read_text(encoding="utf-8", errors="replace")
        changed = dt.datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()
        notes.append({"id": f"m{i:03d}", "type": note_type(text),
                      "bytes": p.stat().st_size, "changed": changed})
    return {
        "note": ("Snapshot of the shared memory folder; one entry per note, "
                 "neutral ids in file-name order, no names or content. "
                 "The index file is not counted."),
        "measured": today,
        "notes": notes,
    }


def main(argv):
    if len(argv) != 3:
        print("usage: count_memory_notes.py MEMORY_DIR OUT_JSON", file=sys.stderr)
        return 2
    memory_dir, out = Path(argv[1]), Path(argv[2])
    if not memory_dir.is_dir():
        print(f"not a folder: {memory_dir}", file=sys.stderr)
        return 2
    data = snapshot(memory_dir, dt.date.today().isoformat())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(data['notes'])} notes -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
