"""Profile-management tools registered in the `cloak` toolset.

  cloak_create_profile  — POST /api/profiles
  cloak_launch          — POST /api/profiles/{id}/launch + mutate BROWSER_CDP_URL env
  cloak_set_active      — find-or-create + launch + mutate env (one-shot)
  cloak_detect_captcha  — run in-page JS to classify the captcha + extract site_key
  cloak_solve_captcha   — route through 2captcha / capsolver, fall through to
                          MANUAL_INTERVENTION_REQUIRED on every failure
  cloak_stop            — POST /api/profiles/{id}/stop + clear BROWSER_CDP_URL
  cloak_list_profiles   — GET  /api/profiles (lookup helper for the agent)

All tools are async. They return plain dicts/strings — no Exceptions
escape into the agent's reasoning loop (failures get caught and surfaced
as error fields the LLM can read).

Mutating ``os.environ['BROWSER_CDP_URL']`` is the integration point with
Hermes's native browser tools — they read this env on every invocation
(see hermes-agent-main/tools/browser_tool.py:288-309 in the recon).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .browser_pool import get_pool
from .captcha import (
    MANUAL_INTERVENTION_REQUIRED,
    CaptchaRouter,
    ManualInterventionRequired,
    detect_in_playwright_page,
)
from .manager_client import ManagerClient, ManagerError
from . import profile_state

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Tool schemas (for Hermes registry.register)
# ----------------------------------------------------------------------------

SCHEMA_CREATE = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Profile name (unique). E.g. 'twitter-scout' or 'acc-x-20260616-i47'."},
        "proxy": {"type": "string", "description": "Optional proxy URL: http://user:pass@host:port or socks5://...", "default": ""},
        "humanize": {"type": "boolean", "description": "Enable cloakbrowser humanize on this profile. Always true for stealth.", "default": True},
        "human_preset": {"type": "string", "enum": ["default", "careful"], "default": "default"},
        "headless": {"type": "boolean", "default": False},
        "geoip": {"type": "boolean", "description": "Auto-spoof timezone/locale from proxy GeoIP.", "default": True},
        "tags": {"type": "array", "items": {"type": "string"}, "default": []},
        "notes": {"type": "string", "default": ""},
        "auto_launch": {"type": "boolean", "default": False},
    },
    "required": ["name"],
    "additionalProperties": True,
}

SCHEMA_LAUNCH = {
    "type": "object",
    "properties": {
        "profile": {"type": "string", "description": "Profile id (UUID) OR name to launch."},
        "allow_profile_switch": {
            "type": "boolean",
            "default": False,
            "description": "Allow this task to switch away from its remembered profile.",
        },
    },
    "required": ["profile"],
}

SCHEMA_SET_ACTIVE = {
    "type": "object",
    "properties": {
        "profile": {"type": "string", "description": "Profile name (created if missing) to make active."},
        "create_if_missing": {"type": "boolean", "default": True},
        "humanize": {"type": "boolean", "default": True},
        "human_preset": {"type": "string", "enum": ["default", "careful"], "default": "default"},
        "proxy": {"type": "string", "default": ""},
        "allow_profile_switch": {"type": "boolean", "default": False},
    },
    "required": ["profile"],
}

SCHEMA_STOP = {
    "type": "object",
    "properties": {
        "profile": {"type": "string", "description": "Profile id or name to stop."},
    },
    "required": ["profile"],
}

SCHEMA_LIST = {"type": "object", "properties": {}}

_KIND_ENUM = [
    "recaptcha_v2", "recaptcha_v3", "recaptcha_enterprise",
    "hcaptcha", "turnstile", "funcaptcha",
    "geetest", "geetest_v4", "amazon_waf",
    "friendly_captcha", "keycaptcha", "datadome",
    "kasada", "akamai", "imperva",
    "lemin", "mtcaptcha", "cybersiara", "cutcaptcha",
    "capy", "yandex", "tencent", "image",
]

SCHEMA_SOLVE_CAPTCHA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": _KIND_ENUM,
                 "description": "Captcha family. Use cloak_detect_captcha first if unsure."},
        "site_key": {"type": "string", "description": "Public site key / pkey / sitekey. Empty for image/geetest/datadome (use extra)."},
        "url": {"type": "string", "description": "Page URL where the captcha lives."},
        "extra": {"type": "object",
                  "description": "Kind-specific extras (action, min_score, gt, challenge, captcha_url, body, iv, context, user_id, etc.). See plugin README.",
                  "default": {}},
        "provider": {"type": "string", "enum": ["auto", "capsolver", "2captcha"],
                     "description": "Force a specific backend. Default = auto (router picks).",
                     "default": "auto"},
    },
    "required": ["kind", "url"],
}

SCHEMA_DETECT_CAPTCHA = {
    "type": "object",
    "properties": {},
    "description": "Detect any captcha currently rendered on the active CloakBrowser tab. "
                   "Returns {kind, site_key, page_url, extra, confidence}. kind=null = no captcha.",
}


# ----------------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------------


async def cloak_create_profile(args: dict, **kw: Any) -> Dict[str, Any]:
    """Create a stealth profile on the manager. Returns the new profile record."""
    task_id = kw.get("task_id")
    name = args.get("name", "")
    proxy = args.get("proxy", "")
    humanize = args.get("humanize", True)
    human_preset = args.get("human_preset", "default")
    headless = args.get("headless", False)
    geoip = args.get("geoip", True)
    tags = args.get("tags")
    notes = args.get("notes", "")
    auto_launch = args.get("auto_launch", False)
    body: Dict[str, Any] = {
        "name": name,
        "humanize": humanize,
        "human_preset": human_preset,
        "headless": headless,
        "geoip": geoip,
        "tags": tags or [],
        "notes": notes,
        "auto_launch": auto_launch,
    }
    if proxy:
        body["proxy"] = proxy
    for key, value in args.items():
        if key not in body and key not in ("name",):
            body[key] = value

    async with ManagerClient() as mgr:
        try:
            result = await mgr.create_profile(**body)
        except ManagerError as exc:
            return {"error": str(exc), "status_code": exc.status_code}
    profile_id = str(result.get("id") or result.get("profile_id") or "")
    if profile_id:
        profile_state.remember_profile(
            task_id,
            profile_id=profile_id,
            profile_name=str(result.get("name") or name),
            proxy=proxy,
            source="cloak_create_profile",
        )
        result["task_id"] = profile_state.task_key(task_id)
        result["remembered_for_task"] = True
        result["launch_next_with_profile_id"] = profile_id
    return result


async def cloak_launch(args: dict, **kw: Any) -> Dict[str, Any]:
    """Launch a profile and bind its CDP URL to the current process env."""
    profile = args.get("profile", "")
    return await _launch_profile(
        profile,
        task_id=kw.get("task_id"),
        allow_profile_switch=bool(args.get("allow_profile_switch", False)),
    )


async def _launch_profile(
    profile: str,
    *,
    task_id: Any = None,
    allow_profile_switch: bool = False,
) -> Dict[str, Any]:
    binding_before = profile_state.get_binding(task_id)
    async with ManagerClient() as mgr:
        try:
            profile_id = await _resolve_profile_id(mgr, profile)
            guard = _profile_switch_guard(
                binding_before,
                requested_profile_id=profile_id,
                allow_profile_switch=allow_profile_switch,
            )
            if guard is not None:
                return guard
            resp = await mgr.launch(profile_id)
        except ManagerError as exc:
            if exc.status_code == 409 and "already running" in exc.body.lower():
                resp = {
                    "profile_id": profile_id,
                    "status": "running",
                    "cdp_url": f"/api/profiles/{profile_id}/cdp",
                    "already_running": True,
                }
            else:
                return {"error": str(exc), "status_code": exc.status_code}

        launched_profile_id = str(resp.get("profile_id") or profile_id)
        if launched_profile_id != profile_id:
            return {
                "error": "Cloak launch returned a different profile_id than requested.",
                "requested_profile_id": profile_id,
                "launched_profile_id": launched_profile_id,
                "task_id": profile_state.task_key(task_id),
            }

        cdp_rel = resp.get("cdp_url", "")
        cdp_abs = ""
        if cdp_rel:
            cdp_abs = await mgr.bind_browser_cdp_env(cdp_rel)
            cdp_profile_id = profile_state.profile_id_from_cdp(cdp_abs or cdp_rel)
            if cdp_profile_id and cdp_profile_id != profile_id:
                return {
                    "error": "Resolved CDP URL points at a different profile.",
                    "requested_profile_id": profile_id,
                    "cdp_profile_id": cdp_profile_id,
                    "cdp_url": cdp_abs,
                    "task_id": profile_state.task_key(task_id),
                }

        old_cdp = str((binding_before or {}).get("cdp_url") or "")
        if old_cdp and cdp_abs and old_cdp != cdp_abs:
            await get_pool().drop(old_cdp)

        try:
            status_after = await mgr.profile_status(profile_id)
        except ManagerError:
            status_after = {}

        profile_state.remember_profile(
            task_id,
            profile_id=profile_id,
            profile_name=str((binding_before or {}).get("profile_name") or profile),
            cdp_url=cdp_abs,
            cdp_http_url=os.environ.get("CLOAK_CDP_HTTP_URL", ""),
            proxy=(binding_before or {}).get("proxy"),
            source="cloak_launch",
        )

        return {
            "profile_id": resp.get("profile_id", profile_id),
            "profile_name": str((binding_before or {}).get("profile_name") or profile),
            "status": resp.get("status"),
            "status_after": status_after.get("status"),
            "cdp_url": cdp_abs,
            "vnc_ws_port": resp.get("vnc_ws_port"),
            "display": resp.get("display"),
            "already_running": bool(resp.get("already_running")),
            "active": True,
            "task_id": profile_state.task_key(task_id),
        }


async def cloak_set_active(args: dict, **kw: Any) -> Dict[str, Any]:
    """Find-or-create the profile, ensure it's running, set env. One-shot."""
    return await set_active_profile(
        args.get("profile", ""),
        create_if_missing=args.get("create_if_missing", True),
        humanize=args.get("humanize", True),
        human_preset=args.get("human_preset", "default"),
        proxy=args.get("proxy", ""),
        task_id=kw.get("task_id"),
        allow_profile_switch=bool(args.get("allow_profile_switch", False)),
    )


