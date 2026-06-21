#!/usr/bin/env bash
# Maintainer only — pull deltas from a remote VPS for merge review.
# Usage: VPS_HOST=user@host ./sync-from-vps.sh
set -euo pipefail
: "${VPS_HOST:?Set VPS_HOST=user@your-server}"
PATCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAP="$PATCH_DIR/_vps_snapshot"
mkdir -p "$SNAP"
scp -r "$VPS_HOST:/opt/hermes-plugin-cloak/" "$SNAP/plugin/" || true
scp -r "$VPS_HOST:/root/.hermes/skills/cloak-*" "$SNAP/skills/" 2>/dev/null || true
scp "$VPS_HOST:/etc/nginx/sites-available/cloak-cdp-proxy" "$SNAP/nginx/" 2>/dev/null || true
echo "Snapshot in $SNAP — review diff before merging"