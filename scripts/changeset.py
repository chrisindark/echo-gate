#!/usr/bin/env python3
"""Changeset and Semantic Versioning CLI for Echo Gate.

Manages modular changelog entries (changesets), semantic versioning calculation,
and automatic version synchronization across `pyproject.toml`, `web/package.json`,
and `inference-platform/pyproject.toml`.

Commands:
    add       Create a new pending changeset file in `.changesets/`
    status    Show current version, pending changesets, and next computed version
    check     Validate pending changesets and verify monorepo version synchronization
    bump      Apply pending changesets: bump versions across files, update CHANGELOG.md,
              and archive consumed changesets
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CHANGESETS_DIR = ROOT / ".changesets"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"

VERSION_FILES = [
    ("pyproject.toml", ROOT / "pyproject.toml", r'(^version\s*=\s*")[^"]+(")'),
    ("web/package.json", ROOT / "web" / "package.json", r'("version"\s*:\s*")[^"]+(")'),
    (
        "inference-platform/pyproject.toml",
        ROOT / "inference-platform" / "pyproject.toml",
        r'(^version\s*=\s*")[^"]+(")',
    ),
]

VALID_TYPES = ("patch", "minor", "major")
VALID_AREAS = ("gateway", "web", "inference", "docs", "ci", "deps", "core")


class Changeset(NamedTuple):
    path: Path
    bump_type: str
    area: str
    breaking: bool
    summary: str


def get_current_version() -> str:
    """Read the current canonical version from root pyproject.toml."""
    pyproject_path = ROOT / "pyproject.toml"
    text = pyproject_path.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise ValueError(f"Could not parse version from {pyproject_path}")
    return m.group(1)


def parse_changeset_file(path: Path) -> Changeset | None:
    """Parse a changeset markdown file with frontmatter."""
    if path.name in ("README.md", ".gitkeep") or path.suffix != ".md":
        return None

    content = path.read_text(encoding="utf-8").strip()
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
    if not match:
        raise ValueError(
            f"Invalid changeset format in {path.name}. Must start with YAML frontmatter delimiter (---)."
        )

    frontmatter_text, body = match.groups()
    meta: dict[str, str] = {}
    for line in frontmatter_text.strip().split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip().lower()] = val.strip()

    bump_type = meta.get("type", "patch").lower()
    if bump_type not in VALID_TYPES:
        raise ValueError(
            f"Invalid type '{bump_type}' in {path.name}. Must be one of: {', '.join(VALID_TYPES)}"
        )

    area = meta.get("area", "gateway").lower()
    breaking = meta.get("breaking", "false").lower() in ("true", "1", "yes")
    summary = body.strip()
    if not summary:
        raise ValueError(f"Empty changeset summary body in {path.name}")

    return Changeset(
        path=path,
        bump_type=bump_type,
        area=area,
        breaking=breaking,
        summary=summary,
    )


def load_pending_changesets() -> list[Changeset]:
    """Load all pending changesets from .changesets/ directory."""
    if not CHANGESETS_DIR.exists():
        return []

    changesets: list[Changeset] = []
    for file_path in sorted(CHANGESETS_DIR.glob("*.md")):
        cs = parse_changeset_file(file_path)
        if cs:
            changesets.append(cs)
    return changesets


def calculate_next_version(current: str, changesets: list[Changeset]) -> str:
    """Calculate next semver based on highest bump type in changesets."""
    parts = current.split(".")
    if len(parts) != 3:
        raise ValueError(f"Version '{current}' is not standard semver X.Y.Z")

    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if not changesets:
        return current

    has_major = any(c.bump_type == "major" or c.breaking for c in changesets)
    has_minor = any(c.bump_type == "minor" for c in changesets)

    if has_major:
        if major == 0:
            # 0.x semantic versioning convention: major breaking bump can be 0.(minor+1).0
            return f"0.{minor + 1}.0"
        return f"{major + 1}.0.0"
    elif has_minor:
        return f"{major}.{minor + 1}.0"
    else:
        return f"{major}.{minor}.{patch + 1}"


def create_changeset(
    bump_type: str,
    area: str,
    summary: str,
    breaking: bool = False,
) -> Path:
    """Generate a new changeset markdown file."""
    CHANGESETS_DIR.mkdir(parents=True, exist_ok=True)

    slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()[:35]).strip("-")
    if not slug:
        slug = "changeset"
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{slug}.md"
    target_path = CHANGESETS_DIR / filename

    content = f"""---