async def set_active_profile(
    profile: str,
    *,
    create_if_missing: bool = True,
    humanize: bool = True,
    human_preset: str = "default",
    proxy: str = "",
    task_id: Any = None,
    allow_profile_switch: bool = False,
) -> Dict[str, Any]:
    """Core find-or-create + launch logic (used by hooks and cloak_set_active)."""
    binding_before = profile_state.get_binding(task_id)
    async with ManagerClient() as mgr:
        try:
            existing = await mgr.get_profile(profile) if profile_state.is_uuid(profile) else await mgr.find_profile_by_name(profile)
            if existing is None:
                if not create_if_missing:
                    return {"error": f"profile '{profile}' not found"}
                existing = await mgr.create_profile(
                    name=profile,
                    humanize=humanize,
                    human_preset=human_preset,
                    proxy=proxy,
                )
                profile_state.remember_profile(
                    task_id,
                    profile_id=str(existing.get("id") or ""),
                    profile_name=str(existing.get("name") or profile),
                    proxy=proxy,
                    source="cloak_set_active.create",
                )

            profile_id = existing["id"]
            guard = _profile_switch_guard(
                binding_before,
                requested_profile_id=profile_id,
                allow_profile_switch=allow_profile_switch,
            )
            if guard is not None:
                return guard
            status = await mgr.profile_status(profile_id)
            launched = False
            if status.get("status") != "running":
                try:
                    launch_resp = await mgr.launch(profile_id)
                    launched = True
                except ManagerError as exc:
                    if exc.status_code == 409 and "already running" in exc.body.lower():
                        launch_resp = {
                            "profile_id": profile_id,
                            "status": "running",
                            "cdp_url": existing.get("cdp_url", f"/api/profiles/{profile_id}/cdp"),
                            "already_running": True,
                        }
                    else:
                        raise
            else:
                launch_resp = {
                    "profile_id": profile_id,
                    "status": "running",
                    "cdp_url": existing.get("cdp_url", f"/api/profiles/{profile_id}/cdp"),
                }
        except ManagerError as exc:
            return {"error": str(exc), "status_code": exc.status_code}

        cdp_abs = await mgr.bind_browser_cdp_env(
            launch_resp.get("cdp_url", f"/api/profiles/{profile_id}/cdp")
        )
        cdp_profile_id = profile_state.profile_id_from_cdp(cdp_abs)
        if cdp_profile_id and cdp_profile_id != profile_id:
            return {
                "error": "Resolved CDP URL points at a different profile.",
                "requested_profile_id": profile_id,
                "cdp_profile_id": cdp_profile_id,
                "cdp_url": cdp_abs,
                "task_id": profile_state.task_key(task_id),
            }
        old_cdp = str((binding_before or {}).get("cdp_url") or "")
        if old_cdp and old_cdp != cdp_abs:
            await get_pool().drop(old_cdp)
        profile_state.remember_profile(
            task_id,
            profile_id=profile_id,
            profile_name=str(existing.get("name") or profile),
            cdp_url=cdp_abs,
            cdp_http_url=os.environ.get("CLOAK_CDP_HTTP_URL", ""),
            proxy=proxy if proxy else (binding_before or {}).get("proxy"),
            source="cloak_set_active",
        )
        return {
            "profile_id": profile_id,
            "profile_name": str(existing.get("name") or profile),
            "cdp_url": cdp_abs,
            "already_running": bool(launch_resp.get("already_running")),
            "active": True,
            "launched": launched,
            "task_id": profile_state.task_key(task_id),
        }


