#!/usr/bin/env bash
# Print the current Cloak Manager auth token + login hints.
# Run on the VPS as root (or with read access to /etc/cloak/manager.env).
set -uo pipefail

ENV_FILE="${CLOAK_ENV_FILE:-/etc/cloak/manager.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found — is the patch installed?" >&2
  exit 1
fi

TOK="$(grep '^CLOAK_AUTH_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"
if [[ -z "$TOK" ]]; then
  echo "ERROR: CLOAK_AUTH_TOKEN missing from $ENV_FILE" >&2
  exit 1
fi

# Try to figure out the public IP for the SSH tunnel hint
IP="$(curl -s --max-time 3 https://api.ipify.org 2>/dev/null || true)"
[[ -z "$IP" ]] && IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -z "$IP" ]] && IP="YOUR_VPS"
USER_NAME="${SUDO_USER:-$(id -un)}"

cat <<EOF

================================================================
  CLOAK MANAGER LOGIN
================================================================

  Token (paste into the UI / use as Bearer):
    $TOK

  SSH tunnel from laptop:
    ssh -L 8080:127.0.0.1:8080 $USER_NAME@$IP

  Then open: http://localhost:8080

  Direct API test (on the VPS itself):
    curl -H "Authorization: Bearer $TOK" http://127.0.0.1:8080/api/status

  Token files on disk:
    $ENV_FILE                (CLOAK_AUTH_TOKEN=...)
    /etc/cloak/auth_token    (token only, chmod 600)

================================================================
EOF