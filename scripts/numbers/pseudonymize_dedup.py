#!/usr/bin/env python3
"""Pseudonymize note slugs in the duplicate-run data files (21 Sept 2026).

The raw run files use the file names of the private note folder as keys.
Those names may contain client and project references, so they must not be
published. This script replaces every note slug deterministically with a
short ID (``n001``, ``n002``, …) in sorted order of the original slugs. All
input files of one call share **one** mapping over the union of their slugs,
so the same private note gets the same public ID in every output file and the
files can be joined on their IDs. The mapping itself is never written
anywhere.

Usage (from the repository root):

    python3 scripts/numbers/pseudonymize_dedup.py --output data/dedup-run/ \
        --input <private-pairs.json> --input <private-unlinked.json>

Supported inputs (both from the private measurement folder):
  * the full pair file:      {"a||b": {...scores...}, ...}
  * the unlinked-pairs file: [[score, "a", "b", {...}], ...]

Standard library only; reads every --input, writes one file per input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PAIR_SEP = "||"
SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def collect_slugs(obj) -> set:
    """Return every note slug used in a supported input structure."""
    slugs = set()
    if isinstance(obj, dict):
        for key in obj:
            if not isinstance(key, str) or PAIR_SEP not in key:
                raise ValueError(f"unsupported pair key: {key!r}")
            left, right = key.split(PAIR_SEP, 1)
            slugs.add(left)
            slugs.add(right)
    elif isinstance(obj, list):
        for row in obj:
            if not (isinstance(row, list) and len(row) >= 3):
                raise ValueError(f"unsupported row: {row!r}")
            slugs.add(row[1])
            slugs.add(row[2])
    else:
        raise ValueError("input must be a JSON object (pairs) or list (rows)")
    return slugs


def build_mapping(slugs: set) -> dict:
    """Sorted-order replacement: n001, n002, … (three digits, grows if needed)."""
    width = max(3, len(str(len(slugs))))
    return {slug: f"n{i:0{width}d}" for i, slug in enumerate(sorted(slugs), 1)}


def shared_mapping(datasets) -> dict:
    """One mapping over the union of the slugs of all inputs."""
    slugs = set()
    for data in datasets:
        slugs |= collect_slugs(data)
    for slug in slugs:
        if not SLUG_OK.match(slug):
            raise ValueError(f"unexpected slug shape: {slug!r}")
    return build_mapping(slugs)


def pseudonymize_pairs(obj, mapping: dict):
    """Rewrite the top-level "a||b" keys; score dicts below stay untouched."""
    out = {}
    for key, value in obj.items():
        left, right = key.split(PAIR_SEP, 1)
        out[mapping[left] + PAIR_SEP + mapping[right]] = value
    return out


def pseudonymize_row(row: list, mapping: dict) -> list:
    out = list(row)
    out[1] = mapping[row[1]]
    out[2] = mapping[row[2]]
    return out


def apply_mapping(data, mapping: dict):
    """Pseudonymize one input structure with an existing shared mapping."""
    if isinstance(data, dict):
        return pseudonymize_pairs(data, mapping)
    return [pseudonymize_row(row, mapping) for row in data]


def process(data):
    """Single-input convenience: own mapping, as in earlier versions."""
    mapping = shared_mapping([data])
    return apply_mapping(data, mapping), len(mapping)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Pseudonymize note slugs (deterministic, one mapping per call).")
    ap.add_argument("--input", action="append", required=True, dest="inputs",
                    help="private raw JSON file (repeat for both dedup files)")
    ap.add_argument("--output", required=True, help="output directory of this repo")
    args = ap.parse_args(argv)

    out_dir = Path(args.output)
    sources = []
    for name in args.inputs:
        src = Path(name)
        if not src.is_file():
            print(f"error: input not found: {src}", file=sys.stderr)
            return 2
        sources.append(src)
    try:
        datasets = [json.loads(src.read_text(encoding="utf-8")) for src in sources]
        mapping = shared_mapping(datasets)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    for src, data in zip(sources, datasets):
        target = out_dir / src.name
        result = apply_mapping(data, mapping)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{target}: {len(mapping)} note slugs in the shared mapping (mapping not stored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
