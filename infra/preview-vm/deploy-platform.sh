#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this deployment command through sudo." >&2
  exit 1
fi

if [[ "$#" -ne 1 || ! "$1" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Usage: audentra-platform-deploy <40-character git commit SHA>" >&2
  exit 64
fi

release_id="$1"
image_archive="/tmp/audentra-platform-image-${release_id}.tar.gz"
release_archive="/tmp/audentra-platform-release-${release_id}.tar.gz"
release_root="/opt/audentra-platform/releases"
shared_root="/opt/audentra-platform/shared"
release_dir="${release_root}/${release_id}"
current_link="/opt/audentra-platform/current"
environment_file="${shared_root}/.env"
deployment_file="${shared_root}/deployment.env"
next_deployment_file="${deployment_file}.next"
lock_file="/run/lock/audentra-preview-deploy.lock"

exec 9>"$lock_file"
if ! flock -w 900 9; then
  echo "Timed out waiting for another Audentra preview deployment to finish." >&2
  exit 75
fi

for required_file in "$image_archive" "$release_archive" "$environment_file"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Required deployment file is missing: ${required_file}" >&2
    exit 66
  fi
done

while IFS= read -r entry; do
  if [[ -z "$entry" || "$entry" == /* || "$entry" == ".." || "$entry" == ../* || "$entry" == */../* ]]; then
    echo "Unsafe release archive entry rejected: ${entry}" >&2
    exit 65
  fi
done < <(tar -tzf "$release_archive")

previous_release=""
previous_tag=""
if [[ -L "$current_link" ]]; then
  previous_release="$(readlink -f "$current_link")"
fi
if [[ -f "$deployment_file" ]]; then
  previous_tag="$(sed -n 's/^PLATFORM_IMAGE_TAG=//p' "$deployment_file" | tail -n 1)"
fi

rm -rf "$release_dir"
install -d -m 0750 -o root -g root "$release_dir"
tar -xzf "$release_archive" --directory "$release_dir" --no-same-owner --no-same-permissions
if find "$release_dir" -type l -print -quit | grep -q .; then
  echo "Release archives containing symbolic links are not accepted." >&2
  rm -rf "$release_dir"
  exit 65
fi
if [[ ! -f "$release_dir/infra/preview-vm/compose.yaml" ]]; then
  echo "The platform preview Compose file is missing from the release." >&2
  exit 66
fi

gzip -t "$image_archive"
docker load --input "$image_archive"
docker image inspect "audentra-platform:${release_id}" >/dev/null

if ! docker network inspect audentra-preview >/dev/null 2>&1; then
  docker network create audentra-preview >/dev/null
fi

printf 'PLATFORM_IMAGE_TAG=%s\n' "$release_id" >"$next_deployment_file"
chmod 0600 "$next_deployment_file"
chown root:root "$next_deployment_file"
chmod 0600 "$environment_file"
chown root:root "$environment_file"

compose=(
  docker compose
  --project-name audentra-platform-preview
  --env-file "$environment_file"
  --env-file "$next_deployment_file"
  --file "$release_dir/infra/preview-vm/compose.yaml"
)

deployment_failed=0
"${compose[@]}" up -d --no-build postgres minio || deployment_failed=1
if [[ "$deployment_failed" -eq 0 ]]; then
  "${compose[@]}" run --rm minio-init || deployment_failed=1
fi
if [[ "$deployment_failed" -eq 0 ]]; then
  "${compose[@]}" run --rm migrate || deployment_failed=1
fi
if [[ "$deployment_failed" -eq 0 ]]; then
  minio_ip="$(
    docker inspect \
      --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
      audentra-platform-preview-minio-1
  )"
  if [[ ! "$minio_ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    echo "Could not resolve the MinIO container address for deterministic seeding." >&2
    deployment_failed=1
  else
    "${compose[@]}" run --rm \
      -e "OBJECT_STORAGE_ENDPOINT=http://${minio_ip}:9000" \
      seed || deployment_failed=1
  fi
fi
if [[ "$deployment_failed" -eq 0 ]]; then
  "${compose[@]}" up -d --no-build api worker || deployment_failed=1
fi

if [[ "$deployment_failed" -eq 0 ]]; then
  for _ in $(seq 1 60); do
    api_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' audentra-platform-preview-api-1 2>/dev/null || true)"
    worker_state="$(docker inspect --format '{{.State.Status}}' audentra-platform-preview-worker-1 2>/dev/null || true)"
    if [[ "$api_health" == "healthy" && "$worker_state" == "running" ]]; then
      deployment_failed=0
      break
    fi
    deployment_failed=1
    sleep 2
  done
fi

if [[ "$deployment_failed" -ne 0 ]]; then
  echo "Platform health check failed; restoring the previous application image." >&2
  "${compose[@]}" logs --tail=200 api worker migrate seed >&2 || true
  if [[ -n "$previous_release" && -n "$previous_tag" && -d "$previous_release" ]]; then
    printf 'PLATFORM_IMAGE_TAG=%s\n' "$previous_tag" >"$deployment_file"
    chmod 0600 "$deployment_file"
    rollback_compose=(
      docker compose
      --project-name audentra-platform-preview
      --env-file "$environment_file"
      --env-file "$deployment_file"
      --file "$previous_release/infra/preview-vm/compose.yaml"
    )
    "${rollback_compose[@]}" up -d --no-build postgres minio api worker || true
  fi
  exit 1
fi

ln -sfn "$release_dir" "${current_link}.next"
mv -Tf "${current_link}.next" "$current_link"
mv -f "$next_deployment_file" "$deployment_file"
install -m 0755 "$release_dir/infra/preview-vm/deploy-platform.sh" /usr/local/sbin/audentra-platform-deploy

rm -f "$image_archive" "$release_archive"
find "$release_root" -mindepth 1 -maxdepth 1 -type d ! -path "$release_dir" -printf '%T@ %p\n' |
  sort -nr | tail -n +4 | cut -d' ' -f2- | xargs --no-run-if-empty rm -rf
timeout 60 docker image prune --force >/dev/null || true

echo "Audentra platform release ${release_id} is healthy."
