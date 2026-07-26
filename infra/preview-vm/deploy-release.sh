#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this deployment command through sudo." >&2
  exit 1
fi

if [[ "$#" -ne 1 || ! "$1" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Usage: vv-edgent-deploy <40-character git commit SHA>" >&2
  exit 64
fi

release_id="$1"
archive="/tmp/vv-edgent-${release_id}.tar.gz"
release_root="/opt/vv-edgent/releases"
shared_root="/opt/vv-edgent/shared"
release_dir="${release_root}/${release_id}"
current_link="/opt/vv-edgent/current"
environment_file="${shared_root}/.env"
deployment_file="${shared_root}/deployment.env"
lock_file="/run/lock/vv-edgent-deploy.lock"

exec 9>"$lock_file"
if ! flock -n 9; then
  echo "Another VV deployment is already running." >&2
  exit 75
fi

if [[ ! -f "$archive" ]]; then
  echo "Release archive not found: $archive" >&2
  exit 66
fi
if [[ -L "$current_link" && "$(readlink -f "$current_link")" == "$release_dir" ]]; then
  rm -f "$archive"
  echo "VV Edgent release ${release_id} is already active."
  exit 0
fi

if [[ ! -f "$environment_file" ]]; then
  echo "Protected deployment environment is missing: $environment_file" >&2
  exit 78
fi
if [[ ! -f "$deployment_file" ]]; then
  printf 'DEPLOY_IMAGE_TAG=local\n' >"$deployment_file"
  chmod 0600 "$deployment_file"
  chown root:root "$deployment_file"
fi

while IFS= read -r entry; do
  if [[ -z "$entry" || "$entry" == /* || "$entry" == ".." || "$entry" == ../* || "$entry" == */../* ]]; then
    echo "Unsafe archive entry rejected: $entry" >&2
    exit 65
  fi
done < <(tar -tzf "$archive")

previous_release=""
previous_tag=""
if [[ -L "$current_link" ]]; then
  previous_release="$(readlink -f "$current_link")"
fi
if [[ -f "$deployment_file" ]]; then
  previous_tag="$(
    sed -n 's/^DEPLOY_IMAGE_TAG=//p' "$deployment_file" |
      tail -n 1
  )"
fi

rm -rf "$release_dir"
install -d -m 0750 -o root -g root "$release_dir"
tar -xzf "$archive" \
  --directory "$release_dir" \
  --no-same-owner \
  --no-same-permissions
rm -f "$archive"

if find "$release_dir" -type l -print -quit | grep -q .; then
  echo "Release archives containing symbolic links are not accepted." >&2
  rm -rf "$release_dir"
  exit 65
fi

ln -s "$environment_file" "$release_dir/infra/preview-vm/.env"
chmod 0600 "$environment_file"
chown root:root "$environment_file"

compose=(
  docker compose
  --env-file "$environment_file"
  --env-file "$deployment_file"
  -f "$release_dir/infra/preview-vm/compose.yaml"
)

printf 'DEPLOY_IMAGE_TAG=%s\n' "$release_id" >"${deployment_file}.next"
chmod 0600 "${deployment_file}.next"
chown root:root "${deployment_file}.next"

DEPLOY_IMAGE_TAG="$release_id" "${compose[@]}" build api
DEPLOY_IMAGE_TAG="$release_id" "${compose[@]}" build web

ln -sfn "$release_dir" "${current_link}.next"
mv -Tf "${current_link}.next" "$current_link"
mv -f "${deployment_file}.next" "$deployment_file"

if ! "${compose[@]}" up -d --no-build --remove-orphans; then
  deployment_failed=1
else
  deployment_failed=0
fi

for _ in $(seq 1 60); do
  api_health="$(
    docker inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      vv-edgent-preview-api-1 2>/dev/null || true
  )"
  web_health="$(
    docker inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      vv-edgent-preview-web-1 2>/dev/null || true
  )"
  if [[ "$api_health" == "healthy" && "$web_health" == "healthy" ]]; then
    deployment_failed=0
    break
  fi
  deployment_failed=1
  sleep 2
done

if [[ "$deployment_failed" -ne 0 ]]; then
  echo "Release health check failed; restoring the previous release." >&2
  if [[ -n "$previous_release" && -n "$previous_tag" && -d "$previous_release" ]]; then
    ln -sfn "$previous_release" "${current_link}.rollback"
    mv -Tf "${current_link}.rollback" "$current_link"
    printf 'DEPLOY_IMAGE_TAG=%s\n' "$previous_tag" >"$deployment_file"
    chmod 0600 "$deployment_file"
    rollback_compose=(
      docker compose
      --env-file "$environment_file"
      --env-file "$deployment_file"
      -f "$previous_release/infra/preview-vm/compose.yaml"
    )
    "${rollback_compose[@]}" up -d --no-build --remove-orphans
  fi
  exit 1
fi

install -m 0755 \
  "$release_dir/infra/preview-vm/deploy-release.sh" \
  /usr/local/sbin/vv-edgent-deploy
install -m 0755 \
  "$release_dir/infra/preview-vm/harden-host.sh" \
  /usr/local/sbin/vv-edgent-harden

find "$release_root" \
  -mindepth 1 \
  -maxdepth 1 \
  -type d \
  ! -path "$release_dir" \
  -printf '%T@ %p\n' |
  sort -nr |
  tail -n +4 |
  cut -d' ' -f2- |
  xargs --no-run-if-empty rm -rf

echo "VV Edgent release ${release_id} is healthy."
