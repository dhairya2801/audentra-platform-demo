#!/usr/bin/env bash
# Deploy the already-built immutable image into the private Vekend preview.
# Infrastructure and Secret Manager values are provisioned separately; this
# script deliberately never receives, prints, or stores secret values.

set -euo pipefail

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID is required}"
: "${GCP_REGION:?GCP_REGION is required}"
: "${IMAGE_URI:?IMAGE_URI is required}"
: "${RUNTIME_SERVICE_ACCOUNT:?RUNTIME_SERVICE_ACCOUNT is required}"
: "${SCHEDULER_SERVICE_ACCOUNT:?SCHEDULER_SERVICE_ACCOUNT is required}"
: "${CLOUD_SQL_INSTANCE:?CLOUD_SQL_INSTANCE is required}"
: "${GCS_BUCKET:?GCS_BUCKET is required}"
: "${DATABASE_URL_SECRET:?DATABASE_URL_SECRET is required}"
: "${DOCUMENT_WORKER_TOKEN_SECRET:?DOCUMENT_WORKER_TOKEN_SECRET is required}"
: "${STAFF_INVITATION_CODE_SECRET:?STAFF_INVITATION_CODE_SECRET is required}"
: "${GCP_SERVICE_ACCOUNT:?GCP_SERVICE_ACCOUNT is required}"

OPENROUTER_API_KEY_SECRET="${OPENROUTER_API_KEY_SECRET:-}"
WEB_ORIGIN="${WEB_ORIGIN:-http://localhost:3000}"

API_SERVICE="${API_SERVICE:-audentra-api-preview}"
MIGRATION_JOB="${MIGRATION_JOB:-audentra-migrate-preview}"
SEED_JOB="${SEED_JOB:-audentra-seed-preview}"
WORKER_JOB="${WORKER_JOB:-audentra-worker-once-preview}"
SCHEDULER_JOB="${SCHEDULER_JOB:-audentra-worker-dispatch-preview}"

runtime_secrets="DATABASE_URL=${DATABASE_URL_SECRET}:latest"
runtime_secrets+=",DOCUMENT_WORKER_TOKEN=${DOCUMENT_WORKER_TOKEN_SECRET}:latest"
runtime_secrets+=",VV_STAFF_INVITATION_CODE=${STAFF_INVITATION_CODE_SECRET}:latest"
if [[ -n "${OPENROUTER_API_KEY_SECRET}" ]]; then
  runtime_secrets+=",OPENROUTER_API_KEY=${OPENROUTER_API_KEY_SECRET}:latest"
fi

runtime_environment="AUDENTRA_ENV=preview,AUTH_MODE=demo,WEB_ORIGIN=${WEB_ORIGIN}"
runtime_environment+=",OBJECT_STORAGE_PROVIDER=gcs,OBJECT_STORAGE_BUCKET=${GCS_BUCKET}"
runtime_environment+=",GOOGLE_CLOUD_PROJECT=${GCP_PROJECT_ID},DB_POOL_SIZE=2,DB_MAX_OVERFLOW=0"
runtime_environment+=",WORKER_BATCH_SIZE=5,WORKER_COMMAND_TIMEOUT_SECONDS=180"
runtime_environment+=",WORKER_LEASE_SECONDS=300"

job_network_options=(
  --set-cloudsql-instances="${CLOUD_SQL_INSTANCE}"
  --service-account="${RUNTIME_SERVICE_ACCOUNT}"
  --region="${GCP_REGION}"
  --project="${GCP_PROJECT_ID}"
  --tasks=1
  --parallelism=1
  --max-retries=1
  --task-timeout=900s
  --cpu=1
  --memory=512Mi
  --quiet
)

gcloud run jobs deploy "${MIGRATION_JOB}" \
  --image="${IMAGE_URI}" \
  --command=audentra-migrate \
  --args=--migrations-dir=migrations \
  --set-secrets="DATABASE_URL=${DATABASE_URL_SECRET}:latest" \
  "${job_network_options[@]}"
