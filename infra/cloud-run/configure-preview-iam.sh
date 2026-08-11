#!/usr/bin/env bash
# One-time IAM/WIF setup for the Vekend-owned synthetic preview.
# Run only as a Vekend Owner or IAM administrator after reviewing every binding.

set -euo pipefail

project_id="${GCP_PROJECT_ID:-audentra}"
region="${GCP_REGION:-us-central1}"
pool_id="${WORKLOAD_IDENTITY_POOL_ID:-github-audentra}"
provider_id="${WORKLOAD_IDENTITY_PROVIDER_ID:-github}"
github_owner_id="${GITHUB_OWNER_ID:-310706677}"
github_repository_id="${GITHUB_REPOSITORY_ID:-1318439118}"
github_ref="refs/heads/main"
github_environment="preview"

cd_service_account="audentra-platform-cd@${project_id}.iam.gserviceaccount.com"
runtime_service_account="audentra-platform-runtime@${project_id}.iam.gserviceaccount.com"
scheduler_service_account="audentra-platform-scheduler@${project_id}.iam.gserviceaccount.com"
artifact_repository="audentra-platform"
bucket="audentra-906906351296-preview-documents"
cloud_sql_connection="${project_id}:${region}:audentra-preview-postgres"

project_number="$(gcloud projects describe "${project_id}" --format='value(projectNumber)')"
test -n "${project_number}"

for service_account in \
  "${cd_service_account}" \
  "${runtime_service_account}" \
  "${scheduler_service_account}"; do
  gcloud iam service-accounts describe "${service_account}" \
    --project="${project_id}" >/dev/null
done

if ! gcloud iam workload-identity-pools describe "${pool_id}" \
  --project="${project_id}" --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${pool_id}" \
    --project="${project_id}" \
    --location=global \
    --display-name="Audentra GitHub Actions"
fi

attribute_mapping="google.subject=assertion.sub"
attribute_mapping+=",attribute.repository_id=assertion.repository_id"
attribute_mapping+=",attribute.repository_owner_id=assertion.repository_owner_id"
attribute_mapping+=",attribute.ref=assertion.ref"
attribute_mapping+=",attribute.environment=assertion.environment"
attribute_condition="assertion.repository_owner_id == '${github_owner_id}'"
attribute_condition+=" && assertion.repository_id == '${github_repository_id}'"
attribute_condition+=" && assertion.ref == '${github_ref}'"
attribute_condition+=" && assertion.environment == '${github_environment}'"

if ! gcloud iam workload-identity-pools providers describe "${provider_id}" \
  --workload-identity-pool="${pool_id}" \
  --project="${project_id}" --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${provider_id}" \
    --workload-identity-pool="${pool_id}" \
    --project="${project_id}" \
    --location=global \
    --display-name="Audentra platform main" \
    --issuer-uri=https://token.actions.githubusercontent.com \
    --attribute-mapping="${attribute_mapping}" \
    --attribute-condition="${attribute_condition}"
fi

gcloud projects add-iam-policy-binding "${project_id}" \
  --member="serviceAccount:${cd_service_account}" \
  --role=roles/run.admin --condition=None --quiet
gcloud projects add-iam-policy-binding "${project_id}" \
  --member="serviceAccount:${cd_service_account}" \
  --role=roles/cloudscheduler.admin --condition=None --quiet
gcloud projects add-iam-policy-binding "${project_id}" \
  --member="serviceAccount:${runtime_service_account}" \
  --role=roles/cloudsql.client --condition=None --quiet

gcloud artifacts repositories add-iam-policy-binding "${artifact_repository}" \
  --project="${project_id}" --location="${region}" \
  --member="serviceAccount:${cd_service_account}" \
  --role=roles/artifactregistry.writer --quiet

for service_account in "${runtime_service_account}" "${scheduler_service_account}"; do
  gcloud iam service-accounts add-iam-policy-binding "${service_account}" \
    --project="${project_id}" \
    --member="serviceAccount:${cd_service_account}" \
    --role=roles/iam.serviceAccountUser --quiet
done

principal_set="principalSet://iam.googleapis.com/projects/${project_number}"
principal_set+="/locations/global/workloadIdentityPools/${pool_id}"
principal_set+="/attribute.repository_id/${github_repository_id}"
gcloud iam service-accounts add-iam-policy-binding "${cd_service_account}" \
  --project="${project_id}" \
  --member="${principal_set}" \
  --role=roles/iam.workloadIdentityUser --quiet

required_secrets=(
  audentra-preview-database-url
  audentra-preview-document-worker-token
  audentra-preview-staff-invitation-code
)
for secret in "${required_secrets[@]}"; do
  enabled_versions="$(gcloud secrets versions list \
    --secret="${secret}" --project="${project_id}" \
    --filter='state=ENABLED' --format='value(name)' --limit=1)"
  test -n "${enabled_versions}"
  gcloud secrets add-iam-policy-binding "${secret}" \
    --project="${project_id}" \
    --member="serviceAccount:${runtime_service_account}" \
    --role=roles/secretmanager.secretAccessor --quiet
done

if gcloud secrets describe audentra-preview-openrouter-api-key \
  --project="${project_id}" >/dev/null 2>&1; then
  gcloud secrets add-iam-policy-binding audentra-preview-openrouter-api-key \
    --project="${project_id}" \
    --member="serviceAccount:${runtime_service_account}" \
    --role=roles/secretmanager.secretAccessor --quiet
fi

gcloud storage buckets add-iam-policy-binding "gs://${bucket}" \
  --project="${project_id}" \
  --member="serviceAccount:${runtime_service_account}" \
  --role=roles/storage.objectUser --quiet

# Validate only the connection shape. Never print the database secret.
database_url="$(gcloud secrets versions access latest \
  --secret=audentra-preview-database-url --project="${project_id}")"
if [[ "${database_url}" != *"/cloudsql/${cloud_sql_connection}"* ]]; then
  echo "The database URL does not reference /cloudsql/${cloud_sql_connection}." >&2
  exit 1
fi
unset database_url

echo "Vekend preview IAM and GitHub federation are configured."
