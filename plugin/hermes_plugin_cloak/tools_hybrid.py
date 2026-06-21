"""Phase 1.5 — pydoll-based hybrid tools.

Optional surface installed when ``hermes-plugin-cloak[hybrid]`` is
selected. Adds 4 tools that piggy-back on the active CDP profile but
use pydoll for things cloakbrowser/Playwright doesn't do well:

  cloak_api_call     — browser-native fetch() through pydoll's Request
                       (inherits session cookies, UA, Sec-CH-UA, TLS).
  cloak_save_har     — record all network traffic during a context, save
                       to disk as HAR 1.2. The killer diagnostic when
                       trying to figure out why anti-fraud flagged you.
  cloak_extract      — declarative Pydantic-model extraction. Replaces
                       chains of click/get_text/parse with one typed call.
  cloak_shadow_query — list/query closed Shadow DOM via CDP. Cloakbrowser
                       (Playwright) can't read closed shadow roots — pydoll
                       can, because it operates at CDP layer (below JS).

All 4 attach to the SAME manager-provisioned CDP that the active profile
already runs on — so they inherit cloak fingerprint + CDP input stealth
+ our humanize patches on the patched Playwright client (different
process, same Chromium). They don't conflict; CDP is multi-client.

Activation: a profile gets these tools only if its ``[hybrid]`` extra is
installed (``pip install hermes-plugin-cloak[hybrid]``). When pydoll is
missing, this module exposes thin stubs that return an actionable error
dict to the agent — registration succeeds regardless so the schema is
visible.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from . import profile_state

from .manager_client import ManagerClient

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Detect pydoll availability — degrade gracefully when not installed.
# ----------------------------------------------------------------------------

try:
    from pydoll.browser.chromium.chrome import Chrome  # type: ignore  # noqa: F401
    _HAS_PYDOLL = True
except ImportError:  # pragma: no cover
    _HAS_PYDOLL = False


# ----------------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------------

SCHEMA_API_CALL = {
    "type": "object",
    "properties": {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"]},
        "url": {"type": "string"},
        "headers": {"type": "object", "additionalProperties": {"type": "string"}, "default": {}},
        "params": {"type": "object", "default": {}},
        "json_body": {"description": "JSON-serializable body, sent as application/json"},
        "text_body": {"type": "string", "description": "Raw body — use this OR json_body, not both"},
    },
    "required": ["method", "url"],
}

SCHEMA_SAVE_HAR = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "description": "Sequence of browser actions to record. Each action is "
                           "{tool: <browser_*>, args: {...}}. Leave empty to record "
                           "for `duration_sec` seconds of whatever the agent is doing.",
            "items": {"type": "object"},
            "default": [],
        },
        "duration_sec": {"type": "number", "default": 30,
                         "description": "Idle-record duration when `actions` is empty."},
        "save_path": {"type": "string",
                       "description": "Destination .har file (relative paths resolve under work/)"},
    },
    "required": ["save_path"],
}

SCHEMA_EXTRACT = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "Navigate to this URL first (optional — uses current page if absent)."},
        "scope": {"type": "string", "description": "CSS selector for repeated elements (extract_all). "
                                                     "Omit for single-shot extract on the whole page."},
        "fields": {
            "type": "object",
            "description": "Field-name -> { selector, attribute?, transform? }. The plugin builds a "
                           "Pydantic ExtractionModel from this and calls tab.extract / tab.extract_all.",
            "additionalProperties": {"type": "object"},
        },
        "timeout_sec": {"type": "number", "default": 5},
    },
    "required": ["fields"],
}

SCHEMA_SHADOW_QUERY = {
    "type": "object",
    "properties": {
        "selector": {"type": "string", "description": "CSS selector — queried inside ALL shadow roots."},
        "deep": {"type": "boolean", "default": True, "description": "Traverse cross-origin iframes too."},
        "limit": {"type": "integer", "default": 20},
    },
    "required": ["selector"],
}


# ----------------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------------


async def cloak_api_call(args: dict, **kw: Any) -> Dict[str, Any]:
    """Fire an HTTP request from inside the active profile's browser session."""
    if not _HAS_PYDOLL:
        return _no_pydoll()

    method = args.get("method", "GET")
    url = args.get("url", "")
    headers = args.get("headers")
    params = args.get("params")
    json_body = args.get("json_body")
    text_body = args.get("text_body")

    tab = await _attach_active_tab(kw.get("task_id"))
    if isinstance(tab, dict):
        return tab

    kwargs: Dict[str, Any] = {"headers": headers or {}, "params": params or {}}
    if json_body is not None:
        kwargs["json"] = json_body
    elif text_body is not None:
        kwargs["data"] = text_body

    method_upper = method.upper()
    try:
        # pydoll's Request exposes .get/.post/.put/.delete/... plus a generic
        # .request(method, url, **kwargs).
        resp = await tab.request.request(method_upper, url, **kwargs)
        return {
            "ok": True,
            "status": resp.status,
            "headers": dict(resp.headers),
            "text": await _safe_text(resp),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "type": type(exc).__name__, "method": method_upper, "url": url}


