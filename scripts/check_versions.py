#!/usr/bin/env python3
"""Single version check for the monorepo.

Echo Gate has three version sources (gateway, web, inference-platform), each
at 0.1.0 today, and the released Docker image must stamp VERSION from the git
tag at build time. This script asserts all of them agree, and — when a git
tag is present — that the tag matches.

Usage:
    python scripts/check_versions.py            # check all sources agree
    python scripts/check_versions.py --tag v0.1.0  # also verify a specific tag

Exit 0 = in sync; exit 1 with a diff = out of sync.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SOURCES = [
    ("pyproject.toml", ROOT / "pyproject.toml"),
    ("web/package.json", ROOT / "web" / "package.json"),
    (
        "inference-platform/pyproject.toml",
        ROOT / "inference-platform" / "pyproject.toml",
    ),
]


def extract(text: str) -> str | None:
    # Handles `version = "0.1.0"` (toml) and `"version": "0.1.0"` (json).
    m = re.search(r'"version"\s*:\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def main() -> int:
    versions: dict[str, str] = {}
    ok = True
    for label, path in SOURCES:
        if not path.exists():
            print(f"missing: {label}", file=sys.stderr)
            ok = False
            continue
        v = extract(path.read_text())
        if v is None:
            print(f"could not parse version in {label}", file=sys.stderr)
            ok = False
        else:
            versions[label] = v
            print(f"{label}: {v}")

    distinct = set(versions.values())
    if len(distinct) > 1:
        print("versions out of sync:", file=sys.stderr)
        for label, v in versions.items():
            print(f"  {label}: {v}", file=sys.stderr)
        ok = False
    elif distinct:
        print(f"all sources: {next(iter(distinct))} (in sync)")

    tag = None
    if "--tag" in sys.argv:
        tag = sys.argv[sys.argv.index("--tag") + 1]
    if tag:
        clean = tag.lstrip("v")
        if distinct and clean != next(iter(distinct)):
            print(f"tag {tag} does not match sources {distinct}", file=sys.stderr)
            ok = False
        else:
            print(f"tag {tag}: matches sources")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
