#!/usr/bin/env python3
"""Guard that runs before anything is published.

Scans every file under a root directory for private content: terms from a
private deny-list (kept outside the repo), names of private notes, secret
shapes, absolute home paths and e-mail addresses.

Output never contains a deny-listed term in full, so it is safe in public logs.

Exit codes: 0 clean, 1 findings, 2 usage error.
"""

import argparse
import fnmatch
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}
# Internal work files; skipped only at the top level, never deeper in the tree.
TOP_SKIP = {"intern"}
BINARY_OK = {".png", ".jpg", ".jpeg", ".pdf", ".gz", ".zip"}

# Built-in patterns: (category, compiled regex).
BUILTIN = [
    ("api key shape", re.compile(r"sk-[A-Za-z0-9_-]{16,}")),
    ("api key shape", re.compile("sk-" + "ant-")),
    ("api key shape", re.compile(r"tp-[A-Za-z0-9]{16,}")),
    ("api key shape", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("api key shape", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    ("api key shape", re.compile("github" + "_pat_")),
    ("api key shape", re.compile(r"xox[baprs]-")),
    ("api key shape", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "secret assignment",
        re.compile(
            r"(API_KEY|TOKEN|SECRET|PASSWORD|PASSWORT)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}",
            re.IGNORECASE,
        ),
    ),
    ("home directory path", re.compile(r"/home/[a-z]")),
    ("home directory path", re.compile(r"/Users/[A-Za-z]")),
    ("home directory path", re.compile(r"C:\\Users\\")),
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# Fixed allow-list. No per-file exceptions exist on purpose.
EMAIL_ALLOWED_EXACT = {
    "noreply@anthropic.com",
    "user@example.com",
}
EMAIL_ALLOWED_GLOBS = ["*@example.org", "*@example.com"]


def email_allowed(addr):
    low = addr.lower()
    if low in EMAIL_ALLOWED_EXACT:
        return True
    return any(fnmatch.fnmatchcase(low, g) for g in EMAIL_ALLOWED_GLOBS)


class UsageError(Exception):
    pass


def load_forbidden(path):
    """Return a list of (list line number, kind, matcher)."""
    p = Path(path)
    if not p.is_file():
        raise UsageError(f"deny-list not found: {path}")
    rules = []
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"cannot read deny-list: {exc}")
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("wort:"):
            term = line[5:].strip()
            if not term:
                raise UsageError(f"deny-list line {no}: empty term")
            rules.append((no, "wort", term.lower()))
        elif line.startswith("regex:"):
            pat = line[6:].strip()
            try:
                rx = re.compile(pat, re.IGNORECASE)
            except re.error:
                raise UsageError(f"deny-list line {no}: invalid regex")
            if not pat:
                raise UsageError(f"deny-list line {no}: empty regex")
            rules.append((no, "regex", rx))
        else:
            raise UsageError(f"deny-list line {no}: unknown line format")
    return rules


def load_note_names(directory):
    d = Path(directory)
    if not d.is_dir():
        raise UsageError(f"notes directory not found: {directory}")
    names = []
    for f in sorted(d.glob("*.md")):
        stem = f.name[:-3]
        if stem == "MEMORY":
            continue
        if "-" in stem and len(stem) >= 12:
            names.append(stem.lower())
    return names


def iter_files(root, extra_excludes):
    excluded = set()
    for e in extra_excludes:
        excluded.add((root / e).resolve())
    for path in sorted(root.rglob("*")):
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts[:-1]):
            continue
        if len(rel_parts) > 1 and rel_parts[0] in TOP_SKIP:
            continue
        if path.is_dir():
            continue
        if rel_parts[-1] in SKIP_DIRS:
            continue
        res = path.resolve()
        if any(res == ex or ex in res.parents for ex in excluded):
            continue
        yield path


def scan(root, rules, note_names, excludes):
    findings = []
    skipped = []
    scanned = 0
    for path in iter_files(root, excludes):
        rel = path.relative_to(root).as_posix()
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            if path.suffix.lower() in BINARY_OK:
                skipped.append(rel)
            else:
                findings.append(f"{rel}:0: unreadable file")
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            for no, kind, matcher in rules:
                if kind == "wort":
                    hit = matcher in low
                    first = matcher[0]
                else:
                    m = matcher.search(line)
                    hit = m is not None
                    first = m.group(0)[:1] if m else "?"
                if hit:
                    findings.append(
                        f"{rel}:{lineno}: forbidden term #{no} ({first}***)"
                    )
            for name in note_names:
                if name in low:
                    findings.append(
                        f"{rel}:{lineno}: private note name ({name[:3]}***)"
                    )
            for cat, rx in BUILTIN:
                if rx.search(line):
                    findings.append(f"{rel}:{lineno}: {cat}")
            for m in EMAIL_RE.finditer(line):
                if not email_allowed(m.group(0)):
                    findings.append(f"{rel}:{lineno}: e-mail address")
    return scanned, findings, skipped


def main(argv=None):
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Pre-publication privacy guard.")
    ap.add_argument("--root", default=str(here))
    ap.add_argument("--verbote", help="private deny-list file (outside the repo)")
    ap.add_argument("--merkzettel", help="directory with private *.md notes")
    ap.add_argument("--exclude", nargs="*", default=[], help="paths relative to root")
    args = ap.parse_args(argv)

    try:
        if not args.verbote:
            raise UsageError("--verbote is required")
        root = Path(args.root)
        if not root.is_dir():
            raise UsageError(f"root not found: {args.root}")
        rules = load_forbidden(args.verbote)
        notes = load_note_names(args.merkzettel) if args.merkzettel else []
        scanned, findings, skipped = scan(root.resolve(), rules, notes, args.exclude)
        if scanned == 0:
            raise UsageError("nothing scanned (empty tree is suspicious)")
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for rel in skipped:
        print(f"{rel}: binary skipped (not scanned)")
    for f in findings:
        print(f)
    files_with = len({f.split(":", 1)[0] for f in findings})
    if findings:
        print(f"{len(findings)} findings in {files_with} files")
    print(f"scanned {scanned} files")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