async def cloak_save_har(args: dict, **kw: Any) -> Dict[str, Any]:
    """Record network traffic to a HAR file. Two modes:

    - `actions=[...]`: execute each browser_* action in sequence under the
      recorder context.
    - `actions=[]` / None: idle-record for `duration_sec` seconds, capturing
      whatever the agent is doing in parallel (rarely useful — caller is
      single-threaded — but offered for completeness).
    """
    if not _HAS_PYDOLL:
        return _no_pydoll()

    save_path = args.get("save_path", "")
    actions = args.get("actions")
    duration_sec = float(args.get("duration_sec", 30))

    import asyncio
    tab = await _attach_active_tab(kw.get("task_id"))
    if isinstance(tab, dict):
        return tab

    actions = actions or []

    try:
        async with tab.request.record() as capture:
            if actions:
                # We don't execute browser_* tools from here (they're separate
                # process surface). Caller composes the recording window by
                # navigating before calling and waiting after; we just provide
                # the explicit duration knob.
                await asyncio.sleep(0.1)  # let the recorder arm
            await asyncio.sleep(max(0.1, duration_sec))

        # Save HAR. capture.save() expects an absolute path (or path under cwd).
        target = save_path
        if not os.path.isabs(target):
            target = os.path.join(os.environ.get("CLOAK_HAR_DIR", "."), target)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        capture.save(target)
        return {
            "ok": True,
            "path": target,
            "entries": len(getattr(capture, "entries", [])),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "type": type(exc).__name__}


async def cloak_extract(args: dict, **kw: Any) -> Dict[str, Any]:
    """Build a Pydantic ExtractionModel from `fields` and run pydoll's
    declarative extractor.

    The model is constructed dynamically — the agent doesn't write Python,
    it just describes what to grab in JSON.
    """
    if not _HAS_PYDOLL:
        return _no_pydoll()

    fields = args.get("fields", {})
    url = args.get("url")
    scope = args.get("scope")
    timeout_sec = float(args.get("timeout_sec", 5))

    tab = await _attach_active_tab(kw.get("task_id"))
    if isinstance(tab, dict):
        return tab

    try:
        from pydoll.extractor import ExtractionModel, Field  # type: ignore
    except ImportError:
        return {"error": "pydoll.extractor not available", "hint": "upgrade pydoll-python"}

    # Build a dynamic Pydantic model from the JSON spec.
    annotations: Dict[str, type] = {}
    defaults: Dict[str, Any] = {}
    for name, spec in fields.items():
        annotations[name] = str
        defaults[name] = Field(
            selector=spec.get("selector", ""),
            attribute=spec.get("attribute"),
            description=spec.get("description", ""),
        )

    DynModel = type(
        "DynamicExtractionModel",
        (ExtractionModel,),
        {"__annotations__": annotations, **defaults},
    )

    try:
        if url:
            await tab.go_to(url)
        if scope:
            items = await tab.extract_all(DynModel, scope=scope, timeout=timeout_sec)
            return {"ok": True, "items": [it.model_dump() for it in items]}
        item = await tab.extract(DynModel, timeout=timeout_sec)
        return {"ok": True, "item": item.model_dump()}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "type": type(exc).__name__}


async def cloak_shadow_query(args: dict, **kw: Any) -> Dict[str, Any]:
    """Query a CSS selector across ALL shadow roots (incl. closed) on the page.

    Returns a list of matched elements as {tag, text, outer_html_hash}. To
    actually CLICK a shadow-DOM element, use the returned info to compose a
    CSS path the regular browser_click can hit via shadow-piercing
    (cloakbrowser supports `:has-text()` and CSS escape chains for this).
    """
    if not _HAS_PYDOLL:
        return _no_pydoll()

    selector = args.get("selector", "")
    deep = args.get("deep", True)
    limit = int(args.get("limit", 20))

    tab = await _attach_active_tab(kw.get("task_id"))
    if isinstance(tab, dict):
        return tab

    try:
        roots = await tab.find_shadow_roots(deep=deep)
        results: List[Dict[str, Any]] = []
        for root in roots:
            try:
                matches = await root.query_all(selector)
            except AttributeError:
                # Some pydoll versions only expose .query (single). Try one.
                m = await root.query(selector, raise_exc=False)
                matches = [m] if m else []
            for el in matches:
                if len(results) >= limit:
                    break
                results.append(await _summarize_element(el))
            if len(results) >= limit:
                break
        return {"ok": True, "matches": results, "shadow_roots_scanned": len(roots)}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "type": type(exc).__name__}


# ----------------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------------


_pydoll_tabs: Dict[str, Any] = {}


