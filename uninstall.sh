#!/usr/bin/env bash
set -euo pipefail
echo "This removes Cloak patch infra only — Hermes itself stays installed."
read -r -p "Continue? [y/N] " ans
[[ "${ans,,}" == "y" ]] || exit 0
systemctl stop hermes-gateway 2>/dev/null || true
docker stop cloakbrowser-manager 2>/dev/null || true
rm -f /etc/systemd/system/hermes-gateway.service.d/10-cloak-env.conf
rm -f /etc/nginx/sites-enabled/cloak-cdp-proxy
systemctl daemon-reload
systemctl reload nginx 2>/dev/null || true
echo "Done. Plugin dir /opt/hermes-plugin-cloak and /etc/cloak/manager.env kept for safety."