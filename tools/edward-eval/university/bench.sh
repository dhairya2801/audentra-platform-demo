#!/usr/bin/env bash
# Staff Edward university benchmark — one command to repeat after any change.
#
#   tools/edward-eval/university/bench.sh [batch-name] [-- extra run.mjs flags]
#
# What it does:
#   1. freezes the live tenant DB into a snapshot (vv_enrollment_staff_eval),
#      so the dev worker cannot move ground truth mid-run;
#   2. starts the worktree API against that snapshot on :45710 (trace debug on,
#      demo staff headers allowed, gpt-4o-mini via OPENAI_API_KEY);
#   3. extracts deterministic ground truth with SQL (+ the Explorer slot oracle);
#   4. runs the 144-case suite and writes artifacts/runs/<batch>/{report.md,
#      summary.json, transcript.json}.
#
# Prerequisites: docker compose stack up (postgres :55432), the mock university
# deployed into tenant aster-demo (`npm run audentra:deploy` in
# Audentra-university-explorer), `uv`, node >= 22, OPENAI_API_KEY in the env.
#
# Env overrides: SKIP_SNAPSHOT=1 (reuse the existing snapshot), API_PORT,
# EXPLORER_DATABASE_URL, OPENAI_MODEL, KEEP_API=1 (leave the API running).

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
BATCH="${1:-univ-$(date +%Y%m%d-%H%M)}"
shift || true
if [[ "${1:-}" == "--" ]]; then shift; fi
PORT="${API_PORT:-45710}"
SNAPSHOT_DB="vv_enrollment_staff_eval"
export DATABASE_URL="postgresql://vv:vv_local_password@127.0.0.1:55432/${SNAPSHOT_DB}"
export DEMO_TENANT_ID="${DEMO_TENANT_ID:-00000000-0000-7000-8000-000000000003}"
export OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}"
export ASSISTANT_TRACE_DEBUG_ENABLED=true
export BROWSER_AUTH_REQUIRED=false
export AUTH_MODE=demo
export API_PORT="$PORT"
export STAFF_EVAL_BASE_URL="http://127.0.0.1:${PORT}"

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is not set (Edward's planner/composer need it)" >&2
  exit 2
fi

if [[ "${SKIP_SNAPSHOT:-0}" != "1" ]]; then
  echo "▸ snapshotting vv_enrollment → ${SNAPSHOT_DB}"
  docker exec audentra-platform-postgres-1 sh -c \
    "psql -q -U vv -d postgres -c 'DROP DATABASE IF EXISTS ${SNAPSHOT_DB}' \
     && psql -q -U vv -d postgres -c 'CREATE DATABASE ${SNAPSHOT_DB} OWNER vv' \
     && pg_dump -U vv vv_enrollment | psql -q -U vv -d ${SNAPSHOT_DB}" >/dev/null
fi

echo "▸ starting API on :${PORT} against the snapshot"
for pid in $(ss -ltnp 2>/dev/null | grep ":${PORT}" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
  kill "$pid" 2>/dev/null || true
done
sleep 1
LOG="${ROOT}/artifacts/university-eval/api-${BATCH}.log"
mkdir -p "${ROOT}/artifacts/university-eval"
( cd "$ROOT" && nohup uv run --directory apps/api --locked audentra-api > "$LOG" 2>&1 & )
for _ in $(seq 1 40); do
  sleep 1
  curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null && break
done
curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null || { echo "API did not start; see $LOG" >&2; exit 1; }

echo "▸ extracting ground truth"
( cd "$ROOT" && uv run --directory apps/api --locked python "$HERE/ground_truth.py" \
    > artifacts/university-eval/ground-truth.json )

echo "▸ running suite → artifacts/runs/${BATCH}"
( cd "$ROOT" && node "$HERE/run.mjs" --batch "$BATCH" "$@" )

if [[ "${KEEP_API:-0}" != "1" ]]; then
  for pid in $(ss -ltnp 2>/dev/null | grep ":${PORT}" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
    kill "$pid" 2>/dev/null || true
  done
fi
echo "▸ done: ${ROOT}/artifacts/runs/${BATCH}/report.md"
