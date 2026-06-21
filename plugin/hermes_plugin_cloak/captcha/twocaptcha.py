"""Full-coverage 2captcha client (https://2captcha.com/2captcha-api).

Battle-tested kinds, derived from the captcha-solver SKILL.md test matrix:

    Kind                     Method param           Notes / extra
    ----                     ------------           -------------
    recaptcha_v2             userrecaptcha          site_key, url, invisible
    recaptcha_v3             userrecaptcha + v3     site_key, url, action, min_score
    recaptcha_enterprise     userrecaptcha+enterp.  site_key, url
    hcaptcha                 hcaptcha               site_key, url   (HARDEST, often fails)
    turnstile                turnstile              site_key, url, action?
    funcaptcha               funcaptcha             public_key, url, surl?
    geetest                  geetest                gt, challenge, url, api_server?
    geetest_v4               geetest_v4             captcha_id, url
    amazon_waf               amazon_waf             pageurl, sitekey, iv, context
    friendly_captcha         friendly_captcha       site_key, url
    keycaptcha               keycaptcha             pageurl, user_id, session_id,
                                                    ws_sign, ws_sign2
    datadome                 datadome               pageurl, captcha_url, userAgent?
    lemin                    lemin                  captcha_id, div_id, url
    mtcaptcha                mt_captcha             site_key, url
    cybersiara               cybersiara             master_url, url, userAgent?
    cutcaptcha               cutcaptcha             misery_key, api_key, url
    capy                     capy                   captchakey, url
    yandex                   yandex                 site_key, url
    tencent                  tencent                app_id, url
    image                    base64                 base64 body, instructions?

On failure (no key, balance, timeout, unsolvable) raises ``TwoCaptchaError``.
Caller-side ``cloak_solve_captcha`` converts that into the
``MANUAL_INTERVENTION_REQUIRED`` sentinel so the agent triggers the human
gate via ``kanban_block``.

This is a pure HTTP client — no Playwright, no browser. The site_key /
gt / challenge / etc. must already be extracted by the caller (the
detector.py module helps with that).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

import httpx

TWO_CAPTCHA_BASE = "https://2captcha.com"


class TwoCaptchaError(RuntimeError):
    pass


# ----------------------------------------------------------------------------
# Per-kind parameter builders
# ----------------------------------------------------------------------------
#
# Each builder accepts a kwargs dict (`extra`) and returns the `params`
# dict to POST to /in.php. site_key, url etc. are passed explicitly to
# keep the call sites self-documenting.
#
# Adding a new kind = new builder here + add to _BUILDERS map + bump the
# kind list in the docstring above. No other code changes needed.


def _b_recaptcha_v2(site_key: str, url: str, extra: dict) -> dict:
    p = {"method": "userrecaptcha", "googlekey": site_key, "pageurl": url}
    if extra.get("invisible"):
        p["invisible"] = 1
    return p


def _b_recaptcha_v3(site_key: str, url: str, extra: dict) -> dict:
    p = {
        "method": "userrecaptcha",
        "googlekey": site_key,
        "pageurl": url,
        "version": "v3",
        "action": extra.get("action", "verify"),
        "min_score": extra.get("min_score", 0.9),
    }
    return p


def _b_recaptcha_enterprise(site_key: str, url: str, extra: dict) -> dict:
    p = {"method": "userrecaptcha", "googlekey": site_key, "pageurl": url, "enterprise": 1}
    if extra.get("action"):
        p["action"] = extra["action"]
    return p


def _b_hcaptcha(site_key: str, url: str, extra: dict) -> dict:
    return {"method": "hcaptcha", "sitekey": site_key, "pageurl": url}


def _b_turnstile(site_key: str, url: str, extra: dict) -> dict:
    p = {"method": "turnstile", "sitekey": site_key, "pageurl": url}
    if extra.get("action"):
        p["action"] = extra["action"]
    if extra.get("data"):
        p["data"] = extra["data"]
    return p


def _b_funcaptcha(site_key: str, url: str, extra: dict) -> dict:
    p = {"method": "funcaptcha", "publickey": site_key, "pageurl": url}
    if extra.get("surl"):
        p["surl"] = extra["surl"]
    return p


def _b_geetest(_site_key: str, url: str, extra: dict) -> dict:
    if not (extra.get("gt") and extra.get("challenge")):
        raise TwoCaptchaError("geetest requires extra['gt'] and extra['challenge']")
    p = {
        "method": "geetest",
        "gt": extra["gt"],
        "challenge": extra["challenge"],
        "pageurl": url,
    }
    if extra.get("api_server"):
        p["api_server"] = extra["api_server"]
    return p


def _b_geetest_v4(_site_key: str, url: str, extra: dict) -> dict:
    if not extra.get("captcha_id"):
        raise TwoCaptchaError("geetest_v4 requires extra['captcha_id']")
    return {"method": "geetest_v4", "captcha_id": extra["captcha_id"], "pageurl": url}


def _b_amazon_waf(site_key: str, url: str, extra: dict) -> dict:
    p = {
        "method": "amazon_waf",
        "pageurl": url,
        "iv": extra.get("iv", ""),
        "context": extra.get("context", ""),
    }
    if site_key:
        p["sitekey"] = site_key
    if extra.get("challenge_script"):
        p["challenge_script"] = extra["challenge_script"]
    if extra.get("captcha_script"):
        p["captcha_script"] = extra["captcha_script"]
    return p


def _b_friendly(site_key: str, url: str, extra: dict) -> dict:
    return {"method": "friendly_captcha", "sitekey": site_key, "pageurl": url}


def _b_keycaptcha(_site_key: str, url: str, extra: dict) -> dict:
    return {
        "method": "keycaptcha",
        "pageurl": url,
        "s_s_c_user_id": extra.get("user_id", ""),
        "s_s_c_session_id": extra.get("session_id", ""),
        "s_s_c_web_server_sign": extra.get("ws_sign", ""),
        "s_s_c_web_server_sign2": extra.get("ws_sign2", ""),
    }


def _b_datadome(_site_key: str, url: str, extra: dict) -> dict:
    if not extra.get("captcha_url"):
        raise TwoCaptchaError("datadome requires extra['captcha_url']")
    p = {
        "method": "datadome",
        "pageurl": url,
        "captcha_url": extra["captcha_url"],
    }
    if extra.get("userAgent"):
        p["userAgent"] = extra["userAgent"]
    if extra.get("proxy"):
        p["proxy"] = extra["proxy"]
        p["proxytype"] = extra.get("proxytype", "HTTPS")
    return p


def _b_lemin(_site_key: str, url: str, extra: dict) -> dict:
    if not extra.get("captcha_id"):
        raise TwoCaptchaError("lemin requires extra['captcha_id']")
    return {
        "method": "lemin",
        "captcha_id": extra["captcha_id"],
        "div_id": extra.get("div_id", ""),
        "pageurl": url,
    }


def _b_mtcaptcha(site_key: str, url: str, extra: dict) -> dict:
    return {"method": "mt_captcha", "sitekey": site_key, "pageurl": url}


def _b_cybersiara(_site_key: str, url: str, extra: dict) -> dict:
    if not extra.get("master_url"):
        raise TwoCaptchaError("cybersiara requires extra['master_url']")
    p = {"method": "cybersiara", "master_url_id": extra["master_url"], "pageurl": url}
    if extra.get("userAgent"):
        p["userAgent"] = extra["userAgent"]
    return p


def _b_cutcaptcha(_site_key: str, url: str, extra: dict) -> dict:
    if not (extra.get("misery_key") and extra.get("api_key")):
        raise TwoCaptchaError("cutcaptcha requires extra['misery_key'] and extra['api_key']")
    return {
        "method": "cutcaptcha",
        "misery_key": extra["misery_key"],
        "api_key": extra["api_key"],
        "pageurl": url,
    }


def _b_capy(site_key: str, url: str, extra: dict) -> dict:
    return {"method": "capy", "captchakey": site_key, "pageurl": url}


def _b_yandex(site_key: str, url: str, extra: dict) -> dict:
    return {"method": "yandex", "sitekey": site_key, "pageurl": url}


def _b_tencent(_site_key: str, url: str, extra: dict) -> dict:
    if not extra.get("app_id"):
        raise TwoCaptchaError("tencent requires extra['app_id']")
    return {"method": "tencent", "app_id": extra["app_id"], "pageurl": url}


def _b_image(_site_key: str, _url: str, extra: dict) -> dict:
    if not extra.get("body"):
        raise TwoCaptchaError("image requires extra['body'] (base64-encoded)")
    p = {"method": "base64", "body": extra["body"]}
    if extra.get("textinstructions"):
        p["textinstructions"] = extra["textinstructions"]
    if extra.get("regsense"):
        p["regsense"] = 1
    if extra.get("numeric"):
        p["numeric"] = extra["numeric"]
    return p


_BUILDERS = {
    "recaptcha_v2": _b_recaptcha_v2,
    "recaptcha_v3": _b_recaptcha_v3,
    "recaptcha_enterprise": _b_recaptcha_enterprise,
    "hcaptcha": _b_hcaptcha,
    "turnstile": _b_turnstile,
    "funcaptcha": _b_funcaptcha,
    "geetest": _b_geetest,
    "geetest_v4": _b_geetest_v4,
    "amazon_waf": _b_amazon_waf,
    "friendly_captcha": _b_friendly,
    "friendly": _b_friendly,
    "keycaptcha": _b_keycaptcha,
    "datadome": _b_datadome,
    "lemin": _b_lemin,
    "mtcaptcha": _b_mtcaptcha,
    "cybersiara": _b_cybersiara,
    "cutcaptcha": _b_cutcaptcha,
    "capy": _b_capy,
    "yandex": _b_yandex,
    "tencent": _b_tencent,
    "image": _b_image,
}

SUPPORTED_KINDS = sorted(_BUILDERS.keys())


# ----------------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------------


class TwoCaptchaClient:
    """Async 2captcha client. Reuses a single httpx.AsyncClient per instance."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = 180.0,
        poll_interval: float = 5.0,
        first_poll_delay: float = 15.0,
    ):
        self.api_key = (
            api_key
            or os.environ.get("TWO_CAPTCHA_API_KEY")
            or os.environ.get("TWOCAPTCHA_API_KEY")
            or os.environ.get("CAPTCHA_API_KEY")
            or _read_api_key_file()
        )
        if not self.api_key:
            raise TwoCaptchaError("TWO_CAPTCHA_API_KEY (or TWOCAPTCHA_API_KEY) not set")
        self._client = httpx.AsyncClient(timeout=30.0)
        self.timeout = timeout
        self.poll_interval = poll_interval
        # 2captcha recommends ~15s before first poll for most kinds.
        self.first_poll_delay = first_poll_delay

    async def __aenter__(self) -> "TwoCaptchaClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def name(self) -> str:
        return "2captcha"

    async def solve(
        self,
        kind: str,
        site_key: str = "",
        url: str = "",
        *,
        extra: Optional[dict] = None,
    ) -> str:
        """Solve a captcha and return the token / answer.

        ``kind`` must be one of ``SUPPORTED_KINDS``.
        ``site_key`` may be empty for kinds that pass everything through ``extra``
        (geetest, datadome, image, etc.).
        """
        extra = dict(extra or {})
        builder = _BUILDERS.get(kind)
        if not builder:
            raise TwoCaptchaError(
                f"Unsupported kind: {kind!r}. Known: {', '.join(SUPPORTED_KINDS)}"
            )
        params = builder(site_key, url, extra)
        params["key"] = self.api_key
        params["json"] = 1

        req_id = await self._submit(params)
        return await self._poll(req_id)

    # ------- internals ------- #

    async def _submit(self, params: dict[str, Any]) -> str:
        resp = await self._client.post(f"{TWO_CAPTCHA_BASE}/in.php", data=params)
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise TwoCaptchaError(f"non-JSON response from 2captcha: {resp.text[:200]}") from exc
        if data.get("status") != 1:
            raise TwoCaptchaError(f"2captcha submit failed: {data.get('request')}")
        return str(data["request"])

    async def _poll(self, req_id: str) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        await asyncio.sleep(self.first_poll_delay)
        while True:
            if loop.time() > deadline:
                raise TwoCaptchaError(f"2captcha timeout for request {req_id}")
            resp = await self._client.get(
                f"{TWO_CAPTCHA_BASE}/res.php",
                params={"key": self.api_key, "action": "get", "id": req_id, "json": 1},
            )
            try:
                data = resp.json()
            except Exception:
                await asyncio.sleep(self.poll_interval)
                continue
            if data.get("status") == 1:
                return str(data["request"])
            err = data.get("request")
            if err == "CAPCHA_NOT_READY":
                await asyncio.sleep(self.poll_interval)
                continue
            raise TwoCaptchaError(f"2captcha solve failed: {err}")


def _read_api_key_file() -> Optional[str]:
    """Read the conventional local 2captcha key file if env vars are unset."""
    candidates: list[Path] = []
    if os.environ.get("TWO_CAPTCHA_API_KEY_FILE"):
        candidates.append(Path(os.environ["TWO_CAPTCHA_API_KEY_FILE"]))
    candidates = [
        *candidates,
        Path.home() / ".config" / "2captcha" / "api_key",
    ]
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None
