"""Route read-only browser tools through Cloak CDP when configured."""
from __future__ import annotations

import json
import logging
import os
import asyncio
from typing import Any

from .hooks import _ensure_cloak_active_sync
from . import profile_state

logger = logging.getLogger(__name__)

SCHEMA_NAVIGATE = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "The URL to navigate to."},
    },
    "required": ["url"],
}


async def _navigate_via_cloak(url: str, cdp_url: str) -> str:
    from .browser_pool import get_pool

    if not cdp_url:
        return json.dumps({"error": "No Cloak CDP binding for this task after auto-launch"})
    outer_timeout_ms = _env_int("CLOAK_NAV_OUTER_TIMEOUT_MS", 90_000)
    try:
        return await asyncio.wait_for(
            _navigate_via_cloak_inner(url, cdp_url),
            timeout=max(5, outer_timeout_ms / 1000),
        )
    except asyncio.TimeoutError:
        await get_pool().drop(cdp_url)
        return json.dumps(
            {
                "success": False,
                "error": f"browser_navigate timed out after {outer_timeout_ms}ms; CDP pool was reset",
                "type": "TimeoutError",
                "url": url,
                "via": "cloak-playwright",
                "recoverable": True,
            },
            ensure_ascii=False,
        )


async def _navigate_via_cloak_inner(url: str, cdp_url: str) -> str:
    from .browser_pool import get_pool

    preset = os.environ.get("CLOAK_HUMAN_PRESET", "default")
    timeout_ms = _env_int("CLOAK_NAV_TIMEOUT_MS", 75_000)
    settle_ms = _env_int("CLOAK_POST_NAV_SETTLE_MS", 2_500)
    attempts = max(1, _env_int("CLOAK_NAV_ATTEMPTS", 2))
    pool = get_pool()
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        client = await pool.get(cdp_url, preset=preset)
        try:
            try:
                await asyncio.wait_for(
                    client.page.goto(url, wait_until="commit", timeout=timeout_ms),
                    timeout=max(5, (timeout_ms / 1000) + 5),
                )
            except Exception as exc:  # noqa: BLE001
                await _stop_loading(client.page)
                meta = await _page_meta(client.page)
                if _looks_usable_after_timeout(exc, meta):
                    return _nav_result(url, cdp_url, meta, attempt, warning=str(exc))
                raise

            if settle_ms > 0:
                await asyncio.sleep(settle_ms / 1000)

            meta = await _page_meta(client.page)
            return _nav_result(url, cdp_url, meta, attempt)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if _should_retry_navigation(exc) and attempt < attempts:
                logger.warning(
                    "cloak browser_navigate transient failure on attempt %s/%s; reconnecting CDP: %s",
                    attempt,
                    attempts,
                    exc,
                )
                await pool.drop(cdp_url)
                await asyncio.sleep(min(2.0 * attempt, 5.0))
                continue
            raise

    assert last_error is not None
    raise last_error


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


async def _page_meta(page: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    try:
        meta["current_url"] = page.url
    except Exception:  # noqa: BLE001
        meta["current_url"] = ""
    try:
        meta["title"] = await asyncio.wait_for(page.title(), timeout=3)
    except Exception:  # noqa: BLE001
        meta["title"] = ""
    try:
        meta.update(
            await asyncio.wait_for(
                page.evaluate(
                    """() => ({
                        ready_state: document.readyState,
                        body_chars: document.body ? document.body.innerText.length : 0,
                        field_count: document.querySelectorAll('input, select, textarea, button').length
                    })"""
                ),
                timeout=3,
            )
        )
    except Exception as exc:  # noqa: BLE001
        meta["page_meta_error"] = str(exc)
    return meta


async def _stop_loading(page: Any) -> None:
    try:
        await asyncio.wait_for(page.evaluate("() => window.stop()"), timeout=3)
    except Exception:  # noqa: BLE001
        return


def _looks_usable_after_timeout(exc: Exception, meta: dict[str, Any]) -> bool:
    """A slow proxy can make Playwright time out even after the page is usable."""
    text = f"{type(exc).__name__}: {exc}"
    if "Timeout" not in text:
        return False
    if meta.get("ready_state") in {"interactive", "complete"}:
        return True
    if meta.get("title") or int(meta.get("field_count") or 0) > 0:
        return True
    return False


def _should_retry_navigation(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    transient_markers = (
        "TargetClosed",
        "has been closed",
        "WebSocket",
        "connect_over_cdp",
        "403 Forbidden",
        "ERR_TUNNEL_CONNECTION_FAILED",
        "Timeout",
    )
    return any(marker in text for marker in transient_markers)


def _nav_result(
    requested_url: str,
    cdp_url: str,
    meta: dict[str, Any],
    attempt: int,
    warning: str | None = None,
) -> str:
    result: dict[str, Any] = {
        "ok": True,
        "url": requested_url,
        "current_url": meta.get("current_url", ""),
        "title": meta.get("title", ""),
        "ready_state": meta.get("ready_state", ""),
        "field_count": meta.get("field_count", 0),
        "body_chars": meta.get("body_chars", 0),
        "attempt": attempt,
        "via": "cloak-playwright",
        "cdp_url": cdp_url[:80],
    }
    if warning:
        result["warning"] = warning
    if _is_outlook_marketing_redirect(requested_url, result):
        result["warning"] = (
            "outlook_inbox_deeplink_landed_on_microsoft_marketing_page; "
            "sign in via login.live.com, cancel passkey prompts if shown, then retry inbox"
        )
    return json.dumps(
        result,
        ensure_ascii=False,
    )


def _is_outlook_marketing_redirect(requested_url: str, result: dict[str, Any]) -> bool:
    if "outlook.live.com/mail" not in requested_url:
        return False
    current_url = str(result.get("current_url", "")).lower()
    title = str(result.get("title", "")).lower()
    return "microsoft.com" in current_url and "outlook" in title


def browser_navigate(args: dict, **kw: Any) -> str:
    """This override replaces Hermes browser_navigate — always route through Cloak."""
    url = args.get("url", "")
    task_id = kw.get("task_id")
    _ensure_cloak_active_sync(task_id)
    cdp = profile_state.cdp_url_for_task(task_id)
    if not cdp:
        return json.dumps(
            {
                "error": "Cloak profile not active for this task. "
                "Call cloak_set_active(profile=acc-<task_id>) first.",
                "task_id": profile_state.task_key(task_id),
            },
            ensure_ascii=False,
        )
    from model_tools import _run_async

    logger.info(
        "cloak browser_navigate via Playwright pool: %s task=%s cdp=%s",
        url,
        profile_state.task_key(task_id),
        cdp[:60],
    )
    try:
        return _run_async(_navigate_via_cloak(url, cdp))
    except Exception as exc:  # noqa: BLE001
        logger.exception("cloak browser_navigate failed")
        return json.dumps({"error": str(exc), "type": type(exc).__name__, "via": "cloak-playwright"})
