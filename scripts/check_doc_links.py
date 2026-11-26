#!/usr/bin/env python3
"""Fail when an internal Markdown link or anchor in the repo does not resolve.

Checks every tracked ``*.md`` file:

* relative links must point at a path that exists;
* ``#fragment`` links must match a heading in the target file, using GitHub's
  slug rules (lowercase, punctuation stripped, spaces to hyphens, duplicates
  suffixed ``-1``, ``-2``, ...), or an explicit ``<a id="...">`` /
  ``<a name="...">`` anchor;
* links to non-Markdown files are checked for existence only.

At the repo root ``ARCHITECTURE.md``, ``API_REFERENCE.md``, ``CONFIGURATION.md``,
``ROADMAP.md``, and the other symlinked docs are skipped as link *sources*: their
relative links are written from ``docs/`` and already validated there.

Usage:
    python scripts/check_doc_links.py [paths...]

Exit code 0 when everything resolves, 1 otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FENCE_RE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.MULTILINE | re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*([^)\s]+)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
EXPLICIT_ANCHOR_RE = re.compile(r"""<a\s+(?:id|name)=["']([^"']+)["']""", re.IGNORECASE)


def slugify(text: str) -> str:
    """Approximate GitHub's heading slug (unicode letters kept, punctuation dropped)."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[`*_~]", "", text).strip().lower()
    out: list[str] = []
    for ch in text:
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif ch.isspace():
            out.append("-")
    return "".join(out)


def markdown_files() -> list[Path]:
    try:
        out = subprocess.check_output(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "*.md",
            ],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        ).decode()
        files = [ROOT / p for p in out.split("\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [
            p
            for p in ROOT.rglob("*.md")
            if not any(part in {"node_modules", ".venv", ".git"} for part in p.parts)
        ]
    return [p for p in files if p.exists() and not p.is_symlink()]


class AnchorIndex:
    """Cached heading/anchor lookup for Markdown link targets."""

    def __init__(self) -> None:
        self._cache: dict[Path, set[str]] = {}

    def anchors(self, path: Path) -> set[str]:
        if path not in self._cache:
            found: dict[str, int] = {}
            if path.suffix.lower() == ".md":
                body = FENCE_RE.sub(
                    "", path.read_text(encoding="utf-8", errors="replace")
                )
                body = INLINE_CODE_RE.sub("", body)
                for heading in HEADING_RE.findall(body):
                    base = slugify(heading)
                    count = found.get(base, 0)
                    found[base] = count + 1
                    found[f"{base}-{count}" if count else base] = 0
            self._cache[path] = set(found)
        return self._cache[path]


def main(argv: list[str]) -> int:
    files = [ROOT / a for a in argv] if argv else markdown_files()
    index = AnchorIndex()
    errors: list[str] = []

    for path in files:
        if not path.exists():
            errors.append(f"{path}: file not found")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        text = FENCE_RE.sub("", text)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in LINK_RE.finditer(INLINE_CODE_RE.sub("", line)):
                link = match.group(1)
                if link.startswith(("http://", "https://", "mailto:", "tel:")):
                    continue
                target_part, _, fragment = link.partition("#")
                target = (
                    path if not target_part else (path.parent / target_part).resolve()
                )
                rel = target_part or path.name
                if not target.exists():
                    errors.append(f"{path.relative_to(ROOT)}:{line_no}: missing {rel}")
                    continue
                if fragment:
                    if fragment not in index.anchors(target):
                        errors.append(
                            f"{path.relative_to(ROOT)}:{line_no}: no anchor "
                            f"#{fragment} in {target.relative_to(ROOT)}"
                        )

    if errors:
        print("Broken documentation links:")
        for err in errors:
            print(f"  {err}")
        return 1
    print(
        f"[docs:links] {len(files)} markdown files checked; all internal links resolve."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
