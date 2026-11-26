#!/usr/bin/env python3
"""Generate the environment-variable index in ``docs/ENV_VARS.md`` from code.

The table is derived by statically scanning ``api/app`` for environment reads
(``os.getenv`` / ``os.environ.get`` and the typed helpers in
``api/app/core/config.py``), so it cannot drift from what the gateway actually
reads. Hand-written prose stays in ``docs/CONFIGURATION.md`` and the annotated
``api/app/.env.example`` remains the operator template.

Usage:
    python scripts/gen_env_docs.py            # rewrite the generated block
    python scripts/gen_env_docs.py --check    # exit 1 if the block is stale
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOT = ROOT / "api" / "app"
TARGET = ROOT / "docs" / "ENV_VARS.md"

BEGIN = "<!-- BEGIN GENERATED: env vars (scripts/gen_env_docs.py) -->"
END = "<!-- END GENERATED -->"

HELPERS = {
    "_int_env": "int",
    "_float_env": "float",
    "_bounded_float_env": "float",
    "_bool_env": "bool",
    "_optional_bool_env": "bool?",
    "_str_env": "str",
}
CASTS = {"int": "int", "float": "float", "str": "str", "bool": "bool"}
READERS = {"getenv", "get"}
#: ``.get`` is only an env read when it is called on ``os.environ``; a bare
#: ``dict.get("SOME_KEY", 0)`` is not.
ENVIRON_OWNERS = {"environ"}


def call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def literal(node: ast.expr) -> str:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return "*(computed)*"
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "`true`" if value else "`false`"
    if isinstance(value, str):
        return f"`{value}`" if value else '`""`'
    return f"`{value}`"


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    return {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _is_env_read(node: ast.Call, name: str) -> bool:
    func = node.func
    if name == "getenv":
        return True
    if name != "get":
        return False
    if not isinstance(func, ast.Attribute):
        return False
    owner = func.value
    if isinstance(owner, ast.Attribute):
        return owner.attr in ENVIRON_OWNERS
    return isinstance(owner, ast.Name) and owner.id in ENVIRON_OWNERS


def _cast_around(node: ast.Call, parents: dict[int, ast.AST]) -> str | None:
    """Return the cast type when the read is wrapped as ``int(os.getenv(...))``."""
    parent = parents.get(id(node))
    if isinstance(parent, ast.Call):
        name = call_name(parent)
        if name in CASTS:
            return CASTS[name]
    return None


def scan_file(path: Path) -> list[tuple[str, str, str, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    parents = _parents(tree)
    rows: list[tuple[str, str, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        if not name or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        var = first.value
        if not (var.isupper() and var.replace("_", "").isalnum()):
            continue
        kind: str | None = None
        if name in HELPERS:
            kind = HELPERS[name]
        elif name in READERS and _is_env_read(node, name):
            kind = _cast_around(node, parents) or "str"
        else:
            continue
        default_node: ast.expr | None = node.args[1] if len(node.args) > 1 else None
        # A "true"/"false" literal default is a boolean flag whichever reader saw it.
        if kind == "str" and isinstance(default_node, ast.Constant):
            lowered = str(default_node.value).strip().lower()
            if lowered in {"true", "false"}:
                kind = "bool"
        default = literal(default_node) if default_node is not None else "—"
        source = path.relative_to(SCAN_ROOT).as_posix()
        rows.append((var, kind, default, source))
    return rows


def collect() -> list[tuple[str, str, str, str]]:
    seen: dict[str, tuple[str, str, str, str]] = {}
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        if any(part == "tests" for part in path.parts):
            continue
        for var, kind, default, source in scan_file(path):
            seen.setdefault(var, (var, kind, default, source))
    return [seen[key] for key in sorted(seen)]


def render(rows: list[tuple[str, str, str, str]]) -> str:
    lines = [
        BEGIN,
        f"<!-- {len(rows)} variables. Regenerate with: python scripts/gen_env_docs.py -->",
        "",
        "| Variable | Type | Default | Read in |",
        "| --- | --- | --- | --- |",
    ]
    for var, kind, default, source in rows:
        lines.append(f"| `{var}` | {kind} | {default} | `{source}` |")
    lines.append(END)
    return "\n".join(lines)


def splice(current: str, block: str) -> str:
    start = current.find(BEGIN)
    end = current.find(END)
    if start == -1 or end == -1:
        raise SystemExit(f"{TARGET}: generated markers not found; add {BEGIN} / {END}")
    return current[:start] + block + current[end + len(END) :]


def main(argv: list[str]) -> int:
    rows = collect()
    block = render(rows)
    if not TARGET.exists():
        raise SystemExit(f"{TARGET}: missing; create it with the generated markers")
    current = TARGET.read_text(encoding="utf-8")
    updated = splice(current, block)
    if "--check" in argv:
        if current.strip() != updated.strip():
            print(
                "[docs:env] docs/ENV_VARS.md is stale — "
                "run `python scripts/gen_env_docs.py` and commit the result."
            )
            return 1
        print(f"[docs:env] docs/ENV_VARS.md matches code ({len(rows)} variables).")
        return 0
    if current != updated:
        TARGET.write_text(updated, encoding="utf-8")
        print(f"[docs:env] wrote {len(rows)} variables to docs/ENV_VARS.md")
    else:
        print(f"[docs:env] docs/ENV_VARS.md already current ({len(rows)} variables)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
