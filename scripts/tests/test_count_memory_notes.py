"""Tests for scripts/measurements/count_memory_notes.py (memory-folder snapshot)."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "count_memory_notes", ROOT / "scripts" / "measurements" / "count_memory_notes.py")
cmn = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cmn
spec.loader.exec_module(cmn)


def note(path: Path, kind: str, body: str = "secret text about project-x") -> None:
    path.write_text(f"---\nname: {path.stem}\nmetadata:\n  type: {kind}\n---\n\n{body}\n",
                    encoding="utf-8")


def test_counts_notes_without_names_or_content(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    note(mem / "client-alpha-project.md", "project")
    note(mem / "zeta.md", "feedback")
    (mem / "MEMORY.md").write_text("- index line\n", encoding="utf-8")
    (mem / "notes.txt").write_text("not a note\n", encoding="utf-8")
    out = tmp_path / "out.json"
    assert cmn.main(["x", str(mem), str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [n["id"] for n in data["notes"]] == ["m001", "m002"]
    assert [n["type"] for n in data["notes"]] == ["project", "feedback"]
    text = out.read_text(encoding="utf-8")
    assert "client-alpha" not in text and "secret text" not in text and "zeta" not in text


def test_type_only_from_front_matter():
    assert cmn.note_type("---\ntype: user\n---\n") == "user"
    assert cmn.note_type("no front matter\ntype: user\n") == "unknown"
    assert cmn.note_type("---\nname: a\n---\ntype: user\n") == "unknown"


def test_snapshot_in_repository_is_consistent():
    snap = json.loads((ROOT / "data" / "memory-snapshot" / "notes-2026-10-05.json")
                      .read_text(encoding="utf-8"))
    ids = [n["id"] for n in snap["notes"]]
    assert ids == [f"m{i:03d}" for i in range(1, len(ids) + 1)]
    assert snap["measured"] == "2026-10-05"
    assert set(snap["notes"][0]) == {"id", "type", "bytes", "changed"}