async def cloak_stop(args: dict, **kw: Any) -> Dict[str, Any]:
    """Stop a profile + drop its cached Playwright client from the pool."""
    task_id = kw.get("task_id")
    profile = args.get("profile", "")
    binding = profile_state.get_binding(task_id)
    async with ManagerClient() as mgr:
        try:
            profile_id = await _resolve_profile_id(mgr, profile)
            await mgr.stop(profile_id)
            cdp_abs = mgr.absolute_cdp_url(f"/api/profiles/{profile_id}/cdp")
        except ManagerError as exc:
            return {"error": str(exc), "status_code": exc.status_code}

    await get_pool().drop(cdp_abs)
    if binding and binding.get("profile_id") == profile_id and binding.get("cdp_url"):
        await get_pool().drop(str(binding["cdp_url"]))
        profile_state.clear_binding(task_id, profile_id=profile_id)
    else:
        profile_state.clear_binding(profile_id=profile_id)

    # If we were active on this profile, clear the env.
    profile_state.clear_env_if_profile(profile_id, cdp_abs)

    return {"profile_id": profile_id, "stopped": True}


async def cloak_list_profiles(args: dict | None = None, **kw: Any) -> Dict[str, Any]:
    async with ManagerClient() as mgr:
        try:
            profiles = await mgr.list_profiles()
        except ManagerError as exc:
            return {"error": str(exc), "status_code": exc.status_code}
    # Trim to a compact form the LLM can scan.
    return {
        "profiles": [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "status": p.get("status"),
                "humanize": p.get("humanize"),
                "tags": p.get("tags", []),
            }
            for p in profiles
        ]
    }


