#!/usr/bin/env bash
# dev.sh - Single-command development runner

cleanup() {
  echo "Stopping services..."
  kill $(jobs -p) 2>/dev/null
  exit 0
}
trap cleanup SIGINT SIGTERM

echo "Starting EchoGate Backend..."
PYTHONPATH=api poetry run uvicorn app.main:app --port 8000 --reload &

echo "Starting Next.js UI..."
(cd web && npm run dev) &

wait
