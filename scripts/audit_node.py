"""Audit Node dependencies for known vulnerabilities, enforcing strict gating.

Mirrors ``scripts/audit_python.py``: advisories that cannot be resolved from this
repository are tracked explicitly with the reason, and every other high or
critical advisory fails this script with exit code 1. Moderate and low advisories
do not gate, matching the previous ``npm audit --audit-level=high`` behaviour.

The web package is a build-time toolchain, so advisories are keyed by GHSA id
rather than package name: one advisory typically surfaces through many
transitive packages, and a new advisory on the same package must still fail.
"""

import json
import subprocess
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Advisories with no patched release published upstream. Remove an entry once a
# fix ships; the script reports stale entries so they do not linger.
EXEMPT_ADVISORIES = {
    "GHSA-fc5q-39xc-vjpf": (
        "picomatch <= 4.0.7 ReDoS; upstream has published no patched version. "
        "Reached only through the web build toolchain (eslint, glob, lint-staged, "
        "shadcn). The shipped Next.js bundle never calls picomatch, and no "
        "attacker-controlled glob pattern reaches it during a build."
    )
}

GATING_SEVERITIES = {"high", "critical"}


def collect_advisories(data: dict) -> dict[str, dict]:
    """Map advisory id -> details, walking npm's per-package vulnerability tree."""
    advisories: dict[str, dict] = {}
    for package_name, entry in data.get("vulnerabilities", {}).items():
        for via in entry.get("via", []):
            if not isinstance(via, dict):
                continue  # a string is a transitive dependency link, not an advisory
            url = via.get("url") or ""
            advisory_id = url.rstrip("/").rsplit("/", 1)[-1] or via.get("source")
            if not advisory_id:
                continue
            record = advisories.setdefault(
                advisory_id,
                {
                    "id": advisory_id,
                    "title": via.get("title", ""),
                    "severity": via.get("severity", "unknown"),
                    "range": via.get("range", ""),
                    "packages": set(),
                },
            )
            record["packages"].add(package_name)
    return advisories


def main() -> int:
    proc = subprocess.run(
        ["npm", "audit", "--json"],
        cwd=WEB_DIR,
        capture_output=True,
        text=True,
    )

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        # npm failed before producing a report (missing lockfile, registry
        # outage, npm not installed). Never report that as a clean audit.
        sys.stderr.write(proc.stderr or proc.stdout or "npm audit produced no output\n")
        return proc.returncode or 1

    if data.get("error"):
        sys.stderr.write(json.dumps(data["error"], indent=2) + "\n")
        return 1

    advisories = collect_advisories(data)
    exempted = []
    violations = []
    for advisory_id, advisory in sorted(advisories.items()):
        if advisory["severity"] not in GATING_SEVERITIES:
            continue
        if advisory_id in EXEMPT_ADVISORIES:
            exempted.append(advisory)
        else:
            violations.append(advisory)

    for advisory in exempted:
        print(
            f"[audit:exempt] {advisory['id']} ({advisory['severity']}): "
            f"{advisory['title']}\n"
            f"               Packages: {', '.join(sorted(advisory['packages']))}\n"
            f"               Reason: {EXEMPT_ADVISORIES[advisory['id']]}\n"
        )

    stale = set(EXEMPT_ADVISORIES) - {advisory["id"] for advisory in exempted}
    for advisory_id in sorted(stale):
        print(
            f"[audit:stale] {advisory_id} is exempted but no longer reported; "
            "remove it from EXEMPT_ADVISORIES."
        )

    if violations:
        print("[audit:FAILURE] Found vulnerabilities in non-exempt dependencies:")
        for advisory in violations:
            print(
                f"  - {advisory['id']} ({advisory['severity']}): "
                f"{advisory['title']}\n"
                f"    Packages: {', '.join(sorted(advisory['packages']))} "
                f"(affected range {advisory['range'] or 'unknown'})"
            )
        return 1

    print("[audit:SUCCESS] All Node dependencies passed vulnerability audit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
