#!/usr/bin/env bash
set -euo pipefail

for command in curl docker git; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$command" >&2
    exit 1
  fi
done

if ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Compose v2 is required.\n' >&2
  exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  printf 'Gateway scenario builds require a clean Git worktree. Commit the task changes first.\n' >&2
  exit 1
fi

export NANOBOT_BUILD_REF="git-$(git rev-parse --short=12 HEAD)"
export NANOBOT_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
scenario_id="scenario-$(date -u +%Y%m%dT%H%M%SZ)"

managed_container_id=$(docker compose ps --status running -q nanobot-gateway 2>/dev/null | cut -c1-12 || true)
port_conflicts=$( {
  docker ps --filter publish=8765 --format '{{.ID}} {{.Names}}'
  docker ps --filter publish=18790 --format '{{.ID}} {{.Names}}'
} | sort -u | awk -v managed="$managed_container_id" '$1 != managed')
if [ -n "$port_conflicts" ]; then
  printf 'The fixed Gateway ports are occupied by another container:\n%s\n' "$port_conflicts" >&2
  printf 'This script only replaces the Compose service nanobot-gateway. Stop the listed container explicitly, then rerun.\n' >&2
  exit 1
fi

printf 'Building and replacing nanobot-gateway with %s...\n' "$NANOBOT_BUILD_REF"
docker compose build nanobot-gateway
docker compose up --no-build --force-recreate -d nanobot-gateway

health_url='http://127.0.0.1:18790/health'
health_body=''
attempt=0
while [ "$attempt" -lt 120 ]; do
  health_body=$(curl --fail --silent --show-error "$health_url" 2>/dev/null || true)
  if printf '%s' "$health_body" | grep -Fq "\"ref\": \"${NANOBOT_BUILD_REF}\""; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if ! printf '%s' "$health_body" | grep -Fq "\"ref\": \"${NANOBOT_BUILD_REF}\""; then
  printf 'Gateway did not become ready with the expected build reference.\n' >&2
  printf 'Expected: %s\n' "$NANOBOT_BUILD_REF" >&2
  printf 'Health response: %s\n' "${health_body:-<none>}" >&2
  docker compose ps nanobot-gateway >&2
  exit 1
fi

container_id=$(docker compose ps --status running -q nanobot-gateway)
if [ -z "$container_id" ]; then
  printf 'Gateway is healthy but no running Compose container was found.\n' >&2
  exit 1
fi
image_id=$(docker inspect --format '{{.Image}}' "$container_id")
container_ref=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_id" \
  | awk -F= '$1 == "NANOBOT_BUILD_REF" { print substr($0, index($0, "=") + 1); exit }')
if [ "$container_ref" != "$NANOBOT_BUILD_REF" ]; then
  printf 'Container build reference does not match the requested build.\n' >&2
  printf 'Expected: %s\nActual: %s\n' "$NANOBOT_BUILD_REF" "${container_ref:-<none>}" >&2
  exit 1
fi

printf '\nGateway scenario environment is ready.\n'
printf 'Build reference: %s\n' "$NANOBOT_BUILD_REF"
printf 'Image ID: %s\n' "$image_id"
printf 'Health: %s\n' "$health_url"
printf 'New chat: http://localhost:8765/#/new\n'
printf 'Trace workbench: http://localhost:8765/#/traces\n'
printf 'Scenario marker: [%s]\n' "$scenario_id"
printf 'Next: send a task-specific prompt containing this marker in a new WebUI chat, then record its trace URL.\n'