async def cloak_solve_captcha(args: dict, **kw: Any) -> str:
    """Try to solve a captcha through the configured providers (CapSolver
    and/or 2captcha) in the order best for ``kind``. On every failure path
    (no API key, balance, unsolvable, timeout, unsupported kind) return
    the MANUAL_INTERVENTION_REQUIRED sentinel so the agent triggers
    kanban_block.

    ``extra`` carries kind-specific fields (e.g. ``{"action": "login",
    "min_score": 0.9}`` for recaptcha v3, ``{"gt": "...", "challenge":
    "..."}`` for geetest, ``{"captcha_url": "..."}`` for datadome, etc.).
    See the plugin README for the per-kind extra schema.
    """
    kind = args.get("kind", "")
    url = args.get("url", "")
    site_key = args.get("site_key", "")
    extra = args.get("extra")
    provider = args.get("provider", "auto")
    router = CaptchaRouter(override_provider=provider)
    try:
        token = await router.solve(kind, site_key=site_key, url=url, extra=extra or {})
        return token
    except ManualInterventionRequired as exc:
        logger.warning("cloak_solve_captcha kind=%s: %s — manual gate", kind, exc.reason)
        return MANUAL_INTERVENTION_REQUIRED
    except Exception as exc:  # noqa: BLE001
        logger.exception("cloak_solve_captcha unexpected error: %s", exc)
        return MANUAL_INTERVENTION_REQUIRED


