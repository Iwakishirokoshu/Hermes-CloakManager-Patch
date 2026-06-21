"""Captcha provider router.

Picks the best backend per ``kind`` based on:

1. ``CAPTCHA_PROVIDER`` env var (``capsolver`` | ``2captcha`` | ``auto``).
2. Per-kind preference table — derived from real-world test matrix in
   captcha-solver SKILL.md, e.g.:
     * hCaptcha — CapSolver is dramatically better.
     * Geetest / mtcaptcha / yandex / tencent — only 2captcha supports.
     * DataDome / Kasada / Akamai / Imperva — CapSolver only.
3. Available API keys.

On every solver failure, ``MANUAL_INTERVENTION_REQUIRED`` is raised so the
agent layer can trigger ``kanban_block``.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from .manual_required import MANUAL_INTERVENTION_REQUIRED
from .twocaptcha import (
    TwoCaptchaClient,
    TwoCaptchaError,
    SUPPORTED_KINDS as _TWO_KINDS,
)
from .capsolver import (
    CapSolverClient,
    CapSolverError,
    SUPPORTED_KINDS as _CAP_KINDS,
)

log = logging.getLogger("cloak.captcha.router")


# Per-kind preferred provider order. First provider in the list is tried
# first; on failure (or missing key) the next is tried. Kinds absent from
# both lists raise MANUAL_INTERVENTION_REQUIRED immediately.
_PREFERRED: dict[str, list[str]] = {
    "recaptcha_v2": ["capsolver", "2captcha"],
    "recaptcha_v3": ["capsolver", "2captcha"],
    "recaptcha_enterprise": ["capsolver", "2captcha"],
    "hcaptcha": ["capsolver", "2captcha"],  # CapSolver wins big here
    "turnstile": ["capsolver", "2captcha"],
    "funcaptcha": ["capsolver", "2captcha"],
    "amazon_waf": ["capsolver", "2captcha"],
    "friendly_captcha": ["capsolver", "2captcha"],
    "friendly": ["capsolver", "2captcha"],
    "keycaptcha": ["capsolver", "2captcha"],
    "datadome": ["capsolver", "2captcha"],
    "lemin": ["capsolver", "2captcha"],
    "image": ["capsolver", "2captcha"],
    # CapSolver-only:
    "kasada": ["capsolver"],
    "akamai": ["capsolver"],
    "imperva": ["capsolver"],
    # 2captcha-only:
    "geetest": ["2captcha"],
    "geetest_v4": ["2captcha"],
    "mtcaptcha": ["2captcha"],
    "cybersiara": ["2captcha"],
    "cutcaptcha": ["2captcha"],
    "capy": ["2captcha"],
    "yandex": ["2captcha"],
    "tencent": ["2captcha"],
}


class ManualInterventionRequired(RuntimeError):
    """Raised when every solver path failed and we need a human."""

    def __init__(self, reason: str = ""):
        super().__init__(MANUAL_INTERVENTION_REQUIRED)
        self.reason = reason


class CaptchaRouter:
    """Stateless router; instantiate per solve call."""

    def __init__(self, override_provider: Optional[str] = None):
        # Force a specific provider via env or constructor arg.
        env_override = os.environ.get("CAPTCHA_PROVIDER", "auto").lower().strip()
        self.override = (override_provider or env_override).lower().strip()
        if self.override not in {"auto", "capsolver", "2captcha", "twocaptcha"}:
            log.warning("Unknown CAPTCHA_PROVIDER=%r, falling back to auto", self.override)
            self.override = "auto"
        if self.override == "twocaptcha":
            self.override = "2captcha"

    async def solve(
        self,
        kind: str,
        site_key: str = "",
        url: str = "",
        *,
        extra: Optional[dict] = None,
    ) -> str:
        """Try providers in preferred order. Returns token or raises
        ``ManualInterventionRequired``."""
        kind = (kind or "").lower().strip()
        if not kind:
            raise ManualInterventionRequired("kind is empty")

        # Resolve the candidate order.
        if self.override == "auto":
            candidates = _PREFERRED.get(kind, [])
        elif self.override == "capsolver":
            candidates = ["capsolver"] if kind in _CAP_KINDS else []
        else:  # 2captcha
            candidates = ["2captcha"] if kind in _TWO_KINDS else []

        if not candidates:
            log.warning("Unsupported captcha kind=%r", kind)
            raise ManualInterventionRequired(f"unsupported kind: {kind}")

        last_err: Optional[Exception] = None
        for provider in candidates:
            client_factory = _CLIENTS[provider]
            try:
                async with client_factory() as client:
                    log.info("captcha solve start kind=%s provider=%s url=%s", kind, provider, url)
                    token = await client.solve(kind, site_key, url, extra=extra)
                    if not token:
                        raise RuntimeError("empty token")
                    log.info("captcha solve OK kind=%s provider=%s", kind, provider)
                    return token
            except (TwoCaptchaError, CapSolverError) as err:
                # Soft fail — try next provider.
                log.warning("captcha provider %s failed: %s", provider, err)
                last_err = err
            except RuntimeError as err:
                msg = str(err).lower()
                if any(x in msg for x in ("not set", "missing")):
                    # Missing API key for this provider — silent fallback.
                    log.info("captcha provider %s skipped (no API key)", provider)
                    last_err = err
                    continue
                log.warning("captcha provider %s runtime error: %s", provider, err)
                last_err = err
            except Exception as err:  # noqa: BLE001
                log.exception("captcha provider %s unexpected error", provider)
                last_err = err

        raise ManualInterventionRequired(
            f"all providers exhausted for kind={kind}: {last_err}"
        )


def _make_two() -> TwoCaptchaClient:
    return TwoCaptchaClient()


def _make_cap() -> CapSolverClient:
    return CapSolverClient()


_CLIENTS = {
    "2captcha": _make_two,
    "capsolver": _make_cap,
}
