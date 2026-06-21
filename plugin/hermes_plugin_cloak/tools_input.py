"""Input-tool overrides: ``browser_click``, ``browser_type``, ``browser_fill``,
``browser_press``, ``browser_hover``, ``browser_drag``, ``browser_scroll``.

These are registered with ``override=True`` so they replace Hermes's
native (agent-browser-driven) versions. They route the user-input action
through an in-process Playwright client that's been patched by
``cloakbrowser.human.patch_browser_async`` — which in turn calls our
pydoll-derived motor math via the sys.modules hook in ``humanize/__init__.py``.

Read-only tools (navigate, snapshot, screenshot, console, vision,
get_images, back) are NOT touched here — they stay on agent-browser,
which is plenty stealthy for state reads and doesn't benefit from human
timing.

Each handler acquires the active CDP URL from ``$BROWSER_CDP_URL`` (set
by ``cloak_set_active`` / ``cloak_launch``) and uses ``BrowserPool`` to
get-or-create the patched page.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from .browser_pool import get_pool
from . import profile_state

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------------

_SEL_SCHEMA = {
    "type": "object",
    "properties": {
        "selector": {"type": "string", "description": "CSS selector for the element."},
    },
    "required": ["selector"],
}

SCHEMA_CLICK = {
    "type": "object",
    "properties": {
        "selector": {"type": "string"},
        "timeout_ms": {"type": "integer", "default": 30000},
        "force": {"type": "boolean", "default": False},
    },
    "required": ["selector"],
}

SCHEMA_TYPE = {
    "type": "object",
    "properties": {
        "selector": {"type": "string"},
        "text": {"type": "string"},
        "timeout_ms": {"type": "integer", "default": 30000},
    },
    "required": ["selector", "text"],
}

SCHEMA_FILL = SCHEMA_TYPE

SCHEMA_PRESS = {
    "type": "object",
    "properties": {
        "selector": {"type": "string"},
        "key": {"type": "string", "description": "e.g. 'Enter', 'Tab', 'ArrowDown'"},
        "timeout_ms": {"type": "integer", "default": 30000},
    },
    "required": ["selector", "key"],
}

SCHEMA_HOVER = SCHEMA_CLICK

SCHEMA_DRAG = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "target": {"type": "string"},
        "timeout_ms": {"type": "integer", "default": 30000},
    },
    "required": ["source", "target"],
}

SCHEMA_SCROLL = {
    "type": "object",
    "properties": {
        "selector": {"type": "string", "description": "Element to scroll into view (optional)."},
        "delta_x": {"type": "integer", "default": 0},
        "delta_y": {"type": "integer", "default": 0},
    },
}


# ----------------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------------


async def browser_click(args: dict, **kw: Any) -> Any:
    ref = args.get("ref", "")
    selector = args.get("selector", "")
    timeout_ms = args.get("timeout_ms", 30000)
    force = args.get("force", False)
    task_id = kw.get("task_id")
    element = ref or selector
    if element.startswith("@"):
        from tools.browser_tool import browser_click as native_click

        return native_click(ref=element, task_id=task_id)
    if not selector:
        return {"error": "Provide ref (@e1) or CSS selector."}
    page = await _active_page(task_id)
    if isinstance(page, dict):
        return page  # error dict
    try:
        await page.click(selector, timeout=timeout_ms, force=force)
        return {"ok": True, "selector": selector}
    except Exception as exc:  # noqa: BLE001
        return _error(exc, selector=selector)


async def browser_type(args: dict, **kw: Any) -> Any:
    ref = args.get("ref", "")
    selector = args.get("selector", "")
    text = args.get("text", "")
    timeout_ms = args.get("timeout_ms", 30000)
    task_id = kw.get("task_id")
    element = ref or selector
    if element.startswith("@"):
        from tools.browser_tool import browser_type as native_type

        return native_type(ref=element, text=text, task_id=task_id)
    if not selector:
        return {"error": "Provide ref (@e1) or CSS selector."}
    page = await _active_page(task_id)
    if isinstance(page, dict):
        return page
    try:
        # cloakbrowser's patched page.type() applies human_click + human_type internally.
        await page.type(selector, text, timeout=timeout_ms)
        return {"ok": True, "selector": selector, "chars": len(text)}
    except Exception as exc:  # noqa: BLE001
        return _error(exc, selector=selector)


async def browser_fill(args: dict, **kw: Any) -> Any:
    ref = args.get("ref", "")
    selector = args.get("selector", "")
    text = args.get("text", "")
    timeout_ms = args.get("timeout_ms", 30000)
    task_id = kw.get("task_id")
    element = ref or selector
    if element.startswith("@"):
        from tools.browser_tool import browser_fill as native_fill

        return native_fill(ref=element, text=text, task_id=task_id)
    if not selector:
        return {"error": "Provide ref (@e1) or CSS selector."}
    page = await _active_page(task_id)
    if isinstance(page, dict):
        return page
    try:
        # cloakbrowser's patched .fill clears + types with humanize.
        await page.fill(selector, text, timeout=timeout_ms)
        return {"ok": True, "selector": selector, "chars": len(text)}
    except Exception as exc:  # noqa: BLE001
        return _error(exc, selector=selector)


async def browser_press(args: dict, **kw: Any) -> Any:
    ref = args.get("ref", "")
    selector = args.get("selector", "")
    key = args.get("key", "")
    timeout_ms = args.get("timeout_ms", 30000)
    task_id = kw.get("task_id")
    element = ref or selector
    if element.startswith("@"):
        from tools.browser_tool import browser_press as native_press

        return native_press(ref=element, key=key, task_id=task_id)
    if not selector:
        return {"error": "Provide ref (@e1) or CSS selector."}
    page = await _active_page(task_id)
    if isinstance(page, dict):
        return page
    try:
        await page.press(selector, key, timeout=timeout_ms)
        return {"ok": True, "selector": selector, "key": key}
    except Exception as exc:  # noqa: BLE001
        return _error(exc, selector=selector)


async def browser_hover(args: dict, **kw: Any) -> Any:
    ref = args.get("ref", "")
    selector = args.get("selector", "")
    timeout_ms = args.get("timeout_ms", 30000)
    force = args.get("force", False)
    task_id = kw.get("task_id")
    element = ref or selector
    if element.startswith("@"):
        from tools.browser_tool import browser_hover as native_hover

        return native_hover(ref=element, task_id=task_id)
    if not selector:
        return {"error": "Provide ref (@e1) or CSS selector."}
    page = await _active_page(task_id)
    if isinstance(page, dict):
        return page
    try:
        await page.hover(selector, timeout=timeout_ms, force=force)
        return {"ok": True, "selector": selector}
    except Exception as exc:  # noqa: BLE001
        return _error(exc, selector=selector)


async def browser_drag(args: dict, **kw: Any) -> Dict[str, Any]:
    source = args.get("source", "")
    target = args.get("target", "")
    timeout_ms = args.get("timeout_ms", 30000)
    task_id = kw.get("task_id")
    page = await _active_page(task_id)
    if isinstance(page, dict):
        return page
    try:
        # Patched page exposes dragAndDrop on frame level (cloakbrowser also
        # patches Frame.dragAndDrop). Use page.dragAndDrop() if available;
        # otherwise fall back to manual mouse choreography (cloak's frame-level
        # path already does this and is reachable here via page.main_frame).
        if hasattr(page, "drag_and_drop"):
            await page.drag_and_drop(source, target, timeout=timeout_ms)
        else:
            await page.main_frame.drag_and_drop(source, target, timeout=timeout_ms)
        return {"ok": True, "source": source, "target": target}
    except Exception as exc:  # noqa: BLE001
        return _error(exc, selector=f"{source} -> {target}")


async def browser_scroll(args: dict, **kw: Any) -> Dict[str, Any]:
    selector = args.get("selector")
    delta_x = args.get("delta_x", 0)
    delta_y = args.get("delta_y", 0)
    task_id = kw.get("task_id")
    page = await _active_page(task_id)
    if isinstance(page, dict):
        return page
    try:
        if selector:
            # cloakbrowser's human_scroll_into_view operates on a locator;
            # the patched page.click already does this on click, but for
            # explicit scrolls we use the locator directly.
            locator = page.locator(selector).first
            await locator.scroll_into_view_if_needed()
            return {"ok": True, "scrolled_into_view": selector}

        # Raw delta scroll — Playwright mouse.wheel goes through
        # patched page.mouse if cloakbrowser wrapped it (it does for
        # mouse.move / mouse.click; wheel is not always wrapped). We
        # fall back to direct wheel call.
        await page.mouse.wheel(delta_x, delta_y)
        return {"ok": True, "delta_x": delta_x, "delta_y": delta_y}
    except Exception as exc:  # noqa: BLE001
        return _error(exc, selector=selector or f"wheel({delta_x},{delta_y})")


# ----------------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------------


async def _active_page(task_id: Any = None) -> Any:
    """Return the patched page for the active profile, or an error dict."""
    cdp_url = profile_state.cdp_url_for_task(task_id)
    if not cdp_url:
        return {
            "error": "No Cloak CDP binding for this task. Call cloak_set_active(profile=...) "
                     "or cloak_launch(profile=...) first.",
            "task_id": profile_state.task_key(task_id),
        }
    preset = os.environ.get("CLOAK_HUMAN_PRESET", "default")
    try:
        client = await get_pool().get(cdp_url, preset=preset)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"BrowserPool.get failed: {exc}", "cdp_url": cdp_url}
    return client.page


def _error(exc: Exception, **context: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"error": str(exc), "type": type(exc).__name__}
    out.update(context)
    return out
