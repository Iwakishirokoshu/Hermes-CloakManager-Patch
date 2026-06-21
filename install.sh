#!/usr/bin/env bash
# hermes-cloak-patch install.sh
# Addon for an EXISTING Hermes install. Adds:
#   - Docker CloakBrowser-Manager on 127.0.0.1:8080
#   - nginx CDP auth proxy on 127.0.0.1:8081
#   - hermes-plugin-cloak (humanize + captcha) into Hermes venv
#   - 3 skills into ~/.hermes/skills/
#   - merges plugins.enabled: [cloak] into ~/.hermes/config.yaml
#   - systemd drop-in for hermes-gateway.service (if it exists)
#
# Idempotent: re-run is safe; --keep-token preserves existing /etc/cloak/manager.env.
set -euo pipefail

PATCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_INSTALL="${PLUGIN_INSTALL:-/opt/hermes-plugin-cloak}"
ENV_FILE="/etc/cloak/manager.env"
ETC_DIR="/etc/cloak"

DRY_RUN=0
KEEP_TOKEN=1
FORCE_SKILLS=0
WITH_BACKUP=0
SKIP_RESTART=0
SECRETS_FILE="${CLOAK_PATCH_ENV:-}"
DOCKER_TOKEN_ENV="${CLOAK_DOCKER_TOKEN_ENV:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --regenerate-token) KEEP_TOKEN=0; shift ;;
    --force-skills) FORCE_SKILLS=1; shift ;;
    --with-backup) WITH_BACKUP=1; shift ;;
    --skip-restart) SKIP_RESTART=1; shift ;;
    --token-env=*) DOCKER_TOKEN_ENV="${1#*=}"; shift ;;
    -h|--help)
      cat <<USAGE
Usage: sudo bash install.sh [options]

Options:
  --dry-run              Print steps without running them
  --regenerate-token     Overwrite /etc/cloak/manager.env with a new token
  --force-skills         Overwrite existing skills in ~/.hermes/skills/
  --with-backup          Install daily backup timer
  --skip-restart         Don't restart hermes-gateway after install
  --token-env=NAME       Env var name passed to Docker manager (default AUTH_TOKEN)

Environment:
  CLOAK_PATCH_ENV=/path/secrets.env   Pre-fill manager.env from this file
  HERMES_BIN=/path/to/hermes          Explicit Hermes binary override
USAGE
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

C_BLU=$'\033[1;36m'; C_GRN=$'\033[1;32m'; C_YEL=$'\033[1;33m'; C_RED=$'\033[1;31m'; C_RST=$'\033[0m'
say()  { printf "%s==>%s %s\n" "$C_BLU" "$C_RST" "$*"; }
ok()   { printf "%s[ok]%s %s\n" "$C_GRN" "$C_RST" "$*"; }
warn() { printf "%s[warn]%s %s\n" "$C_YEL" "$C_RST" "$*"; }
err()  { printf "%s[err]%s %s\n" "$C_RED" "$C_RST" "$*" >&2; }

# Array-form runner: handles spaces and special chars safely.
run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf "  [dry-run]"
    printf " %q" "$@"
    printf "\n"
  else
    "$@"
  fi
}

require_root() { [[ $EUID -eq 0 ]] || { err "run with sudo"; exit 1; }; }

# ---- detection ---- #

DETECTED_HERMES_BIN=""
DETECTED_HERMES_VENV=""
DETECTED_HERMES_USER=""
DETECTED_HERMES_HOME=""
VENV_PIP=""
VENV_PY=""
UV_BIN=""

