#!/usr/bin/env bash
# Hermes-CloakManager-Patch bootstrap installer.
#
# One-liner from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/Iwakishirokoshu/Hermes-CloakManager-Patch/main/scripts/bootstrap.sh | sudo bash
#
# All extra args are forwarded to install.sh, e.g.:
#   curl -fsSL .../bootstrap.sh | sudo bash -s -- --dry-run
#   curl -fsSL .../bootstrap.sh | sudo bash -s -- --token-env CLOAK_AUTH_TOKEN
#
# Environment overrides:
#   REPO_URL  (default: https://github.com/Iwakishirokoshu/Hermes-CloakManager-Patch.git)
#   BRANCH    (default: main)
#   DEST      (default: /opt/hermes-cloak-patch)

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Iwakishirokoshu/Hermes-CloakManager-Patch.git}"
BRANCH="${BRANCH:-main}"
DEST="${DEST:-/opt/hermes-cloak-patch}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: bootstrap.sh must be run as root (use sudo)." >&2
  exit 1
fi

bold "hermes-cloak-patch bootstrap"
echo "  repo:   $REPO_URL"
echo "  branch: $BRANCH"
echo "  dest:   $DEST"
echo

if ! command -v git >/dev/null 2>&1; then
  echo "git not found -> installing..."
  apt-get update -qq
  apt-get install -y -qq git ca-certificates
fi

if [[ -d "$DEST/.git" ]]; then
  echo "[update] existing checkout at $DEST"
  git -C "$DEST" remote set-url origin "$REPO_URL" 2>/dev/null || true
  git -C "$DEST" fetch --depth=1 origin "$BRANCH"
  git -C "$DEST" reset --hard "origin/$BRANCH"
else
  if [[ -e "$DEST" ]]; then
    echo "ERROR: $DEST exists and is not a git checkout. Remove it first." >&2
    exit 2
  fi
  echo "[clone] $REPO_URL -> $DEST"
  git clone --depth=1 --branch "$BRANCH" "$REPO_URL" "$DEST"
fi

chmod +x "$DEST/install.sh" 2>/dev/null || true
chmod +x "$DEST/scripts/"*.sh 2>/dev/null || true
chmod +x "$DEST/docker/"*.sh 2>/dev/null || true
chmod +x "$DEST/uninstall.sh" 2>/dev/null || true

bold "running install.sh $*"
exec bash "$DEST/install.sh" "$@"
