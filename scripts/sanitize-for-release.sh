#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAIL=0
check() {
  if grep -rE "$1" "$ROOT" --include='*.md' --include='*.py' --include='*.sh' --include='*.bat' --include='*.env*' 2>/dev/null | grep -v sanitize-for-release | grep -qv example.com; then
    echo "FAIL pattern $1 found:"; grep -rE "$1" "$ROOT" 2>/dev/null | head -5; FAIL=1
  fi
}
check '147\.182\.167\.220'
check '206\.189\.238\.24'
check 'xaround\.tech'
check 'sk-[A-Za-z0-9]{20,}'
[[ -f "$ROOT/config/manager.env" ]] && { echo "FAIL: real manager.env in package"; FAIL=1; }
[[ $FAIL -eq 0 ]] && echo "sanitize OK" || exit 1