detect_hermes() {
  say "Detecting Hermes..."
  local cand=""
  if [[ -n "${HERMES_BIN:-}" && -x "$HERMES_BIN" ]]; then
    cand="$HERMES_BIN"
  elif command -v hermes >/dev/null 2>&1; then
    cand="$(command -v hermes)"
  else
    for p in \
      /usr/local/lib/hermes-agent/venv/bin/hermes \
      /opt/hermes-agent/venv/bin/hermes \
      /opt/hermes/.venv/bin/hermes \
      "$HOME/.local/pipx/venvs/hermes-agent/bin/hermes" \
      /root/.local/pipx/venvs/hermes-agent/bin/hermes \
      "$HOME/.local/bin/hermes" \
      /root/.local/bin/hermes ; do
      if [[ -x "$p" ]]; then cand="$p"; break; fi
    done
  fi
  [[ -z "$cand" ]] && { err "Hermes not found. Install Hermes first or set HERMES_BIN=..."; exit 1; }

  cand="$(readlink -f "$cand")"
  local venv
  venv="$(dirname "$(dirname "$cand")")"

  # Some installers ship a shell wrapper at /usr/local/bin/hermes that exec's
  # into a real venv elsewhere — peel one layer if so.
  if [[ ! -x "$venv/bin/python" ]] && file -b "$cand" 2>/dev/null | grep -q "shell script"; then
    local inner
    inner="$(grep -oE 'exec[[:space:]]+"?[^"]*/bin/hermes' "$cand" 2>/dev/null | awk '{print $NF}' | tr -d '"' | head -1)"
    if [[ -n "$inner" && -x "$inner" ]]; then
      cand="$(readlink -f "$inner")"
      venv="$(dirname "$(dirname "$cand")")"
    fi
  fi

  if [[ ! -x "$venv/bin/python" ]] && [[ -x /usr/local/lib/hermes-agent/venv/bin/hermes ]]; then
    cand="/usr/local/lib/hermes-agent/venv/bin/hermes"
    venv="/usr/local/lib/hermes-agent/venv"
  fi

  [[ -x "$venv/bin/python" ]] || { err "no python at $venv/bin/python"; exit 1; }

  local owner
  owner="$(stat -c '%U' "$cand" 2>/dev/null || echo root)"
  local home
  home="$(getent passwd "$owner" | cut -d: -f6)"
  [[ -z "$home" ]] && home="/root"

  DETECTED_HERMES_BIN="$cand"
  DETECTED_HERMES_VENV="$venv"
  DETECTED_HERMES_USER="$owner"
  DETECTED_HERMES_HOME="$home"
  VENV_PY="$venv/bin/python"
  [[ -x "$venv/bin/pip" ]] && VENV_PIP="$venv/bin/pip"

  ok "bin   = $DETECTED_HERMES_BIN"
  ok "venv  = $DETECTED_HERMES_VENV"
  ok "user  = $DETECTED_HERMES_USER"
  ok "home  = $DETECTED_HERMES_HOME"
  if [[ -n "$VENV_PIP" ]]; then ok "pip   = $VENV_PIP"; else warn "no pip in venv — will use uv"; fi
}

find_uv() {
  for p in \
    "$(command -v uv 2>/dev/null || true)" \
    "$DETECTED_HERMES_HOME/.hermes/bin/uv" \
    "$DETECTED_HERMES_HOME/.local/bin/uv" \
    /root/.hermes/bin/uv \
    /root/.local/bin/uv \
    /usr/local/bin/uv ; do
    if [[ -n "$p" && -x "$p" ]]; then UV_BIN="$p"; return 0; fi
  done
  return 1
}

# ---- steps ---- #

step_preflight() {
  say "Preflight packages"
  if ! command -v docker >/dev/null 2>&1; then
    run apt-get update -qq
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
  fi
  if ! command -v nginx >/dev/null 2>&1; then
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx-light
  fi
  for pkg in curl python3 rsync openssl; do
    command -v "$pkg" >/dev/null 2>&1 || run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$pkg"
  done
  if [[ $DRY_RUN -eq 0 ]]; then
    systemctl enable --now docker 2>/dev/null || warn "docker not enabled"
  fi
}

step_env() {
  say "Environment file $ENV_FILE"
  run install -d -m 0750 "$ETC_DIR"

  if [[ -f "$ENV_FILE" && $KEEP_TOKEN -eq 1 ]]; then
    ok "keeping existing $ENV_FILE (use --regenerate-token to replace)"
    # Still make sure CLOAK_CDP_PROXY_BASE is present
    if [[ $DRY_RUN -eq 0 ]] && ! grep -q '^CLOAK_CDP_PROXY_BASE=' "$ENV_FILE"; then
      echo "CLOAK_CDP_PROXY_BASE=http://127.0.0.1:8081" >> "$ENV_FILE"
      ok "added CLOAK_CDP_PROXY_BASE to existing env"
    fi
    return
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    say "would create $ENV_FILE from template + generated token"
    return
  fi

  local tok
  tok="$(openssl rand -hex 32)"
  install -m 0600 /dev/null "$ENV_FILE"
  cat > "$ENV_FILE" <<EOF
CLOAK_MANAGER_URL=http://127.0.0.1:8080
CLOAK_AUTH_TOKEN=${tok}
CLOAK_CDP_PROXY_BASE=http://127.0.0.1:8081
CAPSOLVER_API_KEY=
TWOCAPTCHA_API_KEY=
TWO_CAPTCHA_API_KEY=
NOTLETTERS_API_KEY=
EOF
  echo "$tok" > "$ETC_DIR/auth_token"
  chmod 600 "$ETC_DIR/auth_token"

  if [[ -n "$SECRETS_FILE" && -f "$SECRETS_FILE" ]]; then
    while IFS='=' read -r k v; do
      [[ -z "$k" || "$k" =~ ^[[:space:]]*# ]] && continue
      k="${k// /}"
      if grep -q "^${k}=" "$ENV_FILE"; then
        sed -i "s|^${k}=.*|${k}=${v}|" "$ENV_FILE"
      else
        echo "${k}=${v}" >> "$ENV_FILE"
      fi
    done < "$SECRETS_FILE"
    ok "merged secrets from $SECRETS_FILE"
  fi
  ok "created $ENV_FILE"
}

step_docker_image_token_env() {
  # Try to figure out which env var name the cloakhq image reads.
  # Order: explicit --token-env, existing container env, env var override, default.
  if [[ -n "$DOCKER_TOKEN_ENV" ]]; then
    ok "Docker token env = $DOCKER_TOKEN_ENV (from --token-env / env)"
    return
  fi
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx cloakbrowser-manager; then
    local existing
    existing="$(docker inspect cloakbrowser-manager --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
      | grep -oE '^(AUTH_TOKEN|CLOAK_AUTH_TOKEN|MANAGER_TOKEN|API_TOKEN)=' \
      | head -1 | tr -d '=')"
    if [[ -n "$existing" ]]; then
      DOCKER_TOKEN_ENV="$existing"
      ok "Docker token env = $DOCKER_TOKEN_ENV (detected from existing container)"
      return
    fi
  fi
  DOCKER_TOKEN_ENV="AUTH_TOKEN"
  warn "Docker token env defaulting to AUTH_TOKEN — override with --token-env=NAME if your image uses a different name"
}

step_docker() {
  say "Docker CloakBrowser-Manager"
  step_docker_image_token_env
  run env "CLOAK_DOCKER_TOKEN_ENV=$DOCKER_TOKEN_ENV" bash "$PATCH_DIR/docker/run-manager.sh"
}

step_plugin() {
  say "Plugin -> $PLUGIN_INSTALL"
  if [[ -d "$PLUGIN_INSTALL" ]]; then
    run rm -rf "$PLUGIN_INSTALL.bak"
    run cp -a "$PLUGIN_INSTALL" "$PLUGIN_INSTALL.bak"
  fi
  run install -d -m 0755 "$PLUGIN_INSTALL"
  run rsync -a --delete "$PATCH_DIR/plugin/" "$PLUGIN_INSTALL/"

  if [[ -n "$VENV_PIP" ]]; then
    run sudo -u "$DETECTED_HERMES_USER" -H "$VENV_PIP" install -q --upgrade pip wheel
    run sudo -u "$DETECTED_HERMES_USER" -H "$VENV_PIP" install -q -e "$PLUGIN_INSTALL" "httpx>=0.27"
  elif find_uv; then
    say "using uv at $UV_BIN (no pip in venv)"
    run env UV_NO_CONFIG=1 "$UV_BIN" pip install --quiet --python "$VENV_PY" --upgrade pip wheel
    run env UV_NO_CONFIG=1 "$UV_BIN" pip install --quiet --python "$VENV_PY" -e "$PLUGIN_INSTALL" "httpx>=0.27"
  else
    err "neither pip nor uv available — cannot install plugin into $DETECTED_HERMES_VENV"
    exit 1
  fi

  say "Installing Playwright Chromium (may take a few minutes)"
  if [[ $DRY_RUN -eq 0 ]]; then
    sudo -u "$DETECTED_HERMES_USER" -H HOME="$DETECTED_HERMES_HOME" \
      "$VENV_PY" -m playwright install chromium 2>&1 | tail -5 || warn "playwright install warning"
  else
    echo "  [dry-run] playwright install chromium"
  fi

  # Hermes v0.17+ uses directory-style plugin discovery in ~/.hermes/plugins/<name>/
  # in addition to (or instead of) Python entry-points. We always create the shim
  # so the plugin is discoverable regardless of which mechanism Hermes prefers.
  local plg_dir="$DETECTED_HERMES_HOME/.hermes/plugins/cloak"
  run install -d -m 0755 "$DETECTED_HERMES_HOME/.hermes/plugins"
  run install -d -m 0755 "$plg_dir"
  if [[ $DRY_RUN -eq 0 ]]; then
    cat > "$plg_dir/plugin.yaml" <<YAML
name: cloak
version: 0.1.0
description: CloakBrowser-Manager stealth profile control + humanized browser_* tool overrides + captcha routing.
hooks: []
YAML
    cat > "$plg_dir/__init__.py" <<'PY'
"""Hermes plugin shim — delegates to the pip-installed hermes_plugin_cloak package."""
from hermes_plugin_cloak import register  # noqa: F401
PY
    chown -R "$DETECTED_HERMES_USER:$DETECTED_HERMES_USER" "$DETECTED_HERMES_HOME/.hermes/plugins"
  fi
  ok "directory-style plugin shim at $plg_dir"
  ok "plugin installed"
}

step_nginx() {
  say "nginx CDP proxy :8081"
  if [[ $DRY_RUN -eq 1 ]]; then
    say "would write /etc/nginx/sites-available/cloak-cdp-proxy and /etc/nginx/conf.d/cloak-upgrade-map.conf"
    return
  fi
  local tok
  tok="$(grep '^CLOAK_AUTH_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2-)"
  [[ -n "$tok" ]] || { err "CLOAK_AUTH_TOKEN missing in $ENV_FILE"; exit 1; }

  # map directive must live in http {} scope, not server {}
  install -m 0644 "$PATCH_DIR/nginx/cloak-upgrade-map.conf" /etc/nginx/conf.d/cloak-upgrade-map.conf

  sed "s|__CLOAK_AUTH_TOKEN__|${tok}|g" "$PATCH_DIR/nginx/cloak-cdp-proxy.conf.template" \
    > /etc/nginx/sites-available/cloak-cdp-proxy
  chmod 0644 /etc/nginx/sites-available/cloak-cdp-proxy
  ln -sf /etc/nginx/sites-available/cloak-cdp-proxy /etc/nginx/sites-enabled/cloak-cdp-proxy
  rm -f /etc/nginx/sites-enabled/default

  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx
  ok "nginx CDP proxy on 127.0.0.1:8081"
}

step_skills() {
  say "Skills -> $DETECTED_HERMES_HOME/.hermes/skills/"
  local dest="$DETECTED_HERMES_HOME/.hermes/skills"
  run install -d -m 0755 -o "$DETECTED_HERMES_USER" -g "$DETECTED_HERMES_USER" "$dest"
  for skill in cloak-account-registration cloak-proxy-pool notletters-api; do
    if [[ -d "$dest/$skill" && $FORCE_SKILLS -eq 0 ]]; then
      warn "skill $skill exists — skip (use --force-skills to overwrite)"
    else
      run rsync -a "$PATCH_DIR/skills/$skill/" "$dest/$skill/"
      run chown -R "$DETECTED_HERMES_USER:$DETECTED_HERMES_USER" "$dest/$skill"
    fi
  done
}

step_config() {
  say "Hermes config plugins.enabled <- cloak"
  local cfg="$DETECTED_HERMES_HOME/.hermes/config.yaml"

  if [[ $DRY_RUN -eq 1 ]]; then
    say "would merge plugins.enabled: [cloak] into $cfg"
    return
  fi

  run install -d -m 0755 -o "$DETECTED_HERMES_USER" -g "$DETECTED_HERMES_USER" "$DETECTED_HERMES_HOME/.hermes"

  # Use the venv python — Hermes already has PyYAML; ruamel optional.
  if "$VENV_PY" "$PATCH_DIR/scripts/merge_plugin_enabled.py" "$cfg" cloak; then
    chown "$DETECTED_HERMES_USER:$DETECTED_HERMES_USER" "$cfg" 2>/dev/null || true
    ok "config.yaml merged"
  else
    warn "could not merge config.yaml automatically — add 'plugins: {enabled: [cloak]}' manually"
  fi

  # Warn if user has stale CLOAK_AUTH_TOKEN in ~/.hermes/.env (this caused desync on the maintainer VPS).
  if [[ -f "$DETECTED_HERMES_HOME/.hermes/.env" ]] && grep -q '^CLOAK_AUTH_TOKEN=' "$DETECTED_HERMES_HOME/.hermes/.env" 2>/dev/null; then
    warn "CLOAK_AUTH_TOKEN found in ~/.hermes/.env — remove it (single source of truth is /etc/cloak/manager.env)"
  fi
}

step_systemd() {
  say "Gateway systemd drop-in"
  local drop="/etc/systemd/system/hermes-gateway.service.d"
  if [[ $DRY_RUN -eq 1 ]]; then
    say "would install $drop/10-cloak-env.conf"
    return
  fi
  install -d -m 0755 "$drop"
  install -m 0644 "$PATCH_DIR/config/gateway-drop-in.conf" "$drop/10-cloak-env.conf"
  systemctl daemon-reload

  if ! systemctl cat hermes-gateway.service >/dev/null 2>&1; then
    warn "hermes-gateway.service not installed — drop-in is in place but useless until you create the unit."
    warn "  Template: $PATCH_DIR/systemd/hermes-gateway.service.example"
    warn "  Or run Hermes gateway manually with: hermes gateway start --transport telegram"
  fi
}

step_backup() {
  [[ $WITH_BACKUP -eq 0 ]] && return
  say "Backup timer"
  if [[ $DRY_RUN -eq 1 ]]; then
    say "would install cloak-backup.timer (daily 03:30 UTC)"
    return
  fi
  install -m 0755 "$PATCH_DIR/scripts/cloak-backup.sh" /usr/local/bin/cloak-backup.sh
  install -m 0644 "$PATCH_DIR/systemd/cloak-backup.service" /etc/systemd/system/
  install -m 0644 "$PATCH_DIR/systemd/cloak-backup.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now cloak-backup.timer
}

step_restart() {
  [[ $SKIP_RESTART -eq 1 ]] && return
  say "Restart hermes-gateway (if present)"
  if [[ $DRY_RUN -eq 1 ]]; then
    say "would: systemctl restart hermes-gateway.service"
    return
  fi
  if systemctl cat hermes-gateway.service >/dev/null 2>&1; then
    systemctl restart hermes-gateway.service && ok "gateway restarted" || warn "gateway restart failed (check logs)"
  else
    warn "hermes-gateway.service not present — skipping restart"
  fi
}

main() {
  require_root
  detect_hermes
  step_preflight
  step_env
  step_docker
  step_plugin
  step_nginx
  step_skills
  step_config
  step_systemd
  step_backup
  step_restart

  if [[ $DRY_RUN -eq 0 ]]; then
    say "Running verify.sh"
    bash "$PATCH_DIR/scripts/verify.sh" || warn "verify reported issues — see above"
  fi

  ok "Install complete."

  # Final summary — print the token and login instructions so the user
  # doesn't have to hunt for them in /etc/cloak/.
  local final_tok=""
  [[ -f "$ENV_FILE" ]] && final_tok="$(grep '^CLOAK_AUTH_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"
  local public_ip=""
  public_ip="$(curl -s --max-time 3 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  [[ -z "$public_ip" ]] && public_ip="YOUR_VPS"

  printf '\n%s========================================================%s\n' "$C_GRN" "$C_RST"
  printf '%s  HOW TO LOG IN TO CLOAK MANAGER%s\n' "$C_GRN" "$C_RST"
  printf '%s========================================================%s\n\n' "$C_GRN" "$C_RST"
  printf '  1) From your laptop, open an SSH tunnel:\n'
  printf '       ssh -L 8080:127.0.0.1:8080 %s@%s\n\n' "$DETECTED_HERMES_USER" "$public_ip"
  printf '  2) Open in browser:\n'
  printf '       http://localhost:8080\n\n'
  printf '  3) Paste this token when the UI asks (it is the Bearer token):\n'
  if [[ -n "$final_tok" ]]; then
    printf '       %s\n\n' "$final_tok"
  else
    printf '       (token missing — check %s)\n\n' "$ENV_FILE"
  fi
  printf '  Reprint anytime:  cat /etc/cloak/auth_token\n'
  printf '                or: grep CLOAK_AUTH_TOKEN %s\n' "$ENV_FILE"
  printf '                or: bash %s/scripts/get-token.sh\n\n' "$PATCH_DIR"
  printf '  Captcha keys:     edit %s\n' "$ENV_FILE"
  printf '                    (CAPSOLVER_API_KEY / TWOCAPTCHA_API_KEY / NOTLETTERS_API_KEY)\n'
  printf '                    after edit, restart with:\n'
  printf '                      systemctl restart hermes-gateway 2>/dev/null || true\n'
  printf '                      docker restart cloakbrowser-manager\n\n'
  printf '%s========================================================%s\n\n' "$C_GRN" "$C_RST"
}

main "$@"