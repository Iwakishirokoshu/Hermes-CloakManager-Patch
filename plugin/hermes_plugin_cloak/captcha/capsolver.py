"""CapSolver client (https://docs.capsolver.com).

Parallel implementation to ``twocaptcha.py`` with the same surface so the
router can swap one for the other transparently.

Battle-tested kinds (cross-referenced with captcha-solver SKILL.md):

    Kind                     Task type                              Extras
    ----                     ---------                              ------
    recaptcha_v2             ReCaptchaV2TaskProxyLess               isInvisible
    recaptcha_v3             ReCaptchaV3TaskProxyLess               pageAction, minScore
    recaptcha_enterprise     ReCaptchaV2EnterpriseTaskProxyLess     —
    hcaptcha                 HCaptchaTaskProxyLess                  —
    turnstile                AntiTurnstileTaskProxyLess             metadata.action
    funcaptcha               FunCaptchaTaskProxyLess                websiteSubdomain
    amazon_waf               AntiAwsWafTaskProxyLess                awsKey, awsIv, awsContext
    friendly_captcha         FriendlyCaptchaTaskProxyLess           —
    keycaptcha               KeyCaptchaTaskProxyLess                userId, sessionId,
                                                                    webServerSign, webServerSign2
    datadome                 DataDomeSliderTaskProxyLess            captchaUrl, userAgent
    kasada                   KasadaTaskProxyLess                    onlyCD?
    akamai                   AntiAkamaiBmpTaskProxyLess             —
    imperva                  AntiIncapsulaTaskProxyLess             —
    lemin                    LeminTaskProxyLess                     captchaId
    image                    ImageToTextTask                        body (base64)

NOT supported by CapSolver (caller should fall back to 2captcha):
    geetest, geetest_v4, mtcaptcha, cybersiara, cutcaptcha, capy,
    yandex, vk_captcha, tencent, atb_captcha
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import httpx

CAPSOLVER_BASE = "https://api.capsolver.com"


class CapSolverError(RuntimeError):
    pass


# Mapping kind -> CapSolver task type. Kinds NOT in this dict are unsupported.
_TASK_TYPES = {
    "recaptcha_v2": "ReCaptchaV2TaskProxyLess",
    "recaptcha_v3": "ReCaptchaV3TaskProxyLess",
    "recaptcha_enterprise": "ReCaptchaV2EnterpriseTaskProxyLess",
    "hcaptcha": "HCaptchaTaskProxyLess",
    "turnstile": "AntiTurnstileTaskProxyLess",
    "funcaptcha": "FunCaptchaTaskProxyLess",
    "amazon_waf": "AntiAwsWafTaskProxyLess",
    "friendly_captcha": "FriendlyCaptchaTaskProxyLess",
    "friendly": "FriendlyCaptchaTaskProxyLess",
    "keycaptcha": "KeyCaptchaTaskProxyLess",
    "datadome": "DataDomeSliderTaskProxyLess",
    "kasada": "KasadaTaskProxyLess",
    "akamai": "AntiAkamaiBmpTaskProxyLess",
    "imperva": "AntiIncapsulaTaskProxyLess",
    "lemin": "LeminTaskProxyLess",
    "image": "ImageToTextTask",
}

SUPPORTED_KINDS = sorted(_TASK_TYPES.keys())


def _build_task(kind: str, site_key: str, url: str, extra: dict) -> dict:
    """Build the `task` payload for createTask. Raises if unsupported."""
    task_type = _TASK_TYPES.get(kind)
    if not task_type:
        raise CapSolverError(
            f"capsolver does not support kind {kind!r}. "
            f"Supported: {', '.join(SUPPORTED_KINDS)}"
        )

    task: dict[str, Any] = {"type": task_type}

    if kind == "image":
        if not extra.get("body"):
            raise CapSolverError("image kind requires extra['body'] (base64)")
        task["body"] = extra["body"]
        return task

    # Most other tasks need websiteURL and websiteKey.
    if url:
        task["websiteURL"] = url
    if site_key:
        task["websiteKey"] = site_key

    if kind == "recaptcha_v2" and extra.get("invisible"):
        task["isInvisible"] = True
    if kind == "recaptcha_v3":
        task["pageAction"] = extra.get("action", "verify")
        task["minScore"] = extra.get("min_score", 0.9)
    if kind == "turnstile":
        if extra.get("action") or extra.get("data"):
            task["metadata"] = {}
            if extra.get("action"):
                task["metadata"]["action"] = extra["action"]
            if extra.get("data"):
                task["metadata"]["cdata"] = extra["data"]
    if kind == "funcaptcha":
        if extra.get("surl"):
            task["websiteSubdomain"] = extra["surl"]
        if site_key:
            task["websitePublicKey"] = site_key
            task.pop("websiteKey", None)
    if kind == "amazon_waf":
        task["awsKey"] = site_key
        task["awsIv"] = extra.get("iv", "")
        task["awsContext"] = extra.get("context", "")
        if extra.get("challenge_script"):
            task["awsChallengeJS"] = extra["challenge_script"]
    if kind == "keycaptcha":
        task["userId"] = extra.get("user_id", "")
        task["sessionId"] = extra.get("session_id", "")
        task["webServerSign"] = extra.get("ws_sign", "")
        task["webServerSign2"] = extra.get("ws_sign2", "")
    if kind == "datadome":
        if not extra.get("captcha_url"):
            raise CapSolverError("datadome requires extra['captcha_url']")
        task["captchaUrl"] = extra["captcha_url"]
        if extra.get("userAgent"):
            task["userAgent"] = extra["userAgent"]
    if kind == "lemin":
        if not extra.get("captcha_id"):
            raise CapSolverError("lemin requires extra['captcha_id']")
        task["captchaId"] = extra["captcha_id"]
        if extra.get("div_id"):
            task["divId"] = extra["div_id"]
    if kind == "kasada" and extra.get("only_cd"):
        task["onlyCD"] = True

    return task


def _extract_token(kind: str, solution: dict) -> str:
    """Pull the right token field out of CapSolver's per-kind solution dict."""
    if not isinstance(solution, dict):
        return str(solution)
    # Try common token field names in priority order.
    for key in (
        "gRecaptchaResponse", "token", "captchaToken", "answer",
        "x-aws-waf-token", "cookie", "userAgent", "text",
    ):
        if key in solution and solution[key]:
            return str(solution[key])
    # Fall back to the whole dict so the caller can inspect.
    return str(solution)


