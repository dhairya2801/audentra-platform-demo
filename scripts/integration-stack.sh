#!/usr/bin/env bash
# Run the integration API + worker from this worktree against the mock
# university (tenant aster-demo), reusing the docker compose infrastructure
# (Postgres :55432, MinIO :9000, Keycloak :8080, Mailpit :1025) that
# `docker compose -f infra/compose.yaml up` already provides.
#
#   scripts/integration-stack.sh api      # API on :4300 (foreground)
#   scripts/integration-stack.sh worker   # worker (foreground)
#   scripts/integration-stack.sh env      # print the environment and exit
#
# The compose `api`/`worker` containers on :4000 run the image they were built
# from, not this worktree; stop them while manually testing this branch so
# only one worker touches the tenant:
#   (cd infra && docker compose stop api worker)
#
# Overrides: API_PORT (4300), POSTGRES_PORT (55432), DEMO_TENANT_ID
# (aster-demo), OPENAI_MODEL (gpt-4o-mini). OPENAI_API_KEY must be exported —
# Edward's planner and composer need it.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AUDENTRA_ENV="${AUDENTRA_ENV:-development}"
export AUTH_MODE="${AUTH_MODE:-demo}"
export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${API_PORT:-4300}"
export WEB_ORIGIN="${WEB_ORIGIN:-http://localhost:3000}"
export API_PUBLIC_URL="${API_PUBLIC_URL:-http://localhost:${API_PORT}}"
export API_INTERNAL_URL="${API_INTERNAL_URL:-http://localhost:${API_PORT}}"
export DATABASE_URL="${DATABASE_URL:-postgresql://vv:vv_local_password@127.0.0.1:${POSTGRES_PORT:-55432}/vv_enrollment}"
export OBJECT_STORAGE_PROVIDER="${OBJECT_STORAGE_PROVIDER:-s3}"
export OBJECT_STORAGE_ENDPOINT="${OBJECT_STORAGE_ENDPOINT:-http://localhost:9000}"
export OBJECT_STORAGE_REGION="${OBJECT_STORAGE_REGION:-us-east-1}"
export OBJECT_STORAGE_BUCKET="${OBJECT_STORAGE_BUCKET:-vv-documents}"
export OBJECT_STORAGE_ACCESS_KEY="${OBJECT_STORAGE_ACCESS_KEY:-vv_minio}"
export OBJECT_STORAGE_SECRET_KEY="${OBJECT_STORAGE_SECRET_KEY:-vv_minio_password}"
export OBJECT_STORAGE_FORCE_PATH_STYLE="${OBJECT_STORAGE_FORCE_PATH_STYLE:-true}"
# The mock university. `aster` (…0001) is the 14-student compact fixture.
export DEMO_TENANT_ID="${DEMO_TENANT_ID:-00000000-0000-7000-8000-000000000003}"
export DOCUMENT_WORKER_TOKEN="${DOCUMENT_WORKER_TOKEN:-local-development-document-worker-token}"
export FERPA_DELEGATE_LINK_SECRET="${FERPA_DELEGATE_LINK_SECRET:-local-development-ferpa-delegate-link-secret}"
export VV_STAFF_INVITATION_CODE="${VV_STAFF_INVITATION_CODE:-local-staff-invitation-2027}"
export BROWSER_AUTH_REQUIRED="${BROWSER_AUTH_REQUIRED:-false}"
export ASSISTANT_TRACE_DEBUG_ENABLED="${ASSISTANT_TRACE_DEBUG_ENABLED:-true}"
export OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}"
export WORKER_ID="${WORKER_ID:-integration-worker}"

case "${1:-api}" in
  env) env | grep -E '^(AUDENTRA_ENV|AUTH_MODE|API_|WEB_ORIGIN|DATABASE_URL|OBJECT_STORAGE_|DEMO_TENANT_ID|BROWSER_AUTH|ASSISTANT_TRACE|OPENAI_MODEL|WORKER_ID)' | sort ;;
  api)
    [[ -n "${OPENAI_API_KEY:-}" ]] || echo "warning: OPENAI_API_KEY is not set; Edward will answer deterministically only" >&2
    exec uv run --directory "$HERE/apps/api" --locked audentra-api ;;
  worker) exec uv run --directory "$HERE/apps/api" --locked audentra-worker ;;
  *) echo "usage: $0 api|worker|env" >&2; exit 2 ;;
esac
