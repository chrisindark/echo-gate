#!/usr/bin/env bash
# dev.sh - Single-command development runner

cleanup() {
  echo "Stopping services..."
  kill $(jobs -p) 2>/dev/null
  exit 0
}
trap cleanup SIGINT SIGTERM

# The gateway has no in-process models: dense embeddings, reranking, relevance and
# NLI come from inference-platform over HTTP, and startup fails without it. Start
# it first so the API does not race the engine.
if [[ -x inference-platform/.venv/bin/python ]]; then
  echo "Starting inference platform (http://localhost:7997)..."
  (cd inference-platform && ./dev/serve.sh) &
else
  echo "WARNING: inference-platform/.venv is missing; the API will refuse to boot."
  echo "         cd inference-platform && python3.11 -m venv .venv \\"
  echo "           && .venv/bin/pip install -r dev/requirements.txt"
fi

echo "Starting EchoGate Backend..."
PYTHONPATH=api poetry run uvicorn app.main:app --port 8000 --reload &

echo "Starting Next.js UI..."
(cd web && npm run dev) &

wait
