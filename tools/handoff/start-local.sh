#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a
source .env.handoff
set +a
export OBJECT_STORAGE_ENDPOINT="http://127.0.0.1:${HANDOFF_STORAGE_PORT}"
export OBJECT_STORAGE_REGION=us-east-1 OBJECT_STORAGE_BUCKET="$MINIO_BUCKET"
export OBJECT_STORAGE_ACCESS_KEY="$MINIO_ROOT_USER" OBJECT_STORAGE_SECRET_KEY="$MINIO_ROOT_PASSWORD"
export OBJECT_STORAGE_FORCE_PATH_STYLE=true PYTHONPATH=apps/api/src
provider=--disable-openai
if [[ -n "${OPENAI_API_KEY:-}" ]]; then provider=--enable-openai; fi
exec apps/api/.venv/bin/python tools/university/run_demo_excellence.py \
  --database-url "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${HANDOFF_POSTGRES_PORT}/${POSTGRES_DB}" \
  --port 4000 --portal-origin http://localhost:3000 "$provider"
