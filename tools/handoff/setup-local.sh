#!/usr/bin/env bash
# Rebuild the synthetic handoff baseline in dedicated local services; never reset data.
set -euo pipefail
cd "$(dirname "$0")/../.."
umask 077
if [[ ! -f .env.handoff ]]; then
  python3 - <<'PY'
from pathlib import Path
import secrets
source = Path('infra/handoff.env.example').read_text()
lines = [line.replace('=CHANGE_ME', '=' + secrets.token_hex(24))
         if line.endswith('=CHANGE_ME') else line for line in source.splitlines()]
Path('.env.handoff').write_text('\n'.join(lines) + '\n')
PY
fi
set -a
source .env.handoff
set +a
[[ "$POSTGRES_DB" == audentra_university_handoff ]] || { echo 'Use the dedicated handoff database.' >&2; exit 1; }
export DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${HANDOFF_POSTGRES_PORT}/${POSTGRES_DB}"
export OBJECT_STORAGE_ENDPOINT="http://127.0.0.1:${HANDOFF_STORAGE_PORT}"
export OBJECT_STORAGE_REGION=us-east-1 OBJECT_STORAGE_BUCKET="$MINIO_BUCKET"
export OBJECT_STORAGE_ACCESS_KEY="$MINIO_ROOT_USER" OBJECT_STORAGE_SECRET_KEY="$MINIO_ROOT_PASSWORD"
export OBJECT_STORAGE_FORCE_PATH_STYLE=true PYTHONPATH=apps/api/src
npm ci
uv sync --directory apps/api --locked --all-groups
POSTGRES_PORT="127.0.0.1:${HANDOFF_POSTGRES_PORT}" \
MINIO_API_PORT="127.0.0.1:${HANDOFF_STORAGE_PORT}" \
MINIO_CONSOLE_PORT="127.0.0.1:${HANDOFF_STORAGE_CONSOLE_PORT}" \
docker compose -p audentra-handoff --env-file .env.handoff -f infra/compose.yaml up -d --wait postgres minio
POSTGRES_PORT="127.0.0.1:${HANDOFF_POSTGRES_PORT}" \
MINIO_API_PORT="127.0.0.1:${HANDOFF_STORAGE_PORT}" \
MINIO_CONSOLE_PORT="127.0.0.1:${HANDOFF_STORAGE_CONSOLE_PORT}" \
docker compose -p audentra-handoff --env-file .env.handoff -f infra/compose.yaml run --rm --no-deps minio-init
npm run db:migrate
python=apps/api/.venv/bin/python
"$python" tools/university/build.py
"$python" tools/university/import_runtime.py --database-url "$DATABASE_URL"
"$python" tools/university/import_product_runtime.py --database-url "$DATABASE_URL" --source artifacts/university-v3/university.sqlite
for fixture in seed_demo_excellence seed_camila_task_board seed_ada_upload_request seed_camila_connected_board seed_ada_financials; do
  "$python" "tools/university/${fixture}.py" --database-url "$DATABASE_URL"
done
printf '%s\n' 'Local synthetic baseline ready. Start with: tools/handoff/start-local.sh'
