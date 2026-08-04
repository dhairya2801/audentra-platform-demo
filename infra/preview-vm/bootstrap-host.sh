#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this bootstrap command through sudo." >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
shared_root="/opt/audentra-platform/shared"
environment_file="${shared_root}/.env"
legacy_environment_file="/opt/vv-edgent/shared/.env"

read_value() {
  local name="$1"
  local file="$2"
  [[ -f "$file" ]] || return 0
  grep -m1 "^${name}=" "$file" 2>/dev/null | cut -d= -f2- || true
}

from_existing_or_legacy() {
  local name="$1"
  local fallback="${2:-}"
  local value
  value="$(read_value "$name" "$environment_file")"
  if [[ -z "$value" ]]; then
    value="$(read_value "$name" "$legacy_environment_file")"
  fi
  printf '%s' "${value:-$fallback}"
}

install -d -m 0750 -o root -g root /opt/audentra-platform/releases "$shared_root"

site_host="$(from_existing_or_legacy SITE_HOST)"
if [[ -z "$site_host" ]]; then
  echo "SITE_HOST is missing from both the current and legacy protected environments." >&2
  exit 78
fi

postgres_password="$(from_existing_or_legacy POSTGRES_PASSWORD)"
minio_password="$(from_existing_or_legacy MINIO_ROOT_PASSWORD)"
worker_token="$(from_existing_or_legacy DOCUMENT_WORKER_TOKEN)"
staff_invitation_code="$(from_existing_or_legacy VV_STAFF_INVITATION_CODE)"
if [[ -z "$staff_invitation_code" ]]; then
  staff_invitation_code="$(from_existing_or_legacy VV_STAFF_BOOTSTRAP_PASSWORD)"
fi

[[ -n "$postgres_password" ]] || postgres_password="$(openssl rand -hex 24)"
[[ -n "$minio_password" ]] || minio_password="$(openssl rand -hex 24)"
[[ -n "$worker_token" ]] || worker_token="$(openssl rand -hex 32)"
[[ -n "$staff_invitation_code" ]] || staff_invitation_code="$(openssl rand -hex 16)"

umask 077
environment_next="${environment_file}.next"
{
  printf 'SITE_HOST=%s\n' "$site_host"
  printf 'POSTGRES_DB=%s\n' "$(from_existing_or_legacy POSTGRES_DB audentra)"
  printf 'POSTGRES_USER=%s\n' "$(from_existing_or_legacy POSTGRES_USER audentra)"
  printf 'POSTGRES_PASSWORD=%s\n' "$postgres_password"
  printf 'MINIO_ROOT_USER=%s\n' "$(from_existing_or_legacy MINIO_ROOT_USER audentra-minio)"
  printf 'MINIO_ROOT_PASSWORD=%s\n' "$minio_password"
  printf 'MINIO_BUCKET=%s\n' "$(from_existing_or_legacy MINIO_BUCKET audentra-documents)"
  printf 'DOCUMENT_WORKER_TOKEN=%s\n' "$worker_token"
  printf 'VV_STAFF_INVITATION_CODE=%s\n' "$staff_invitation_code"
  printf 'OPENROUTER_API_KEY=%s\n' "$(from_existing_or_legacy OPENROUTER_API_KEY)"
  printf 'OPENROUTER_MODEL=%s\n' "$(from_existing_or_legacy OPENROUTER_MODEL openai/gpt-4o-mini)"
  printf 'GROQ_API_KEY=%s\n' "$(from_existing_or_legacy GROQ_API_KEY)"
  printf 'GROQ_MODEL=%s\n' "$(from_existing_or_legacy GROQ_MODEL qwen/qwen3.6-27b)"
  printf 'TRANSCRIPT_PARSING=%s\n' "$(from_existing_or_legacy TRANSCRIPT_PARSING groq)"
} >"$environment_next"
chmod 0600 "$environment_next"
chown root:root "$environment_next"
mv -f "$environment_next" "$environment_file"

if ! docker network inspect audentra-preview >/dev/null 2>&1; then
  docker network create audentra-preview >/dev/null
fi

install -m 0755 "${script_dir}/deploy-platform.sh" /usr/local/sbin/audentra-platform-deploy
echo "Audentra platform preview host is ready. Protected values remain in ${environment_file}."
