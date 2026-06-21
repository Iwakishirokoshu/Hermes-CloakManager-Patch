# MANIFEST — hermes-cloak-patch v0.1.1

GitHub: <https://github.com/Iwakishirokoshu/Hermes-CloakManager-Patch>

## Components

| Component | Source | Notes |
|-----------|--------|-------|
| plugin/ | hermes-plugin-cloak | v0.1.1, browser guard for 9 native tools |
| install.sh | This patch | idempotent, auto-detect uv/pip, YAML merge |
| scripts/bootstrap.sh | This patch | one-liner curl|bash installer from GitHub |
| docker/run-manager.sh | This patch | Pulls cloakhq/cloakbrowser-manager:latest |
| nginx/cloak-cdp-proxy.conf.template | This patch | server {} only |
| nginx/cloak-upgrade-map.conf | This patch | map directive in http {} scope |
| scripts/merge_plugin_enabled.py | This patch | YAML merge, idempotent, ruamel or PyYAML |
| scripts/verify.sh | This patch | 10 smoke checks |
| scripts/get-token.sh | This patch | helper to print CLOAK_AUTH_TOKEN |
| skills/cloak-account-registration | This patch, sanitized (no TCCD refs) | |
| skills/cloak-proxy-pool | This patch | |
| skills/notletters-api | This patch, sanitized (no real emails) | |
| client/examples/*.bat.example | This patch | Windows SSH-tunnel templates |

## Plugin browser-guard coverage

| Tool | Treatment |
|------|-----------|
| `browser_navigate` | **Fully replaced** by Cloak Playwright (humanized) |
| `browser_snapshot` | Guarded — refuse when CLOAK_MANAGER_URL set & BROWSER_CDP_URL empty |
| `browser_console` | Guarded |
| `browser_back` | Guarded |
| `browser_get_images` | Guarded |
| `browser_vision` | Guarded |
| `browser_click` | Guarded (native CDP routing to Cloak Manager) |
| `browser_type` | Guarded |
| `browser_press` | Guarded |
| `browser_scroll` | Guarded |
| `browser_screenshot` | Not present in Hermes v0.17, skipped automatically |

## Pre-flight test status

| Check | Status |
|-------|--------|
| `bash -n install.sh` | OK |
| `bash -n verify.sh` | OK |
| `bash -n bootstrap.sh` | OK |
| `bash -n run-manager.sh` | OK |
| `bash -n uninstall.sh` | OK |
| `python -m py_compile merge_plugin_enabled.py` | OK |
| `python -m py_compile plugin/hermes_plugin_cloak/__init__.py` | OK |
| `pytest plugin/tests/test_browser_guard.py` | 6/6 PASSED |
| YAML merge: 4 scenarios | OK |
| End-to-end install on clean Ubuntu 24.04 / Hermes 0.17.0 | PASSED |
| Idempotent re-install | PASSED — token kept, container reused |
| `hermes plugins list` shows cloak | PASSED — enabled, v0.1.1 |
| nginx CDP proxy injection | PASSED — :8081 returns 200 without auth header |
| Direct manager auth check | PASSED — :8080 returns 401 without auth, 200 with Bearer |
| Docker container health | PASSED — running, healthy |
| Browser guard live test | PASSED — `patched + guarded 9 native browser tools` in agent.log |

## Release checklist

1. `bash scripts/sanitize-for-release.sh` — exit 0
2. `rg -i 'a7c2d0|147\.182|204\.48|hermes_vps_ed25519' .` — empty
3. End-to-end install on clean Ubuntu 24.04 VM via `bootstrap.sh`
4. `git tag -a v0.1.1 -m "release"` (optional)
