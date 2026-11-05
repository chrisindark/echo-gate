#!/usr/bin/env bash
# Echo Gate self-hosting bring-up.
#
# One command from a fresh clone to a running gateway with the observability
# UI and the first admin key:
#
#   ./deploy/compose/setup.sh                 # core + ui
#   ./deploy/compose/setup.sh --no-ui         # core only
#   ./deploy/compose/setup.sh --name ops --scopes admin:*   # custom admin key
#
# What it does, in order:
#   1. Starts the inference platform (separate compose project) and waits for
#      /health — the gateway refuses to start without it.
#   2. Brings up the core (+ ui) profile.
#   3. Runs Alembic migrations as the table owner (MIGRATION_DATABASE_URL).
#   4. Creates the first admin key via `python -m app.scripts.create_admin_key create`.
#
# Everything is idempotent: re-running the script starts what is stopped and
# creates another admin key (the CLI, by design, has no "exists" check).
set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${COMPOSE_DIR}/../.." && pwd)"
INFERENCE_COMPOSE="${REPO_ROOT}/inference-platform/deploy/compose/inference-native.yml"
INFERENCE_URL="${INFERENCE_BASE_URL:-http://127.0.0.1:7997}"
INFERENCE_TIMEOUT="${INFERENCE_TIMEOUT:-180}"
COMPOSE="docker compose -f ${COMPOSE_DIR}/docker-compose.yml"
WITH_UI=true
KEY_NAME="${ADMIN_KEY_NAME:-local-admin}"
KEY_SCOPES="${ADMIN_KEY_SCOPES:-admin:*}"
KEY_EXPIRES="${ADMIN_KEY_EXPIRES_DAYS:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-ui) WITH_UI=false ;;
    --name) KEY_NAME="$2"; shift ;;
    --scopes) KEY_SCOPES="$2"; shift ;;
    --expires-in-days) KEY_EXPIRES="$2"; shift ;;
    -h|--help)
      echo "Usage: $0 [--no-ui] [--name NAME] [--scopes SCOPE1,SCOPE2] [--expires-in-days N]"
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "${REPO_ROOT}"

echo "==> [1/4] Starting inference platform"
if [[ ! -f "${INFERENCE_COMPOSE}" ]]; then
  echo "ERROR: inference compose file not found: ${INFERENCE_COMPOSE}" >&2
  exit 1
fi
docker compose -f "${INFERENCE_COMPOSE}" up -d --build

echo "    waiting for ${INFERENCE_URL}/health (up to ${INFERENCE_TIMEOUT}s)..."
deadline=$((SECONDS + INFERENCE_TIMEOUT))
until curl -fsS "${INFERENCE_URL}/health" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "ERROR: inference platform did not become healthy in ${INFERENCE_TIMEOUT}s." >&2
    echo "       docker compose -f ${INFERENCE_COMPOSE} logs -f inference" >&2
    exit 1
  fi
  sleep 2
done
echo "    inference platform is up"

echo "==> [2/4] Starting gateway stack"
PROFILES=(--profile core)
if [[ "${WITH_UI}" == "true" ]]; then
  PROFILES+=(--profile ui)
  echo "    profiles: core, ui"
else
  echo "    profiles: core"
fi
${COMPOSE} "${PROFILES[@]}" up -d --build

echo "==> [3/4] Running database migrations"
# Migrations must run as the table owner: RLS policies do not apply to owners,
# which is exactly why the app itself connects as the non-owner echo_gate_app
# role. psycopg is a gateway dependency, so this runs inside the api image.
${COMPOSE} "${PROFILES[@]}" run --rm \
  -e "PYTHON_ENV=production" \
  -e "MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/echo_gate" \
  echo-gate poetry run alembic upgrade head

echo "==> [4/4] Creating admin API key"
KEY_ARGS=(create --name "${KEY_NAME}" --scopes "${KEY_SCOPES}")
if [[ -n "${KEY_EXPIRES}" ]]; then
  KEY_ARGS+=(--expires-in-days "${KEY_EXPIRES}")
fi
# This is the ONLY way to mint an admin key: there is deliberately no HTTP
# endpoint for it. The plaintext key is printed exactly once by the CLI.
${COMPOSE} "${PROFILES[@]}" run --rm \
  -e "PYTHON_ENV=production" \
  -e "MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/echo_gate" \
  echo-gate poetry run python -m app.scripts.create_admin_key "${KEY_ARGS[@]}"

echo ""
echo "Echo Gate is up."
echo "  Gateway : http://localhost:8000   (health: /api/v1/health/deps)"
if [[ "${WITH_UI}" == "true" ]]; then
  echo "  UI      : http://localhost:3000"
fi
echo ""
echo "Save the admin key printed above — it is shown only once."
echo "Then create a tenant API key:"
echo "  curl -X POST http://localhost:8000/api/v1/admin/api-keys/ \\"
echo "    -H \"Content-Type: application/json\" -H \"X-Admin-Key: <admin key>\" \\"
echo "    -d '{\"name\": \"local-dev\", \"tenant_id\": \"acme\", \"user_id\": \"dev\"}'"