class CapSolverClient:
    """Async CapSolver client. Reuses a single httpx.AsyncClient per instance."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = 180.0,
        poll_interval: float = 3.0,
        first_poll_delay: float = 5.0,
    ):
        self.api_key = api_key or os.environ.get("CAPSOLVER_API_KEY")
        if not self.api_key:
            raise CapSolverError("CAPSOLVER_API_KEY not set")
        self._client = httpx.AsyncClient(timeout=30.0)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.first_poll_delay = first_poll_delay

    async def __aenter__(self) -> "CapSolverClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def name(self) -> str:
        return "capsolver"

    async def solve(
        self,
        kind: str,
        site_key: str = "",
        url: str = "",
        *,
        extra: Optional[dict] = None,
    ) -> str:
        extra = dict(extra or {})
        task = _build_task(kind, site_key, url, extra)
        task_id = await self._create_task(task)
        solution = await self._poll(task_id)
        return _extract_token(kind, solution)

    # ------- internals ------- #

    async def _create_task(self, task: dict) -> str:
        resp = await self._client.post(
            f"{CAPSOLVER_BASE}/createTask",
            json={"clientKey": self.api_key, "task": task},
        )
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise CapSolverError(f"non-JSON: {resp.text[:200]}") from exc
        if data.get("errorId"):
            raise CapSolverError(
                f"capsolver createTask error: {data.get('errorCode')} {data.get('errorDescription')}"
            )
        task_id = data.get("taskId")
        if not task_id:
            raise CapSolverError(f"capsolver createTask returned no taskId: {data}")
        return str(task_id)

    async def _poll(self, task_id: str) -> dict:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        await asyncio.sleep(self.first_poll_delay)
        while True:
            if loop.time() > deadline:
                raise CapSolverError(f"capsolver timeout for task {task_id}")
            resp = await self._client.post(
                f"{CAPSOLVER_BASE}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
            )
            try:
                data = resp.json()
            except Exception:
                await asyncio.sleep(self.poll_interval)
                continue
            if data.get("errorId"):
                raise CapSolverError(
                    f"capsolver getTaskResult error: {data.get('errorCode')} "
                    f"{data.get('errorDescription')}"
                )
            status = data.get("status")
            if status == "ready":
                return data.get("solution") or {}
            if status == "processing":
                await asyncio.sleep(self.poll_interval)
                continue
            raise CapSolverError(f"capsolver unexpected status: {data}")