async def cloak_detect_captcha(args: dict | None = None, **kw: Any) -> Dict[str, Any]:
    """Inspect the active CloakBrowser tab and classify any captcha.

    Returns ``{"kind": <str|null>, "site_key": ..., "page_url": ...,
    "extra": {...}, "confidence": "high|medium|low"}`` or an error dict
    if no active profile is bound.

    ``kind == null`` means no captcha detected — proceed normally.
    """
    cdp_url = profile_state.cdp_url_for_task(kw.get("task_id"))
    if not cdp_url:
        return {
            "error": "No Cloak CDP binding for this task. Call cloak_set_active(profile=...) first.",
        }
    preset = os.environ.get("CLOAK_HUMAN_PRESET", "default")
    try:
        client = await get_pool().get(cdp_url, preset=preset)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"BrowserPool.get failed: {exc}", "cdp_url": cdp_url}
    try:
        return await detect_in_playwright_page(client.page)
    except Exception as exc:  # noqa: BLE001
        logger.exception("cloak_detect_captcha evaluate failed")
        return {"error": f"evaluate failed: {exc}"}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


async def _resolve_profile_id(mgr: ManagerClient, profile: str) -> str:
    """Accept either a UUID or a profile name; return the UUID."""
    # Fast path: looks like a UUID, just use it.
    if profile_state.is_uuid(profile):
        return profile
    existing = await mgr.find_profile_by_name(profile)
    if existing is None:
        raise ManagerError(404, profile, "profile not found by name")
    return existing["id"]


def _profile_switch_guard(
    binding: Optional[Dict[str, Any]],
    *,
    requested_profile_id: str,
    allow_profile_switch: bool,
) -> Optional[Dict[str, Any]]:
    expected = str((binding or {}).get("profile_id") or "")
    if not expected or expected == requested_profile_id or allow_profile_switch:
        return None
    return {
        "error": (
            "Task is already bound to a different Cloak profile. "
            "Use the remembered profile_id, create a new task, or pass "
            "allow_profile_switch=true intentionally."
        ),
        "expected_profile_id": expected,
        "requested_profile_id": requested_profile_id,
        "task_id": str((binding or {}).get("task_id") or ""),
        "active_profile_name": str((binding or {}).get("profile_name") or ""),
    }
