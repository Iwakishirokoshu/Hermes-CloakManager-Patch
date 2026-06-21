#!/usr/bin/env bash
# Start CloakBrowser-Manager in Docker on 127.0.0.1:${CLOAK_MANAGER_PORT:-8080}.
# Idempotent: if container exists, just (re)start it.
set -euo pipefail

NAME="${CLOAK_DOCKER_NAME:-cloakbrowser-manager}"
IMAGE="${CLOAK_DOCKER_IMAGE:-cloakhq/cloakbrowser-manager:latest}"
VOLUME="${CLOAK_DOCKER_VOLUME:-cloak-profiles}"
PORT="${CLOAK_MANAGER_PORT:-8080}"
ENV_FILE="${CLOAK_ENV_FILE:-/etc/cloak/manager.env}"
TOKEN_ENV="${CLOAK_DOCKER_TOKEN_ENV:-AUTH_TOKEN}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — run install.sh first" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

if [[ -z "${CLOAK_AUTH_TOKEN:-}" ]]; then
  echo "CLOAK_AUTH_TOKEN missing in $ENV_FILE" >&2
  exit 1
fi

# Reuse existing container if any
if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "Container $NAME already running"
    exit 0
  fi
  echo "Starting existing container $NAME"
  docker start "$NAME"
  exit 0
fi

echo "Pulling image $IMAGE..."
docker pull "$IMAGE" 2>&1 | tail -3 || true

echo "Starting container $NAME on 127.0.0.1:${PORT} (token env: $TOKEN_ENV)"
docker run -d \
  --name "$NAME" \
  --restart unless-stopped \
  -p "127.0.0.1:${PORT}:8080" \
  -v "${VOLUME}:/data" \
  -e "${TOKEN_ENV}=${CLOAK_AUTH_TOKEN}" \
  "$IMAGE"

# Wait briefly for the API
for i in 1 2 3 4 5 6 7 8 9 10; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer ${CLOAK_AUTH_TOKEN}" "http://127.0.0.1:${PORT}/api/status" 2>/dev/null || echo 000)
  if [[ "$code" == "200" ]]; then
    echo "CloakBrowser-Manager up on 127.0.0.1:${PORT}"
    exit 0
  fi
  sleep 1
done

echo "Manager started but /api/status not responding 200 yet — check 'docker logs $NAME'" >&2
echo "(May be a token-env mismatch; rerun install.sh with --token-env=NAME)" >&2
exit 0