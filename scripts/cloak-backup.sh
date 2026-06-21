#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR="${CLOAK_BACKUP_DIR:-/var/backups/cloak}"
TS=$(date -u +%Y%m%d-%H%M%S)
DEST="$BACKUP_DIR/$TS"
mkdir -p "$DEST"
docker run --rm -v cloak-profiles:/from:ro -v "$DEST":/to alpine tar czf /to/cloak-data.tar.gz -C /from . 2>/dev/null || true
HERMES_HOME="${HERMES_HOME:-/root}"
[[ -d "$HERMES_HOME/.hermes/cloak" ]] && tar czf "$DEST/agent-creds.tar.gz" -C "$HERMES_HOME/.hermes" cloak 2>/dev/null || true
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} \;
echo "[$(date -u +%FT%TZ)] backup $DEST"