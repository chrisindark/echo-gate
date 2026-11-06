#!/usr/bin/env bash
# Echo Gate root self-hosting bring-up shortcut.
# Delegates directly to deploy/compose/setup.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/deploy/compose/setup.sh" "$@"
