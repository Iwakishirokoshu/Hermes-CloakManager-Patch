# hermes-plugin-cloak

Hermes plugin that bolts the local CloakBrowser-Manager stealth stack onto a
Hermes profile. Two distinct surfaces:

1. **Profile management tools** (`cloak_create_profile`, `cloak_launch`,
   `cloak_set_active`, `cloak_stop`, `cloak_list_profiles`,
   `cloak_detect_captcha`, `cloak_solve_captcha`) — the agent uses these to
   spin up / select / kill stealth browser profiles on the manager and to
   detect & solve 22+ captcha kinds via a CapSolver↔2captcha router.

2. **`browser_*` input overrides** — replaces the native `browser_click`,
   `browser_type`, `browser_fill`, `browser_press`, `browser_hover`,
   `browser_drag`, `browser_scroll` so that every user-input action goes
   through an in-process Playwright client, attached to the
   manager-provisioned CDP, and runs through cloakbrowser's humanize layer
   **with pydoll-derived motor math** (Fitts's Law timing, minimum-jerk
   velocity, velocity-inverse Gaussian tremor, distance-gated overshoot,
   QWERTY-typo simulation, etc.).

Read-only tools (`browser_navigate`, `browser_snapshot`, `browser_screenshot`,
`browser_console`, `browser_get_images`, `browser_vision`, `browser_back`)
are **not overridden** — they keep going through the native `agent-browser`
CLI, which already inherits stealth+fingerprint via the manager CDP proxy.

## How the math gets there

`cloakbrowser.human.patch_browser_async` patches Playwright `Page.click /
type / fill / mouse.move` etc. on the browser instance we connect to. Its
behaviour comes from `cloakbrowser/human/mouse_async.py` and
`cloakbrowser/human/keyboard_async.py`. Before cloakbrowser is imported,
`hermes_plugin_cloak.humanize.install()` registers our drop-in replacements
for those two modules in `sys.modules`. Python then resolves the imports
to our versions, which expose the same public surface but with pydoll's
motor-control math under the hood.

This is a tiny, surgical change: we keep ALL of cloakbrowser's Page /
Frame / ElementHandle / Locator wrapping (~2500 lines of well-tested code)
and only swap the two leaf modules where the actual mouse curves and
typing timings live (~700 lines).

## Environment

Plugin reads from the profile's `~/.hermes/profiles/<name>/.env`:

| Variable | Purpose | Default |
|---|---|---|
| `CLOAK_MANAGER_URL` | base URL of CloakBrowser-Manager | `http://127.0.0.1:41833` |
| `CLOAK_AUTH_TOKEN` | Bearer token (shared with manager) | required if manager has `AUTH_TOKEN` set |
| `BROWSER_CDP_URL` | active profile's CDP — set by `cloak_set_active` / `cloak_launch` | mutated at runtime |
| `CLOAK_HUMANIZE` | `0` to disable humanize globally (testing only) | `1` |
| `CLOAK_HUMAN_PRESET` | `default` \| `careful` | `default` |
| `TWO_CAPTCHA_API_KEY` | enables 2captcha backend (aliases: `TWOCAPTCHA_API_KEY`, `CAPTCHA_API_KEY`) | unset → backend skipped |
| `CAPSOLVER_API_KEY` | enables CapSolver backend (preferred for hCaptcha / Cloudflare / DataDome / Kasada / Akamai / Imperva) | unset → backend skipped |
| `CAPTCHA_PROVIDER` | force a backend: `auto` (default, routes per kind) / `capsolver` / `2captcha` | `auto` |

With NEITHER captcha key set, `cloak_solve_captcha` returns
`MANUAL_INTERVENTION_REQUIRED` immediately — the agent must `kanban_block`
and you solve via VNC.

## Captcha kinds

22+ supported. See [docs/captcha-providers/README.md](../docs/captcha-providers/README.md)
for the full matrix (which backend handles each kind) and per-kind `extra`
schema. Quick workflow inside the agent:

```python
det = await cloak_detect_captcha()                    # one JS roundtrip, classifies
# det == {"kind": "turnstile", "site_key": "0x4...", "page_url": "...",
#         "extra": {"action": "login"}, "confidence": "high"}

tok = await cloak_solve_captcha(kind=det["kind"],
                                 site_key=det["site_key"] or "",
                                 url=det["page_url"],
                                 extra=det["extra"])
# tok == long token  OR  "MANUAL_INTERVENTION_REQUIRED"
```

## Install

```bash
pip install -e .            # core only
pip install -e .[hybrid]    # + Phase 1.5 pydoll-based tools
pip install -e .[dev]       # + tests
```

## Tests

```bash
pytest -v
```

## License

MIT
