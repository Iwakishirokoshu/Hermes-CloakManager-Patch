#!/usr/bin/env bash
# Smoke test after install.sh. Exits 0 on green, non-zero on any FAIL.
set -uo pipefail

ENV_FILE="/etc/cloak/manager.env"
FAIL=0
pass() { printf "[ok]   %s\n" "$*"; }
fail() { printf "[FAIL] %s\n" "$*"; FAIL=1; }

HERMES_HOME="${HERMES_HOME:-/root}"
if command -v hermes >/dev/null 2>&1; then
  hb="$(readlink -f "$(command -v hermes)" 2>/dev/null || true)"
  if [[ -n "$hb" ]]; then
    u="$(stat -c '%U' "$hb" 2>/dev/null || echo root)"
    h="$(getent passwd "$u" | cut -d: -f6)"
    [[ -n "$h" ]] && HERMES_HOME="$h"
  fi
fi
SKILLS="$HERMES_HOME/.hermes/skills"

if [[ ! -f "$ENV_FILE" ]]; then
  fail "missing $ENV_FILE"
else
  pass "env file present"
fi

# shellcheck disable=SC1090
set -a
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"
set +a
TOK="${CLOAK_AUTH_TOKEN:-}"

if [[ -z "$TOK" ]]; then
  fail "CLOAK_AUTH_TOKEN empty"
else
  pass "CLOAK_AUTH_TOKEN set (len=${#TOK})"
fi

# Manager API with Bearer
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 -H "Authorization: Bearer $TOK" http://127.0.0.1:8080/api/status 2>/dev/null || echo 000)
[[ "$code" == "200" ]] && pass "manager :8080/api/status (auth) = $code" || fail "manager :8080/api/status = $code"

# nginx proxy (no auth — it injects Bearer)
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8081/api/status 2>/dev/null || echo 000)
[[ "$code" == "200" ]] && pass "nginx proxy :8081/api/status = $code" || fail "nginx proxy :8081/api/status = $code"

# Plugin dir
[[ -d /opt/hermes-plugin-cloak/hermes_plugin_cloak ]] && pass "plugin installed" || fail "plugin missing at /opt/hermes-plugin-cloak"

# CDP proxy base env
grep -q '^CLOAK_CDP_PROXY_BASE=' "$ENV_FILE" 2>/dev/null && pass "CLOAK_CDP_PROXY_BASE set" || fail "CLOAK_CDP_PROXY_BASE missing"

# config.yaml has cloak
CFG="$HERMES_HOME/.hermes/config.yaml"
if [[ -f "$CFG" ]] && grep -qE '(^|[[:space:]])-[[:space:]]*cloak([[:space:]]|$)' "$CFG"; then
  pass "config.yaml has plugins.enabled: cloak"
else
  fail "config.yaml missing plugins.enabled: cloak"
fi

# Skills
for s in cloak-account-registration cloak-proxy-pool notletters-api; do
  [[ -d "$SKILLS/$s" ]] && pass "skill $s" || fail "skill $s missing at $SKILLS"
done

# Gateway (optional)
if systemctl cat hermes-gateway.service >/dev/null 2>&1; then
  systemctl is-active --quiet hermes-gateway 2>/dev/null && pass "hermes-gateway active" || fail "hermes-gateway inactive"
  systemctl show hermes-gateway 2>/dev/null | grep -q "EnvironmentFile=.*manager.env" && pass "gateway loads manager.env" || fail "gateway missing manager.env EnvironmentFile"
else
  printf "[skip] hermes-gateway.service not installed (this patch ships an example unit but does not enable it by default)\n"
fi

# Plugin import smoke
if command -v hermes >/dev/null 2>&1; then
  hb="$(readlink -f "$(command -v hermes)" 2>/dev/null || true)"
  py="$(dirname "$(dirname "$hb")")/bin/python"
  if [[ -x "$py" ]]; then
    if "$py" -c "import hermes_plugin_cloak; print(hermes_plugin_cloak.__name__)" >/dev/null 2>&1; then
      pass "plugin importable from Hermes venv"
    else
      fail "plugin NOT importable from $py"
    fi
  fi
fi

if [[ $FAIL -eq 0 ]]; then
  printf "\n[ok] verify: all checks passed\n"
else
  printf "\n[FAIL] verify: %d check(s) failed\n" "$FAIL"
fi
exit $FAIL