#!/usr/bin/env bash
# dev.sh - Single-command development runner

set -o pipefail

INFERENCE_URL="http://127.0.0.1:7997"
INFERENCE_HEALTH_URL="${INFERENCE_URL}/health"
INFERENCE_TIMEOUT=120

cleanup() {
  echo "Stopping services..."
  trap - SIGINT SIGTERM EXIT

  # Stop all tracked background jobs.
  jobs -pr | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true

  exit 0
}
trap cleanup SIGINT SIGTERM

# The gateway has no in-process models: dense embeddings, reranking, relevance and
# NLI come from inference-platform over HTTP, and startup fails without it. Start
# it first so the API does not race the engine.
if [[ ! -x inference-platform/.venv/bin/python ]]; then
    echo "ERROR: inference-platform/.venv is missing."
    echo "Run: cd inference-platform && uv sync"
    exit 1
fi

echo "Starting inference platform (${INFERENCE_URL})..."
(cd inference-platform && exec ./dev/serve.sh) &
INFERENCE_PID=$!

# Wait for inference to become healthy.
echo "Waiting for inference platform to become ready..."

deadline=$((SECONDS + INFERENCE_TIMEOUT))

until curl -fsS "$INFERENCE_HEALTH_URL" >/dev/null 2>&1; do
  if ! kill -0 "$INFERENCE_PID" 2>/dev/null; then
    echo "ERROR: Inference platform exited during startup."
    wait "$INFERENCE_PID" || true
    exit 1
  fi

  if (( SECONDS >= deadline )); then
    echo "ERROR: Inference platform did not become ready within ${INFERENCE_TIMEOUT}s."
    exit 1
  fi

  sleep 1
done

echo "Inference platform is ready."

# Display existing test coverage report if available
if [[ -f .coverage ]]; then
  echo ""
  echo "==================== CURRENT TEST COVERAGE ===================="
  PYTHONPATH=api poetry run coverage report
  PYTHONPATH=api poetry run coverage html 2>/dev/null || true
  echo "==============================================================="
  echo "Coverage UI available at: http://127.0.0.1:8000/coverage"
  echo "==============================================================="
  echo ""
fi

# Only start FastAPI after inference is ready.
echo "Starting EchoGate Backend..."
PYTHONPATH=api poetry run uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload &
API_PID=$!

# Start frontend.
echo "Starting Next.js UI..."
(cd web && npm run dev) &
WEB_PID=$!

wait
