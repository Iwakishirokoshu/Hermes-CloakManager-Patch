"""Plugin hooks — auto-bind Cloak before native browser tools run."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import tools_manage
from . import profile_state

logger = logging.getLogger(__name__)

_BROWSER_TOOLS = frozenset(
    {
        "browser_navigate",
        "browser_snapshot",
        "browser_screenshot",
        "browser_back",
        "browser_console",
        "browser_get_images",
        "browser_vision",
    }
)


def _profile_name_for_session(task_id: Any = None) -> str:
    if task_id:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(task_id))[:32]
        return f"acc-{safe}"
    task = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    if task:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", task)[:32]
        return f"acc-{safe}"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"acc-auto-{stamp}"


def _ensure_cloak_active_sync(task_id: Any = None) -> None:
    if profile_state.activate_task_binding(task_id):
        return
    binding = profile_state.get_binding(task_id)
    if binding and binding.get("profile_id"):
        logger.info(
            "cloak hook: launching remembered profile %s for task %s",
            binding["profile_id"],
            profile_state.task_key(task_id),
        )

        async def _launch_bound() -> None:
            result = await tools_manage.set_active_profile(
                str(binding["profile_id"]),
                create_if_missing=False,
                humanize=True,
                human_preset=os.environ.get("CLOAK_HUMAN_PRESET", "default"),
                task_id=task_id,
                allow_profile_switch=True,
            )
            if result.get("error"):
                logger.warning("cloak hook: remembered profile launch failed: %s", result["error"])

        asyncio.run(_launch_bound())
        if profile_state.activate_task_binding(task_id):
            return
    if not task_id and os.environ.get("BROWSER_CDP_URL", "").strip():
        return
    if not os.environ.get("CLOAK_MANAGER_URL", "").strip():
        return
    name = _profile_name_for_session(task_id)
    logger.info("cloak hook: auto-launching profile %s before browser tool", name)

    async def _go() -> None:
        result = await tools_manage.set_active_profile(
            name,
            create_if_missing=True,
            humanize=True,
            human_preset=os.environ.get("CLOAK_HUMAN_PRESET", "default"),
            task_id=task_id,
        )
        if result.get("error"):
            logger.warning("cloak hook: auto-launch failed: %s", result["error"])

    asyncio.run(_go())


def on_pre_tool_call(
    tool_name: str = "",
    args: Any = None,
    **kw: Any,
) -> Optional[Dict[str, str]]:
    """Before read-only browser tools, ensure a Cloak profile is running."""
    task_id = kw.get("task_id")
    if tool_name not in _BROWSER_TOOLS:
        return None
    if profile_state.activate_task_binding(task_id):
        return None
    if not task_id and os.environ.get("BROWSER_CDP_URL", "").strip():
        return None
    if not os.environ.get("CLOAK_MANAGER_URL", "").strip():
        return None
    try:
        _ensure_cloak_active_sync(task_id)
    except Exception as exc:
        logger.exception("cloak hook: auto-launch error")
        return {
            "action": "block",
            "message": (
                f"Could not auto-launch Cloak profile: {exc}. "
                "Call cloak_set_active(profile=...) or cloak_launch first."
            ),
        }
    if not profile_state.cdp_url_for_task(task_id):
        return {
            "action": "block",
            "message": (
                "No Cloak CDP binding is set for this task. Call cloak_set_active(profile=...) "
                "or cloak_create_profile + cloak_launch before browser_* tools."
            ),
        }
    return None
