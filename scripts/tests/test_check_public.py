"""Tests for scripts/check_public.py.

Decoys (fake secrets, private paths) are assembled at run time so they never
appear literally in this file, which is itself scanned by the guard.
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "check_public.py"

# Run-time assembled decoys.
WORD = "zebra" + "quartz"
HOME_PATH = "/ho" + "me/" + "someone/x"
USERS_PATH = "/Us" + "ers/" + "Someone/x"
WIN_PATH = "C:" + "\\" + "Users" + "\\" + "Someone"
MAIL = "bob" + "@" + "corp.test"
ALLOWED_MAIL = "no" + "reply" + "@" + "anthropic.com"

SECRETS = {
    "sk": "sk" + "-" + "x" * 20,
    "sk-ant": "sk" + "-" + "ant" + "-" + "abc",
    "tp": "tp" + "-" + "a" * 20,
    "aiza": "AI" + "za" + "b" * 35,
    "ghp": "gh" + "p_" + "c" * 36,
    "pat": "github" + "_pat_" + "zzz",
    "slack": "xo" + "xb-" + "123",
    "pem": "-----" + "BEGIN RSA PRIV" + "ATE KEY-----",
    "assign": "API" + "_KEY = " + "'" + "q" * 16 + "'",
}


def run(root, verbote, *extra):
    cmd = [sys.executable, str(SCRIPT), "--root", str(root)]
    if verbote is not None:
        cmd += ["--verbote", str(verbote)]
    cmd += list(extra)
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("Hello world\n", encoding="utf-8")
    vb = tmp_path / "verbote.txt"
    vb.write_text(f"# comment\n\nwort: {WORD}\nregex: proj[0-9]{{3}}x\n", encoding="utf-8")
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "MEMORY.md").write_text("index", encoding="utf-8")
    (notes / "secret-project-name.md").write_text("x", encoding="utf-8")
    return root, vb, notes


def test_clean_tree(env):
    root, vb, notes = env
    r = run(root, vb, "--merkzettel", str(notes))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "scanned 1 files" in r.stdout


def test_forbidden_word_case_insensitive_substring(env):
    root, vb, _ = env
    (root / "a.txt").write_text("prefix " + WORD.upper() + "suffix\n", encoding="utf-8")
    r = run(root, vb)
    assert r.returncode == 1
    assert "a.txt:1: forbidden term #3" in r.stdout


def test_regex_line(env):
    root, vb, _ = env
    (root / "a.txt").write_text("see PROJ123X here\n", encoding="utf-8")
    r = run(root, vb)
    assert r.returncode == 1
    assert "forbidden term #4" in r.stdout


def test_merkzettel_name(env):
    root, vb, notes = env
    (root / "a.txt").write_text("see Secret-Project-Name now\n", encoding="utf-8")
    r = run(root, vb, "--merkzettel", str(notes))
    assert r.returncode == 1
    assert "private note name (sec***)" in r.stdout


@pytest.mark.parametrize("key", sorted(SECRETS))
def test_secret_shapes(env, key):
    root, vb, _ = env
    (root / "a.txt").write_text("x " + SECRETS[key] + "\n", encoding="utf-8")
    r = run(root, vb)
    assert r.returncode == 1, key
    assert "a.txt:1:" in r.stdout


@pytest.mark.parametrize("path", [HOME_PATH, USERS_PATH, WIN_PATH])
def test_home_paths(env, path):
    root, vb, _ = env
    (root / "a.txt").write_text("at " + path + "\n", encoding="utf-8")
    r = run(root, vb)
    assert r.returncode == 1
    assert "home directory path" in r.stdout


def test_foreign_email(env):
    root, vb, _ = env
    (root / "a.txt").write_text("mail " + MAIL + "\n", encoding="utf-8")
    r = run(root, vb)
    assert r.returncode == 1
    assert "e-mail address" in r.stdout
    assert MAIL not in r.stdout


def test_allowed_email_only(env):
    root, vb, _ = env
    text = ALLOWED_MAIL + " " + "user" + "@" + "example.com" + " a" + "@" + "example.org\n"
    (root / "a.txt").write_text(text, encoding="utf-8")
    r = run(root, vb)
    assert r.returncode == 0, r.stdout


def test_output_hides_forbidden_word(env):
    root, vb, notes = env
    (root / "a.txt").write_text(WORD + " Secret-Project-Name\n", encoding="utf-8")
    r = run(root, vb, "--merkzettel", str(notes))
    out = (r.stdout + r.stderr).lower()
    assert r.returncode == 1
    assert WORD not in out
    assert "secret-project-name" not in out


def test_intern_ignored_and_exclude_and_hidden(env):
    root, vb, _ = env
    (root / "intern").mkdir()
    (root / "intern" / "x.md").write_text(WORD, encoding="utf-8")
    assert run(root, vb).returncode == 0
    (root / "skipme").mkdir()
    (root / "skipme" / "y.txt").write_text(WORD, encoding="utf-8")
    assert run(root, vb).returncode == 1
    assert run(root, vb, "--exclude", "skipme").returncode == 0
    (root / ".env").write_text(WORD, encoding="utf-8")
    r = run(root, vb, "--exclude", "skipme")
    assert r.returncode == 1
    assert ".env:1:" in r.stdout


def test_missing_verbote_is_usage_error(env):
    root, _, _ = env
    assert run(root, None).returncode == 2


def test_missing_verbote_file_is_usage_error(env, tmp_path):
    root, _, _ = env
    assert run(root, tmp_path / "nope.txt").returncode == 2


def test_broken_verbote_line(env, tmp_path):
    root, _, _ = env
    bad = tmp_path / "bad.txt"
    bad.write_text("garbage line\n", encoding="utf-8")
    assert run(root, bad).returncode == 2


def test_empty_tree(env, tmp_path):
    _, vb, _ = env
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run(empty, vb).returncode == 2


def test_unreadable_bin_is_finding(env):
    root, vb, _ = env
    (root / "x.bin").write_bytes(b"\xff\xfe\x00\x80")
    r = run(root, vb)
    assert r.returncode == 1
    assert "x.bin:0: unreadable file" in r.stdout


def test_png_is_info_only(env):
    root, vb, _ = env
    (root / "x.png").write_bytes(b"\x89PNG\xff\xfe\x00")
    r = run(root, vb)
    assert r.returncode == 0
    assert "x.png: binary skipped (not scanned)" in r.stdout


def test_short_or_hyphenless_note_names_ignored(env):
    root, vb, notes = env
    (notes / "nohyphenatall.md").write_text("x", encoding="utf-8")
    (notes / "a-b.md").write_text("x", encoding="utf-8")
    (root / "a.txt").write_text("nohyphenatall and a-b here\n", encoding="utf-8")
    r = run(root, vb, "--merkzettel", str(notes))
    assert r.returncode == 0, r.stdout


def test_nested_intern_is_scanned(env):
    root, vb, _ = env
    (root / "data" / "intern").mkdir(parents=True)
    (root / "data" / "intern" / "z.md").write_text(WORD, encoding="utf-8")
    assert run(root, vb).returncode == 1


def test_author_mail_is_not_allowed(env):
    root, vb, _ = env
    (root / "m.txt").write_text("fatih" + "altiok" + "@" + "outlook.com", encoding="utf-8")
    assert run(root, vb).returncode == 1