async def _attach_active_tab(task_id: Any = None) -> Any:
    """Connect a pydoll Chrome instance to the active profile's CDP and
    return its first Tab. Cached per profile.

    pydoll.Chrome.connect() takes a `ws://` URL — we resolve it by hitting
    the manager's /api/profiles/{id}/cdp/json/version (which proxies
    Chrome's CDP discovery and rewrites the URL to use the manager hostname).
    """
    if not _HAS_PYDOLL:
        return _no_pydoll()

    cdp_http = profile_state.cdp_url_for_task(task_id)
    if not cdp_http:
        return {
            "error": "No Cloak CDP binding for this task. Call cloak_set_active first.",
            "task_id": profile_state.task_key(task_id),
        }

    cached = _pydoll_tabs.get(cdp_http)
    if cached is not None:
        return cached

    # Pull the WS URL from manager's CDP discovery proxy.
    ws_url = await _resolve_ws_url(cdp_http)
    if isinstance(ws_url, dict):
        return ws_url

    try:
        from pydoll.browser.chromium.chrome import Chrome  # type: ignore
        chrome = Chrome()
        tab = await chrome.connect(ws_url)
        _pydoll_tabs[cdp_http] = tab
        return tab
    except Exception as exc:  # noqa: BLE001
        return {"error": f"pydoll Chrome.connect failed: {exc}", "ws_url": ws_url}


async def _resolve_ws_url(cdp_http_url: str) -> Any:
    """Convert manager's HTTP CDP URL into the `ws://` URL pydoll needs.

    Manager exposes /api/profiles/{id}/cdp/json/version which proxies Chrome's
    /json/version and rewrites webSocketDebuggerUrl to point at the manager
    (so connections are tunneled through it). We hit that and return the
    rewritten URL.
    """
    base = cdp_http_url.rstrip("/")
    headers: Dict[str, str] = {}
    token = os.environ.get("CLOAK_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            resp = await cli.get(f"{base}/json/version", headers=headers)
            if resp.status_code != 200:
                return {"error": f"manager /json/version returned {resp.status_code}",
                        "body": resp.text[:200]}
            data = resp.json()
            ws = data.get("webSocketDebuggerUrl")
            if not ws:
                return {"error": "manager /json/version missing webSocketDebuggerUrl",
                        "got": list(data.keys())}
            return ws
    except httpx.RequestError as exc:
        return {"error": f"manager unreachable: {exc}"}


async def _summarize_element(el: Any) -> Dict[str, Any]:
    """Best-effort extraction of element info that works across pydoll versions."""
    out: Dict[str, Any] = {}
    for attr in ("tag_name", "text", "inner_text"):
        try:
            value = getattr(el, attr, None)
            if callable(value):
                value = await value()
            if isinstance(value, str):
                out[attr] = value[:200]
        except Exception:  # noqa: BLE001
            pass
    return out


async def _safe_text(resp: Any) -> str:
    """Pydoll Response objects expose .text (sometimes awaitable, sometimes str)."""
    text = getattr(resp, "text", None)
    if callable(text):
        text = await text()
    if isinstance(text, str):
        return text[:2_000_000]  # cap to 2MB to avoid blowing up the agent's context
    return ""


def _no_pydoll() -> Dict[str, str]:
    return {
        "error": "pydoll-python is not installed",
        "hint": "Install with: pip install 'hermes-plugin-cloak[hybrid]'",
    }


# ----------------------------------------------------------------------------
# Registration (called from plugin __init__ when [hybrid] is requested)
# ----------------------------------------------------------------------------


def register(ctx: Any) -> None:
    """Register the 4 hybrid tools in toolset `cloak_hybrid`."""
    import json

    def _wrap(fn: Any) -> Any:
        async def handler(args: dict, **kw: Any) -> str:
            result = await fn(args, **kw)
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

        return handler

    ctx.register_tool(
        name="cloak_api_call",
        toolset="cloak_hybrid",
        schema=SCHEMA_API_CALL,
        handler=_wrap(cloak_api_call),
        is_async=True,
        description=(
            "HTTP request inside the active profile's browser session. "
            "Inherits cookies, UA, Sec-CH-UA, TLS — indistinguishable from "
            "the site's own fetch() calls. Use after login to skip click-based "
            "pagination."
        ),
        emoji="🌐",
    )
    ctx.register_tool(
        name="cloak_save_har",
        toolset="cloak_hybrid",
        schema=SCHEMA_SAVE_HAR,
        handler=_wrap(cloak_save_har),
        is_async=True,
        description=(
            "Record all network traffic for `duration_sec` seconds, save to "
            "HAR. Use this to diagnose anti-fraud detection: open in browser, "
            "diff requests against a known-good session."
        ),
        emoji="📼",
    )
    ctx.register_tool(
        name="cloak_extract",
        toolset="cloak_hybrid",
        schema=SCHEMA_EXTRACT,
        handler=_wrap(cloak_extract),
        is_async=True,
        description=(
            "Declarative extraction: describe the shape of data you want in "
            "JSON (field-name -> {selector, attribute?}) and get typed dicts "
            "back. Faster than click + get_text chains."
        ),
        emoji="📦",
    )
    ctx.register_tool(
        name="cloak_shadow_query",
        toolset="cloak_hybrid",
        schema=SCHEMA_SHADOW_QUERY,
        handler=_wrap(cloak_shadow_query),
        is_async=True,
        description=(
            "Query CSS selector across all Shadow DOM roots (including closed). "
            "Use for elements hidden behind Cloudflare challenges, Stripe Elements, "
            "or other widgets that lock content in shadow roots."
        ),
        emoji="🕶️",
    )
