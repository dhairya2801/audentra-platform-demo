#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this deployment command through sudo." >&2
  exit 1
fi

if [[ "$#" -ne 2 || ! "$1" =~ ^[0-9a-f]{40}$ || ! "$2" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Usage: audentra-platform-deploy <40-character git SHA> <64-character seed revision>" >&2
  exit 64
fi

release_id="$1"
seed_revision="$2"
release_archive="/tmp/audentra-platform-release-${release_id}.tar.gz"
release_root="/opt/audentra-platform/releases"
shared_root="/opt/audentra-platform/shared"
release_dir="${release_root}/${release_id}"
current_link="/opt/audentra-platform/current"
environment_file="${shared_root}/.env"
deployment_file="${shared_root}/deployment.env"
next_deployment_file="${deployment_file}.next"
seed_revision_file="${shared_root}/seed-revision"
next_seed_revision_file="${seed_revision_file}.next"
lock_file="/run/lock/audentra-preview-deploy.lock"

read_environment_value() {
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

exec 9>"$lock_file"
if ! flock -w 900 9; then
  echo "Timed out waiting for another Audentra preview deployment to finish." >&2
  exit 75
fi

for required_file in "$release_archive" "$environment_file"; do
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
if [[ -L "$current_link" ]]; then
  previous_release="$(readlink -f "$current_link")"
fi

rm -rf "$release_dir"
install -d -m 0750 -o root -g root "$release_dir"
tar -xzf "$release_archive" --directory "$release_dir" --no-same-owner --no-same-permissions
if find "$release_dir" -type l -print -quit | grep -q .; then
  echo "Release archives containing symbolic links are not accepted." >&2
  rm -rf "$release_dir"
  exit 65
fi
for release_file in compose.yaml Caddyfile .env.example; do
  if [[ ! -f "$release_dir/infra/preview-vm/$release_file" ]]; then
    echo "The platform preview release is missing: ${release_file}" >&2
    exit 66
  fi
done

bootstrap_script="$release_dir/infra/preview-vm/bootstrap-host.sh"
if [[ ! -f "$bootstrap_script" ]]; then
  echo "The platform preview bootstrap script is missing from the release." >&2
  exit 66
fi
bash "$bootstrap_script"

image_repository="$(read_environment_value PLATFORM_IMAGE_REPOSITORY)"
if [[ -z "$image_repository" ]]; then
  echo "PLATFORM_IMAGE_REPOSITORY is missing from the protected environment." >&2
  exit 78
fi
image_uri="${image_repository}:${release_id}"

if ! docker image inspect "$image_uri" >/dev/null 2>&1; then
  registry_host="${image_repository%%/*}"
  if [[ "$image_repository" == */* && "$registry_host" == *.pkg.dev ]]; then
    docker_config="$(mktemp -d)"
    trap 'rm -rf "$docker_config"' EXIT
    registry_token="$(
      curl --fail --silent --show-error --connect-timeout 5 --max-time 15 \
        -H 'Metadata-Flavor: Google' \
        'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
    )"
    printf '%s' "$registry_token" \
      | docker --config "$docker_config" login \
          --username oauth2accesstoken --password-stdin "$registry_host" >/dev/null
    docker --config "$docker_config" pull "$image_uri"
  elif [[ "$image_repository" == */* ]]; then
    docker pull "$image_uri"
  else
    echo "Local platform image is missing: ${image_uri}" >&2
    echo "Build the reviewed commit with infra/docker/api.Dockerfile before deploying." >&2
    exit 69
  fi
fi
docker image inspect "$image_uri" >/dev/null

printf 'PLATFORM_IMAGE_URI=%s\n' "$image_uri" >"$next_deployment_file"
chmod 0600 "$next_deployment_file"
chown root:root "$next_deployment_file"
chmod 0600 "$environment_file"
chown root:root "$environment_file"

compose=(
  env
  --ignore-environment
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  docker compose
  --project-name audentra-platform-preview
  --env-file "$environment_file"
  --env-file "$next_deployment_file"
  --file "$release_dir/infra/preview-vm/compose.yaml"
)

deployment_failed=0
"${compose[@]}" config --quiet || deployment_failed=1
if [[ "$deployment_failed" -eq 0 ]]; then
  "${compose[@]}" run --rm --no-deps caddy \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || deployment_failed=1
fi
if [[ "$deployment_failed" -eq 0 ]]; then
  "${compose[@]}" up -d --no-build postgres minio || deployment_failed=1
fi
if [[ "$deployment_failed" -eq 0 ]]; then
  "${compose[@]}" run --rm migrate || deployment_failed=1
fi
if [[ "$deployment_failed" -eq 0 ]]; then
  "${compose[@]}" run --rm minio-init || deployment_failed=1
fi
if [[ "$deployment_failed" -eq 0 ]]; then
  current_seed_revision="$(cat "$seed_revision_file" 2>/dev/null || true)"
  if [[ "$current_seed_revision" == "$seed_revision" ]]; then
    echo "Seed inputs are unchanged; deterministic seeding is skipped."
  else
    "${compose[@]}" run --rm seed || deployment_failed=1
  fi
fi
if [[ "$deployment_failed" -eq 0 ]]; then
  "${compose[@]}" up -d --no-build api worker caddy || deployment_failed=1
fi

if [[ "$deployment_failed" -eq 0 ]]; then
  deployment_failed=1
  for _ in $(seq 1 60); do
    api_container="$("${compose[@]}" ps -q api)"
    worker_container="$("${compose[@]}" ps -q worker)"
    caddy_container="$("${compose[@]}" ps -q caddy)"
    api_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$api_container" 2>/dev/null || true)"
    worker_state="$(docker inspect --format '{{.State.Status}}' "$worker_container" 2>/dev/null || true)"
    caddy_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$caddy_container" 2>/dev/null || true)"
    if [[ "$api_health" == "healthy" && "$worker_state" == "running" && "$caddy_health" == "healthy" ]]; then
      deployment_failed=0
      break
    fi
    sleep 2
  done
fi

if [[ "$deployment_failed" -ne 0 ]]; then
  echo "Platform health check failed; restoring the previous application image." >&2
  "${compose[@]}" logs --tail=200 api worker caddy migrate seed minio-init >&2 || true
  if [[ -n "$previous_release" && -d "$previous_release" && -f "$deployment_file" ]]; then
    rollback_compose=(
      env
      --ignore-environment
      PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
      docker compose
      --project-name audentra-platform-preview
      --env-file "$environment_file"
      --env-file "$deployment_file"
      --file "$previous_release/infra/preview-vm/compose.yaml"
    )
    rollback_services=(postgres minio api worker)
    if "${rollback_compose[@]}" config --services | grep -qx caddy; then
      rollback_services+=(caddy)
    fi
    "${rollback_compose[@]}" up -d --no-build "${rollback_services[@]}" || true
  fi
  exit 1
fi

ln -sfn "$release_dir" "${current_link}.next"
mv -Tf "${current_link}.next" "$current_link"
mv -f "$next_deployment_file" "$deployment_file"
printf '%s\n' "$seed_revision" >"$next_seed_revision_file"
chmod 0600 "$next_seed_revision_file"
chown root:root "$next_seed_revision_file"
mv -f "$next_seed_revision_file" "$seed_revision_file"
install -m 0755 "$release_dir/infra/preview-vm/deploy-platform.sh" /usr/local/sbin/audentra-platform-deploy

rm -f "$release_archive"
find "$release_root" -mindepth 1 -maxdepth 1 -type d ! -path "$release_dir" -printf '%T@ %p\n' |
  sort -nr | tail -n +4 | cut -d' ' -f2- | xargs --no-run-if-empty rm -rf

echo "Audentra platform release ${release_id} is healthy."