type: {bump_type}
area: {area}
breaking: {str(breaking).lower()}
---

{summary.strip()}
"""
    target_path.write_text(content, encoding="utf-8")
    return target_path


def apply_version_bump(
    new_version: str, changesets: list[Changeset], dry_run: bool = False
) -> None:
    """Update version in all project files, update CHANGELOG.md, and remove consumed changesets."""
    print(f"Applying version bump to v{new_version}...")

    # 1. Update version files
    for label, path, pattern in VERSION_FILES:
        if not path.exists():
            print(f"Warning: {label} not found, skipping.", file=sys.stderr)
            continue
        original = path.read_text(encoding="utf-8")
        updated = re.sub(pattern, rf"\g<1>{new_version}\g<2>", original, flags=re.M)
        if updated == original:
            print(
                f"Warning: version replacement had no effect in {label}",
                file=sys.stderr,
            )
        if not dry_run:
            path.write_text(updated, encoding="utf-8")
        print(f"  [bump] {label} -> {new_version}")

    # 2. Update CHANGELOG.md
    if CHANGELOG_PATH.exists() and changesets:
        changelog_text = CHANGELOG_PATH.read_text(encoding="utf-8")
        today = datetime.date.today().isoformat()

        # Categorize entries
        added_entries: list[str] = []
        changed_entries: list[str] = []
        fixed_entries: list[str] = []

        for cs in changesets:
            prefix = "[BREAKING] " if cs.breaking else ""
            area_tag = f"**[{cs.area}]** " if cs.area != "gateway" else ""
            entry_line = f"- {prefix}{area_tag}{cs.summary}"
            if cs.bump_type == "major" or cs.breaking:
                changed_entries.append(entry_line)
            elif cs.bump_type == "minor":
                added_entries.append(entry_line)
            else:
                fixed_entries.append(entry_line)

        section_parts = [f"## [v{new_version}] - {today}\n"]
        if added_entries:
            section_parts.append("### Added\n\n" + "\n".join(added_entries) + "\n")
        if changed_entries:
            section_parts.append("### Changed\n\n" + "\n".join(changed_entries) + "\n")
        if fixed_entries:
            section_parts.append("### Fixed\n\n" + "\n".join(fixed_entries) + "\n")

        new_section = "\n".join(section_parts).strip() + "\n\n"

        # Insert above the first release section
        # Look for ## [v
        match = re.search(r"^##\s+\[v\d", changelog_text, re.M)
        if match:
            pos = match.start()
            updated_changelog = (
                changelog_text[:pos] + new_section + changelog_text[pos:]
            )
        else:
            # Fallback: append after [Unreleased]
            unreleased_pos = changelog_text.find("## [Unreleased]")
            if unreleased_pos != -1:
                end_of_line = changelog_text.find("\n", unreleased_pos)
                updated_changelog = (
                    changelog_text[: end_of_line + 1]
                    + "\n"
                    + new_section
                    + changelog_text[end_of_line + 1 :]
                )
            else:
                updated_changelog = changelog_text + "\n" + new_section

        if not dry_run:
            CHANGELOG_PATH.write_text(updated_changelog, encoding="utf-8")
        print(
            f"  [changelog] Updated {CHANGELOG_PATH.name} with v{new_version} release notes"
        )

    # 3. Remove consumed changesets
    if not dry_run:
        for cs in changesets:
            if cs.path.exists():
                cs.path.unlink()
        print(f"  [clean] Removed {len(changesets)} consumed changeset file(s)")

    print(f"Version bump to v{new_version} completed successfully.")


def cmd_add(args: argparse.Namespace) -> int:
    bump_type = args.type
    area = args.area
    summary = args.message
    breaking = args.breaking

    if not bump_type:
        print("Select semver bump type:")
        for idx, t in enumerate(VALID_TYPES, 1):
            print(f"  {idx}) {t}")
        choice = input("Enter choice [1-3, default 1 (patch)]: ").strip() or "1"
        bump_type = (
            VALID_TYPES[int(choice) - 1] if choice in ("1", "2", "3") else "patch"
        )

    if not area:
        print(f"Select area ({', '.join(VALID_AREAS)}):")
        area = input("Enter area [default: gateway]: ").strip() or "gateway"

    if not summary:
        summary = input("Enter summary description: ").strip()
        if not summary:
            print("Error: summary cannot be empty.", file=sys.stderr)
            return 1

    path = create_changeset(
        bump_type=bump_type, area=area, summary=summary, breaking=breaking
    )
    print(f"Created changeset: {path.relative_to(ROOT)}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    current = get_current_version()
    changesets = load_pending_changesets()
    next_ver = calculate_next_version(current, changesets)

    print(f"Current version: v{current}")
    print(f"Pending changesets: {len(changesets)}")
    for cs in changesets:
        breaking_flag = " [BREAKING]" if cs.breaking else ""
        print(
            f"  - ({cs.bump_type}) [{cs.area}]{breaking_flag}: {cs.summary} ({cs.path.name})"
        )

    if changesets:
        print(f"Next computed version: v{next_ver}")
    else:
        print(
            "No pending changesets. Run `python scripts/changeset.py add` to create one."
        )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    ok = True
    # 1. Validate changesets syntax
    changesets = []
    if CHANGESETS_DIR.exists():
        for file_path in sorted(CHANGESETS_DIR.glob("*.md")):
            if file_path.name in ("README.md", ".gitkeep"):
                continue
            try:
                cs = parse_changeset_file(file_path)
                if cs:
                    changesets.append(cs)
            except Exception as e:
                print(
                    f"[changeset:check] Error in {file_path.name}: {e}", file=sys.stderr
                )
                ok = False

    # 2. Check monorepo version synchronization
    from scripts.check_versions import main as check_versions_main

    if check_versions_main() != 0:
        print(
            "[changeset:check] Version synchronization check failed.", file=sys.stderr
        )
        ok = False

    if ok:
        print(
            f"[changeset:check] Validated {len(changesets)} pending changeset(s); versions in sync."
        )
        return 0
    return 1


def cmd_bump(args: argparse.Namespace) -> int:
    current = get_current_version()
    changesets = load_pending_changesets()

    if not changesets and not args.version:
        print(
            "No pending changesets to bump. Specify --version X.Y.Z to force a bump.",
            file=sys.stderr,
        )
        return 1

    next_ver = args.version or calculate_next_version(current, changesets)
    apply_version_bump(next_ver, changesets, dry_run=args.dry_run)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Echo Gate Changeset & Semver Tool")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # add
    p_add = subparsers.add_parser("add", help="Create a new changeset")
    p_add.add_argument(
        "-t", "--type", choices=VALID_TYPES, help="Bump type (patch, minor, major)"
    )
    p_add.add_argument(
        "-a", "--area", choices=VALID_AREAS, help="Area of change (gateway, web, etc.)"
    )
    p_add.add_argument("-m", "--message", help="Summary message describing the change")
    p_add.add_argument(
        "--breaking", action="store_true", help="Mark as breaking change"
    )

    # status
    subparsers.add_parser("status", help="Show current version and pending changesets")

    # check
    subparsers.add_parser("check", help="Validate pending changesets and version sync")

    # bump
    p_bump = subparsers.add_parser(
        "bump", help="Bump version and update changelog from changesets"
    )
    p_bump.add_argument("-v", "--version", help="Explicit target version override")
    p_bump.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview version bump without writing files",
    )

    args = parser.parse_args()
    if not args.command:
        return cmd_status(args)

    if args.command == "add":
        return cmd_add(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "check":
        return cmd_check(args)
    elif args.command == "bump":
        return cmd_bump(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
