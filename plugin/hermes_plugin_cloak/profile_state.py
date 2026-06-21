"""Task-scoped Cloak profile bindings.

The manager and native browser stack still use process env vars as the final
CDP handoff, but the agent can run multiple logical jobs in one process. This
module keeps the intended profile per Hermes task/session so a new registration
does not accidentally reuse the previous profile.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_lock = threading.RLock()
_loaded = False
_bindings: Dict[str, Dict[str, Any]] = {}


def task_key(task_id: Any = None) -> str:
    value = str(task_id or "").strip()
    return value or "default"


def is_uuid(value: str) -> bool:
    return bool(_UUID_RE.fullmatch(str(value or "").strip()))


def profile_id_from_cdp(url: str) -> str:
    match = _UUID_RE.search(str(url or ""))
    return match.group(0) if match else ""


def get_binding(task_id: Any = None) -> Optional[Dict[str, Any]]:
    with _lock:
        _load_locked()
        binding = _bindings.get(task_key(task_id))
        return dict(binding) if binding else None


def remember_profile(
    task_id: Any = None,
    *,
    profile_id: str,
    profile_name: str = "",
    cdp_url: Optional[str] = None,
    cdp_http_url: Optional[str] = None,
    proxy: Optional[str] = None,
    source: str = "",
) -> Dict[str, Any]:
    key = task_key(task_id)
    now = _now()
    with _lock:
        _load_locked()
        current = dict(_bindings.get(key) or {})
        current.update(
            {
                "task_id": key,
                "profile_id": profile_id or current.get("profile_id", ""),
                "updated_at": now,
            }
        )
        current.setdefault("created_at", now)
        if profile_name:
            current["profile_name"] = profile_name
        elif "profile_name" not in current:
            current["profile_name"] = ""
        if cdp_url is not None:
            current["cdp_url"] = cdp_url
        if cdp_http_url is not None:
            current["cdp_http_url"] = cdp_http_url
        if proxy is not None:
            current["proxy"] = proxy
        if source:
            current["source"] = source
        _bindings[key] = current
        _save_locked()
        return dict(current)


def activate_task_binding(task_id: Any = None) -> str:
    binding = get_binding(task_id)
    if not binding:
        return ""
    cdp_url = str(binding.get("cdp_url") or "").strip()
    if not cdp_url:
        return ""
    _apply_env(binding)
    return cdp_url


def cdp_url_for_task(task_id: Any = None) -> str:
    cdp_url = activate_task_binding(task_id)
    if cdp_url:
        return cdp_url
    if task_id:
        return ""
    return os.environ.get("BROWSER_CDP_URL", "").strip()


def current_profile_id(task_id: Any = None) -> str:
    binding = get_binding(task_id)
    if binding and binding.get("profile_id"):
        return str(binding["profile_id"])
    return profile_id_from_cdp(os.environ.get("BROWSER_CDP_URL", ""))


def clear_binding(task_id: Any = None, *, profile_id: str = "") -> None:
    key = task_key(task_id)
    with _lock:
        _load_locked()
        if task_id is not None:
            binding = _bindings.get(key)
            if not binding or not profile_id or binding.get("profile_id") == profile_id:
                _bindings.pop(key, None)
        elif profile_id:
            for item_key, binding in list(_bindings.items()):
                if binding.get("profile_id") == profile_id:
                    _bindings.pop(item_key, None)
        _save_locked()


def clear_env_if_profile(profile_id: str, cdp_url: str = "") -> None:
    active_id = os.environ.get("CLOAK_ACTIVE_PROFILE_ID", "")
    active_cdp = os.environ.get("BROWSER_CDP_URL", "")
    if (profile_id and active_id == profile_id) or (cdp_url and active_cdp == cdp_url):
        for name in (
            "BROWSER_CDP_URL",
            "CLOAK_CDP_HTTP_URL",
            "CLOAK_ACTIVE_PROFILE_ID",
            "CLOAK_ACTIVE_PROFILE_NAME",
            "CLOAK_ACTIVE_TASK_ID",
        ):
            os.environ.pop(name, None)


def _apply_env(binding: Dict[str, Any]) -> None:
    os.environ["BROWSER_CDP_URL"] = str(binding.get("cdp_url") or "")
    if binding.get("cdp_http_url"):
        os.environ["CLOAK_CDP_HTTP_URL"] = str(binding["cdp_http_url"])
    os.environ["CLOAK_ACTIVE_PROFILE_ID"] = str(binding.get("profile_id") or "")
    os.environ["CLOAK_ACTIVE_PROFILE_NAME"] = str(binding.get("profile_name") or "")
    os.environ["CLOAK_ACTIVE_TASK_ID"] = str(binding.get("task_id") or "")


def _state_path() -> Path:
    home = os.environ.get("HERMES_HOME", "").strip()
    base = Path(home) if home else Path.home() / ".hermes"
    return base / "cloak" / "session-bindings.json"


def _load_locked() -> None:
    global _loaded, _bindings
    if _loaded:
        return
    _loaded = True
    path = _state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            _bindings = {
                str(k): dict(v)
                for k, v in raw.items()
                if isinstance(v, dict)
            }
    except FileNotFoundError:
        _bindings = {}
    except Exception:
        _bindings = {}


def _save_locked() -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_bindings, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        # Binding persistence is best-effort; in-memory state still protects
        # the current process from profile mixups.
        return


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
