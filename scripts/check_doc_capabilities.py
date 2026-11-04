#!/usr/bin/env python3
"""
Validates that documented Echo Gate capabilities point to real test files
and exported symbols, ensuring documentation never drifts from reality.
Source of truth: docs/capability-evidence.json
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "docs" / "capability-evidence.json"
README_PATH = ROOT / "README.md"


def fail(reason: str) -> None:
    print(f"[docs:check] ERROR: {reason}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    print("[docs:check] reading registry and README...")
    if not REGISTRY_PATH.exists():
        fail(f"Missing {REGISTRY_PATH}")

    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            registry = json.load(f)
    except Exception as e:
        fail(f"Invalid JSON in {REGISTRY_PATH}: {e}")

    capabilities = registry.get("capabilities", [])
    if not isinstance(capabilities, list):
        fail("capability-evidence.json must have a 'capabilities' list")

    errors = []
    checked = 0

    for entry in capabilities:
        cap_id = entry.get("id", "(unknown)")
        doc_rel = entry.get("doc")
        claim = entry.get("claim")
        source_rel = entry.get("source")
        export_sym = entry.get("export")
        test_rel = entry.get("test")
        test_needle = entry.get("testNeedle")

        # 1. Verify doc claim
        if doc_rel and claim:
            doc_path = ROOT / doc_rel
            if not doc_path.exists():
                errors.append(f"{cap_id}: doc file missing: {doc_rel}")
            else:
                doc_text = doc_path.read_text(encoding="utf-8")
                if claim not in doc_text:
                    errors.append(f"{cap_id}: claim '{claim}' not found in {doc_rel}")

        # 2. Verify source export
        if source_rel and export_sym:
            source_path = ROOT / source_rel
            if not source_path.exists():
                errors.append(f"{cap_id}: source file missing: {source_rel}")
            else:
                src_text = source_path.read_text(encoding="utf-8")
                if not re.search(r"\b" + re.escape(export_sym) + r"\b", src_text):
                    errors.append(f"{cap_id}: symbol '{export_sym}' not found in {source_rel}")

        # 3. Verify test evidence
        if test_rel and test_needle:
            test_path = ROOT / test_rel
            if not test_path.exists():
                errors.append(f"{cap_id}: test file missing: {test_rel}")
            else:
                test_text = test_path.read_text(encoding="utf-8")
                if test_needle not in test_text:
                    errors.append(f"{cap_id}: test needle '{test_needle}' not found in {test_rel}")

        checked += 1

    if errors:
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        fail(f"{len(errors)} capability drift(s) detected.")

    print(f"[docs:check] {checked} documented capabilities checked; all point to real code and test evidence.")


if __name__ == "__main__":
    main()
