"""Audit Python dependencies for known vulnerabilities, enforcing strict gating.

Transitive dependencies with known upstream version constraints that do not affect
Echo Gate's attack surface (e.g. Pillow in fastembed, where only BM25 text embedding
is used and image decoding is never invoked) are explicitly tracked and logged.
Any other package vulnerability will immediately fail this script with exit code 1.
"""

import json
import subprocess
import sys

# Tracked upstream transitive packages where Echo Gate does not use the affected code paths.
EXEMPT_PACKAGES = {
    "pillow": (
        "Transitive dependency pinned by fastembed 0.8.1 (<13.0). "
        "Echo Gate uses fastembed exclusively for SparseTextEmbedding (BM25 tokenization); "
        "image decoding / ImageEmbedding is never used."
    )
}


def main() -> int:
    cmd = [sys.executable, "-m", "pip_audit", "--format=json"]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        # If output is not JSON, pip-audit might have errored before starting
        sys.stderr.write(proc.stderr or proc.stdout)
        return proc.returncode

    dependencies = data.get("dependencies", [])
    violations = []
    exemptions_seen = []

    for dep in dependencies:
        name = dep.get("name", "").lower()
        vulns = dep.get("vulns", [])
        if not vulns:
            continue

        if name in EXEMPT_PACKAGES:
            exemptions_seen.append(
                (name, dep.get("version"), len(vulns), EXEMPT_PACKAGES[name])
            )
        else:
            violations.append((name, dep.get("version"), vulns))

    if exemptions_seen:
        for name, version, count, reason in exemptions_seen:
            print(
                f"[audit:exempt] {name}=={version} has {count} vulnerability advisories.\n"
                f"               Reason: {reason}\n"
            )

    if violations:
        print("[audit:FAILURE] Found vulnerabilities in non-exempt dependencies:")
        for name, version, vulns in violations:
            for v in vulns:
                vid = v.get("id")
                desc = v.get("description", "").split("\n")[0]
                fix = ", ".join(v.get("fix_versions", [])) or "None"
                print(f"  - {name}=={version} [{vid}] (Fix: {fix}): {desc[:100]}")
        return 1

    print("[audit:SUCCESS] All dependencies passed vulnerability audit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