gcloud run jobs execute "${MIGRATION_JOB}" \
  --region="${GCP_REGION}" --project="${GCP_PROJECT_ID}" --wait --quiet

gcloud run jobs deploy "${SEED_JOB}" \
  --image="${IMAGE_URI}" \
  --command=audentra-seed \
  --args=--all \
  --set-env-vars="${runtime_environment}" \
  --set-secrets="${runtime_secrets}" \
  "${job_network_options[@]}"
gcloud run jobs execute "${SEED_JOB}" \
  --region="${GCP_REGION}" --project="${GCP_PROJECT_ID}" --wait --quiet

gcloud run deploy "${API_SERVICE}" \
  --image="${IMAGE_URI}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT_ID}" \
  --service-account="${RUNTIME_SERVICE_ACCOUNT}" \
  --add-cloudsql-instances="${CLOUD_SQL_INSTANCE}" \
  --port=8080 \
  --cpu=1 \
  --memory=512Mi \
  --concurrency=20 \
  --min-instances=0 \
  --max-instances=3 \
  --timeout=300s \
  --ingress=all \
  --no-allow-unauthenticated \
  --set-env-vars="${runtime_environment},DB_APPLICATION_NAME=audentra-cloud-run" \
  --set-secrets="${runtime_secrets}" \
  --quiet

api_url="$(gcloud run services describe "${API_SERVICE}" \
  --region="${GCP_REGION}" --project="${GCP_PROJECT_ID}" --format='value(status.url)')"
test -n "${api_url}"

gcloud run services add-iam-policy-binding "${API_SERVICE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SERVICE_ACCOUNT}" \
  --role=roles/run.invoker \
  --quiet

gcloud run services add-iam-policy-binding "${API_SERVICE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT_ID}" \
  --member="serviceAccount:${GCP_SERVICE_ACCOUNT}" \
  --role=roles/run.invoker \
  --quiet

identity_token="$(gcloud auth print-identity-token --audiences="${api_url}")"
for attempt in {1..12}; do
  if curl --fail --silent --show-error \
    --header="Authorization: Bearer ${identity_token}" \
    "${api_url}/health/ready" >/dev/null; then
    break
  fi
  if [[ "${attempt}" -eq 12 ]]; then
    echo "Authenticated Cloud Run readiness check failed." >&2
    exit 1
  fi
  sleep 5
done

gcloud run jobs deploy "${WORKER_JOB}" \
  --image="${IMAGE_URI}" \
  --command=audentra-worker \
  --args=--once \
  --set-env-vars="${runtime_environment},DB_APPLICATION_NAME=audentra-worker,API_INTERNAL_URL=${api_url},API_INTERNAL_AUDIENCE=${api_url}" \
  --set-secrets="${runtime_secrets}" \
  "${job_network_options[@]}"
gcloud run jobs execute "${WORKER_JOB}" \
  --region="${GCP_REGION}" --project="${GCP_PROJECT_ID}" --wait --quiet

gcloud run jobs add-iam-policy-binding "${WORKER_JOB}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT_ID}" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT}" \
  --role=roles/run.invoker \
  --quiet

scheduler_uri="https://run.googleapis.com/v2/projects/${GCP_PROJECT_ID}/locations/${GCP_REGION}/jobs/${WORKER_JOB}:run"
scheduler_args=(
  --location="${GCP_REGION}"
  --project="${GCP_PROJECT_ID}"
  --schedule="*/5 * * * *"
  --time-zone=Etc/UTC
  --uri="${scheduler_uri}"
  --http-method=POST
  --oauth-service-account-email="${SCHEDULER_SERVICE_ACCOUNT}"
  --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform
  --message-body='{}'
  --attempt-deadline=30s
  --quiet
)
if gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
  --location="${GCP_REGION}" --project="${GCP_PROJECT_ID}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${SCHEDULER_JOB}" "${scheduler_args[@]}"
else
  gcloud scheduler jobs create http "${SCHEDULER_JOB}" "${scheduler_args[@]}"
fi

echo "Cloud Run preview deployed: ${api_url}"
