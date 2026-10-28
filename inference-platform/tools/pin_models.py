#!/usr/bin/env python
"""Resolve and verify the Hugging Face revisions recorded in models.lock.yaml.

An unpinned ``revision: ""`` means a redeploy can silently pull different weights,
which turns into a silent cache-quality change rather than an error. Pin once,
commit the SHA, then let ``--check`` guard against drift.

    python tools/pin_models.py --write   # fill empty revisions with current SHAs
    python tools/pin_models.py --check    # fail if a pin is missing or unresolvable
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from huggingface_hub import HfApi
from huggingface_hub.utils import HfHubHTTPError

LOCK_PATH = Path(__file__).resolve().parent.parent / "models.lock.yaml"


def load() -> dict:
    return yaml.safe_load(LOCK_PATH.read_text())


def resolve(api: HfApi, model_id: str) -> str:
    return api.model_info(model_id, revision="main").sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="fill empty revisions")
    group.add_argument("--check", action="store_true", help="verify every pin")
    args = parser.parse_args()

    lock = load()
    roles: dict = lock.get("roles") or {}
    api = HfApi()

    problems: list[str] = []
    changed = False

    for name, spec in roles.items():
        model_id = spec.get("model")
        revision = spec.get("revision") or ""

        # fastembed resolves and pins its own snapshot for sparse roles, so a
        # Hugging Face SHA here would not describe what actually loads.
        if (spec.get("kind") or "").strip().lower() == "sparse":
            print(f"skip    {name:10s} {model_id} (kind=sparse, pinned by fastembed)")
            continue

        if args.check and not revision:
            problems.append(f"{name}: unpinned ({model_id})")
            continue

        try:
            sha = resolve(api, model_id)
        except HfHubHTTPError as exc:
            problems.append(f"{name}: cannot resolve {model_id} ({exc})")
            continue

        if args.write and not revision:
            spec["revision"] = sha
            changed = True
            print(f"pinned  {name:10s} {model_id} -> {sha[:12]}")
        elif args.write and revision and revision != sha:
            print(
                f"note    {name:10s} pinned at {revision[:12]}, main is now {sha[:12]}"
            )
        elif args.check:
            print(f"ok      {name:10s} {model_id} @ {sha[:12]}")

    if changed:
        LOCK_PATH.write_text(yaml.safe_dump(lock, sort_keys=False))
        print(f"\nupdated {LOCK_PATH}")

    if problems:
        print("\nproblems:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
