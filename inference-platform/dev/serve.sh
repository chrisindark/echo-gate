#!/usr/bin/env bash
# Run the native inference server on the laptop.
#
#   ./dev/serve.sh                        # auto device (mps if available)
#   INFERENCE_DEVICE=cpu ./dev/serve.sh
#   INFERENCE_ROLES=embed,sparse ./dev/serve.sh
#
# Deliberately runs on the host rather than in a container: a Linux container on
# macOS has no Metal, so containerizing this would cost the GPU for no gain.
#
# The package is its own dependency root (uv), independent of the gateway's
# Poetry environment. Run `uv sync` once inside this directory first.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

exec uv run --frozen inference-platform
