"""HTTP client for CloakBrowser-Manager (the FastAPI service on :41833 by default).

API surface mapped from CloakBrowser-Manager-main/backend/main.py:

    POST   /api/auth/login            -> { ok }                 (sets auth cookie)
    GET    /api/profiles              -> [ProfileResponse]
    POST   /api/profiles              -> ProfileResponse
    GET    /api/profiles/{id}         -> ProfileResponse
    PUT    /api/profiles/{id}         -> ProfileResponse
    DELETE /api/profiles/{id}         -> { ok }
    POST   /api/profiles/{id}/launch  -> LaunchResponse
    POST   /api/profiles/{id}/stop    -> { ok }
    GET    /api/profiles/{id}/status  -> ProfileStatusResponse
    GET    /api/status                -> { running_count, ... }

Auth: optional shared Bearer token via header ``Authorization: Bearer <token>``,
sourced from $CLOAK_AUTH_TOKEN. Cloak's middleware exempts /api/status,
/api/auth/status, /api/auth/login.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


DEFAULT_BASE_URL = "http://127.0.0.1:41833"
DEFAULT_TIMEOUT = 30.0


class ManagerError(RuntimeError):
    """Wraps any non-2xx manager response with body + status code."""

    def __init__(self, status_code: int, body: str, message: str):
        super().__init__(f"{message} (HTTP {status_code}): {body}")
        self.status_code = status_code
        self.body = body


class ManagerClient:
    """Lightweight async wrapper over the CloakBrowser-Manager REST API.

    Re-uses one httpx.AsyncClient per instance; you can also use it as a
    context manager (``async with ManagerClient() as mgr: ...``).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = (base_url or os.environ.get("CLOAK_MANAGER_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.auth_token = auth_token if auth_token is not None else os.environ.get("CLOAK_AUTH_TOKEN")
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        )

    async def __aenter__(self) -> "ManagerClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------- low-level ------- #

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise ManagerError(
                0, str(exc),
                f"CloakBrowser-Manager unreachable at {self.base_url}",
            ) from exc
        if resp.status_code >= 400:
            raise ManagerError(resp.status_code, resp.text, f"{method} {path} failed")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # ------- public API ------- #

    async def status(self) -> Dict[str, Any]:
        """GET /api/status (exempt from auth — safe smoke check)."""
        return await self._request("GET", "/api/status")

    async def list_profiles(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/profiles")

    async def get_profile(self, profile_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/api/profiles/{profile_id}")

    async def find_profile_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Look up a profile by its `name` field, return None if not found."""
        profiles = await self.list_profiles()
        for p in profiles:
            if p.get("name") == name:
                return p
        return None

    async def create_profile(self, **fields: Any) -> Dict[str, Any]:
        """POST /api/profiles. `fields` is forwarded as JSON body.

        Required: `name`. Common optional: proxy, humanize, human_preset,
        headless, geoip, fingerprint_seed, tags, notes. See
        CloakBrowser-Manager-main/backend/models.py for the full schema.
        """
        if "name" not in fields:
            raise ValueError("create_profile() requires `name`")
        return await self._request("POST", "/api/profiles", json=fields)

    async def update_profile(self, profile_id: str, **fields: Any) -> Dict[str, Any]:
        return await self._request("PUT", f"/api/profiles/{profile_id}", json=fields)

    async def delete_profile(self, profile_id: str) -> None:
        await self._request("DELETE", f"/api/profiles/{profile_id}")

    async def launch(self, profile_id: str) -> Dict[str, Any]:
        """POST /api/profiles/{id}/launch — returns LaunchResponse with cdp_url etc.

        Body is empty (manager pulls launch params from the profile record).
        """
        return await self._request("POST", f"/api/profiles/{profile_id}/launch")

    async def stop(self, profile_id: str) -> None:
        await self._request("POST", f"/api/profiles/{profile_id}/stop")

    async def profile_status(self, profile_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/api/profiles/{profile_id}/status")

    # ------- helpers ------- #

    def absolute_cdp_url(self, relative_cdp_path: str) -> str:
        """Manager's launch response returns cdp_url like `/api/profiles/{id}/cdp`.

        Playwright connect_over_cdp wants an absolute http(s) URL — this
        method builds it against base_url.
        """
        if relative_cdp_path.startswith(("http://", "https://", "ws://", "wss://")):
            return relative_cdp_path
        if not relative_cdp_path.startswith("/"):
            relative_cdp_path = "/" + relative_cdp_path
        return f"{self.base_url}{relative_cdp_path}"

    async def resolve_cdp_ws_url(self, cdp_http_url: str) -> str:
        """Fetch /json/version with auth and return webSocketDebuggerUrl.

        Hermes native browser_tool cannot attach Bearer tokens to CDP
        discovery — we resolve the WS URL here and store that in
        ``BROWSER_CDP_URL`` so agent-browser connects without 401.
        """
        base = cdp_http_url.rstrip("/")
        headers: Dict[str, str] = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        try:
            resp = await self._client.get(f"{base}/json/version", headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "CDP /json/version returned %s for %s",
                    resp.status_code,
                    base,
                )
                return cdp_http_url
            data = resp.json()
            ws = str(data.get("webSocketDebuggerUrl") or "").strip()
            return ws or cdp_http_url
        except Exception as exc:  # noqa: BLE001
            logger.warning("CDP WS resolve failed for %s: %s", base, exc)
            return cdp_http_url

    async def bind_browser_cdp_env(self, relative_or_absolute_cdp: str) -> str:
        """Set ``BROWSER_CDP_URL`` to an authenticated WS endpoint when possible.

        Native Hermes ``agent-browser`` cannot attach an ``Authorization: Bearer``
        header on the WebSocket upgrade request. When CloakBrowser-Manager has
        ``AUTH_TOKEN`` set, that handshake gets 401/403 and read-only tools
        (browser_snapshot, browser_screenshot, browser_console, browser_vision)
        fail even though our patched action tools work fine.

        If the operator sets ``CLOAK_CDP_PROXY_BASE`` (e.g. ``http://127.0.0.1:8081``)
        we point ``BROWSER_CDP_URL`` at that endpoint instead — typically a
        localhost-only reverse proxy (nginx/aiohttp) that injects the Bearer
        header server-side. Native agent-browser then connects without auth
        and the proxy upgrades the request before forwarding to the manager.
        """
        cdp_http = self.absolute_cdp_url(relative_or_absolute_cdp)
        cdp_ws = await self.resolve_cdp_ws_url(cdp_http)

        proxy_base = os.environ.get("CLOAK_CDP_PROXY_BASE", "").strip().rstrip("/")
        if proxy_base:
            from urllib.parse import urlparse
            proxy = urlparse(proxy_base)
            proxy_netloc = proxy.netloc or proxy.path  # tolerate "127.0.0.1:8081"
            if proxy_netloc:
                def _rewrite(url: str, ws: bool) -> str:
                    p = urlparse(url)
                    scheme = ("ws" if ws else "http") if proxy.scheme in ("", "http") \
                        else ("wss" if ws else "https")
                    return f"{scheme}://{proxy_netloc}{p.path or ''}" \
                           + (f"?{p.query}" if p.query else "")
                cdp_http = _rewrite(cdp_http, ws=False)
                cdp_ws = _rewrite(cdp_ws, ws=True)
                logger.info("CDP URLs rewritten via CLOAK_CDP_PROXY_BASE=%s", proxy_base)

        os.environ["BROWSER_CDP_URL"] = cdp_ws
        os.environ["CLOAK_CDP_HTTP_URL"] = cdp_http
        return cdp_ws
