#!/usr/bin/env bash
# Run the native inference server on the laptop.
#
#   ./dev/serve.sh              # auto device (mps if available)
#   INFERENCE_DEVICE=cpu ./dev/serve.sh
#
# Deliberately runs on the host rather than in a container: a Linux container on
# macOS has no Metal, so containerizing this would cost the GPU for no gain.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  echo "no .venv yet. run:" >&2
  echo "  python3.11 -m venv .venv && source .venv/bin/activate && pip install -r dev/requirements.txt" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

exec uvicorn native_server:app \
  --app-dir dev \
  --host "${INFERENCE_HOST:-127.0.0.1}" \
  --port "${INFERENCE_PORT:-7997}" \
  --log-level "${INFERENCE_LOG_LEVEL:-info}"
