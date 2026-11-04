"""Deterministic OpenAPI contract snapshot test.

Regenerates the FastAPI OpenAPI document and compares it against the committed
snapshot in ``tests/snapshots/openapi.json``. Any structural change to the API
contract (endpoints, request/response schemas) fails the test and rewrites the
snapshot so the change shows up in ``git diff``.

Differences are classified so acceptance is mechanical:

- Additive changes (new endpoints, new schemas) are labelled as safe to accept.
- Removals and modifications of existing contract elements are labelled as
  breaking and must be reviewed before the regenerated snapshot is committed.
"""

import json
import unittest
from pathlib import Path

from app.main import app

SNAPSHOT_PATH = Path(__file__).resolve().parent / "snapshots" / "openapi.json"

HTTP_METHODS = (
    "get",
    "put",
    "post",
    "delete",
    "options",
    "head",
    "patch",
    "trace",
)


def canonical_json(spec: dict) -> str:
    """Serialize the spec deterministically so diffs are stable across runs."""
    return json.dumps(spec, sort_keys=True, indent=2) + "\n"


def iter_operations(spec: dict) -> dict[str, dict]:
    """Return ``{"GET /path": operation}`` for every operation in the spec."""
    operations = {}
    for path, path_item in spec.get("paths", {}).items():
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if operation is not None:
                operations[f"{method.upper()} {path}"] = operation
    return operations


def diff_specs(old: dict, new: dict) -> dict[str, list[str]]:
    """Classify contract differences between two OpenAPI documents."""
    old_ops = iter_operations(old)
    new_ops = iter_operations(new)
    old_keys, new_keys = old_ops.keys(), new_ops.keys()

    old_schemas = old.get("components", {}).get("schemas", {})
    new_schemas = new.get("components", {}).get("schemas", {})

    old_components = old.get("components", {})
    new_components = new.get("components", {})
    component_sections = sorted(
        f"components.{key}"
        for key in set(old_components) | set(new_components)
        if key != "schemas" and old_components.get(key) != new_components.get(key)
    )

    other_sections = sorted(
        key
        for key in set(old) | set(new)
        if key not in {"paths", "components"} and old.get(key) != new.get(key)
    )

    return {
        "added_endpoints": sorted(new_keys - old_keys),
        "removed_endpoints": sorted(old_keys - new_keys),
        "changed_endpoints": sorted(
            key for key in old_keys & new_keys if old_ops[key] != new_ops[key]
        ),
        "added_schemas": sorted(new_schemas.keys() - old_schemas.keys()),
        "removed_schemas": sorted(old_schemas.keys() - new_schemas.keys()),
        "changed_schemas": sorted(
            key
            for key in old_schemas.keys() & new_schemas.keys()
            if old_schemas[key] != new_schemas[key]
        ),
        "other_sections": component_sections + other_sections,
    }


def format_report(diff: dict[str, list[str]]) -> str:
    """Render the diff classification as a pass/fail friendly report."""
    breaking = [
        ("removed endpoint", diff["removed_endpoints"]),
        ("changed endpoint", diff["changed_endpoints"]),
        ("removed schema", diff["removed_schemas"]),
        ("changed schema", diff["changed_schemas"]),
        ("changed section", diff["other_sections"]),
    ]
    additive = [
        ("added endpoint", diff["added_endpoints"]),
        ("added schema", diff["added_schemas"]),
    ]

    lines = [
        "The generated OpenAPI document differs from the committed snapshot.",
        f"The snapshot was regenerated at {SNAPSHOT_PATH} - review it with `git diff`.",
        "",
    ]

    has_breaking = any(items for _, items in breaking)
    has_additive = any(items for _, items in additive)

    lines.append("BREAKING changes (review before accepting):")
    if has_breaking:
        for label, items in breaking:
            lines.extend(f"  - {label}: {item}" for item in items)
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("Additive changes (safe to accept):")
    if has_additive:
        for label, items in additive:
            lines.extend(f"  + {label}: {item}" for item in items)
    else:
        lines.append("  (none)")

    lines.append("")
    if has_breaking:
        lines.append(
            "Existing contract elements changed. If this is intentional, "
            "commit the regenerated snapshot; otherwise revert it with "
            "`git checkout -- tests/snapshots/openapi.json`."
        )
    else:
        lines.append(
            "Every change is additive - review and commit the regenerated "
            "snapshot to accept it."
        )
    return "\n".join(lines)


class TestOpenApiSnapshot(unittest.TestCase):
    """Gate the API contract against the committed OpenAPI snapshot."""

    maxDiff = None

    def test_openapi_document_matches_committed_snapshot(self):
        """Fail when app.openapi() drifts from tests/snapshots/openapi.json."""
        spec = app.openapi()
        rendered = canonical_json(spec)

        if not SNAPSHOT_PATH.exists():
            SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            SNAPSHOT_PATH.write_text(rendered)
            self.fail(
                f"OpenAPI snapshot did not exist and was created at "
                f"{SNAPSHOT_PATH}. Review and commit it to lock the contract."
            )

        current = SNAPSHOT_PATH.read_text()
        if rendered == current:
            return

        try:
            previous = json.loads(current)
        except json.JSONDecodeError:
            previous = {}

        SNAPSHOT_PATH.write_text(rendered)
        self.fail(format_report(diff_specs(previous, spec)))


if __name__ == "__main__":
    unittest.main()
