# Echo Gate Changesets

This directory holds modular, pending changeset records for upcoming Echo Gate releases.

## Why Changesets?

Changesets provide a decentralized, pull-request-friendly way to declare version bumps and changelog notes alongside your code changes without git conflicts in `CHANGELOG.md` or version files.

## Adding a Changeset

Run the CLI tool:

```bash
# Non-interactive:
python scripts/changeset.py add --type minor --area gateway --message "Add natural streaming playback for cache hits"

# Interactive:
python scripts/changeset.py add
```

### Options:
- `--type`: Semver bump level (`patch`, `minor`, `major`).
- `--area`: Affected system component (`gateway`, `web`, `inference`, `docs`, `ci`, `deps`, `core`).
- `--breaking`: Flag to indicate a breaking change requiring a major version bump.
- `--message` / `-m`: Summary of the change for `CHANGELOG.md`.

## Checking Status & Release

```bash
# View pending changesets and computed next version
python scripts/changeset.py status

# Validate all changeset files and monorepo version synchronization
python scripts/changeset.py check

# Apply release bump (updates pyproject.toml, web/package.json, inference-platform/pyproject.toml, and CHANGELOG.md)
python scripts/changeset.py bump
```
