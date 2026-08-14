#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this bootstrap command through sudo." >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
shared_root="/opt/audentra-platform/shared"
environment_file="${shared_root}/.env"
environment_example="${script_dir}/.env.example"

read_value() {
  local name="$1"
  local value
  value="$(grep -m1 "^${name}=" "$environment_file" 2>/dev/null | cut -d= -f2- || true)"
  value="${value%$'\r'}"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then
    value="${value:1:${#value}-2}"
  elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "$value"
}

configuration_error() {
  echo "Protected preview configuration is invalid or missing: $1" >&2
  echo "Edit ${environment_file}; secret values were not printed." >&2
  exit 78
}

install -d -m 0750 -o root -g root /opt/audentra-platform/releases "$shared_root"

if [[ ! -f "$environment_file" ]]; then
  if [[ ! -f "$environment_example" ]]; then
    echo "Tracked preview environment example is missing: ${environment_example}" >&2
    exit 66
  fi
  install -m 0600 -o root -g root "$environment_example" "$environment_file"
  echo "Created ${environment_file} from the tracked example." >&2
  echo "Replace every CHANGE_ME/example value, then run bootstrap again." >&2
  exit 78
fi

chmod 0600 "$environment_file"
chown root:root "$environment_file"

required_names=(
  AUDENTRA_ENV
  AUTH_MODE
  BROWSER_AUTH_REQUIRED
  API_DOMAIN
  WEB_ORIGIN
  PLATFORM_IMAGE_REPOSITORY
  POSTGRES_DB
  POSTGRES_USER
  POSTGRES_PASSWORD
  MINIO_ROOT_USER
  MINIO_ROOT_PASSWORD
  MINIO_BUCKET
  DOCUMENT_WORKER_TOKEN
  VV_STAFF_INVITATION_CODE
)

for name in "${required_names[@]}"; do
  value="$(read_value "$name")"
  if [[ -z "$value" || "$value" == *CHANGE_ME* || "$value" == *REPLACE_ME* ]]; then
    configuration_error "$name"
  fi
done

[[ "$(read_value AUDENTRA_ENV)" == "preview" ]] || configuration_error AUDENTRA_ENV
[[ "$(read_value AUTH_MODE)" == "demo" ]] || configuration_error AUTH_MODE
[[ "$(read_value BROWSER_AUTH_REQUIRED)" == "true" ]] || \
  configuration_error BROWSER_AUTH_REQUIRED

api_domain="$(read_value API_DOMAIN)"
if [[ ! "$api_domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ || \
      "$api_domain" == *..* || "$api_domain" == *.example.com ]]; then
  configuration_error API_DOMAIN
fi

web_origin="$(read_value WEB_ORIGIN)"
IFS=',' read -r -a web_origins <<<"$web_origin"
for origin in "${web_origins[@]}"; do
  if [[ ! "$origin" =~ ^https://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$ || \
        "$origin" == *..* ]]; then
    configuration_error WEB_ORIGIN
  fi
done

for name in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
  value="$(read_value "$name")"
  [[ "$value" =~ ^[A-Za-z0-9._~-]+$ ]] || configuration_error "$name"
done

[[ ${#api_domain} -le 253 ]] || configuration_error API_DOMAIN
[[ ${#web_origin} -le 1000 ]] || configuration_error WEB_ORIGIN

postgres_password="$(read_value POSTGRES_PASSWORD)"
minio_password="$(read_value MINIO_ROOT_PASSWORD)"
worker_token="$(read_value DOCUMENT_WORKER_TOKEN)"
staff_invitation_code="$(read_value VV_STAFF_INVITATION_CODE)"
[[ ${#postgres_password} -ge 24 ]] || configuration_error POSTGRES_PASSWORD
[[ ${#minio_password} -ge 24 ]] || configuration_error MINIO_ROOT_PASSWORD
[[ ${#worker_token} -ge 32 ]] || configuration_error DOCUMENT_WORKER_TOKEN
[[ ${#staff_invitation_code} -ge 16 ]] || configuration_error VV_STAFF_INVITATION_CODE

image_repository="$(read_value PLATFORM_IMAGE_REPOSITORY)"
if [[ "$image_repository" =~ [[:space:]] || "$image_repository" == */ || \
      "$image_repository" == *: ]]; then
  configuration_error PLATFORM_IMAGE_REPOSITORY
fi

install -m 0755 "${script_dir}/deploy-platform.sh" \
  /usr/local/sbin/audentra-platform-deploy

echo "Audentra preview host configuration is valid; no cloud resources were created."
