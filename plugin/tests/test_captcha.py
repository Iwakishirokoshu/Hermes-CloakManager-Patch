"""Unit tests for the captcha subsystem.

Focus: parameter builders + router behaviour + sentinel fallback.
We mock the network layer so these run offline.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hermes_plugin_cloak.captcha import (
    MANUAL_INTERVENTION_REQUIRED,
    CaptchaRouter,
    ManualInterventionRequired,
)
from hermes_plugin_cloak.captcha import twocaptcha as tc
from hermes_plugin_cloak.captcha import capsolver as cs
from hermes_plugin_cloak.captcha import detector as det
from hermes_plugin_cloak.captcha import router as router_mod


# ---------------------------------------------------------------------------
# Detector JS shape
# ---------------------------------------------------------------------------


def test_detector_js_is_self_contained():
    """Make sure the JS snippet is a single expression with no template gaps."""
    js = det.detector_js()
    assert js.strip().startswith("(() => {")
    assert js.strip().endswith("})();")
    # All 22 captcha kinds we advertise should be string-mentioned.
    for kind in [
        "turnstile", "hcaptcha", "recaptcha_v2", "recaptcha_v3",
        "recaptcha_enterprise", "funcaptcha", "geetest", "amazon_waf",
        "friendly_captcha", "keycaptcha", "datadome", "kasada", "akamai",
        "imperva", "yandex", "tencent", "lemin", "mtcaptcha", "image",
        "cloudflare_interstitial",
    ]:
        assert f'"{kind}"' in js, f"detector JS missing kind {kind!r}"


# ---------------------------------------------------------------------------
# 2captcha parameter builders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(tc.SUPPORTED_KINDS))
def test_two_builder_for_each_kind_returns_method(kind: str):
    """Every advertised kind must yield params with a 'method' key (so the
    request is well-formed before being POSTed to /in.php)."""
    builder = tc._BUILDERS[kind]
    extra = _example_extra_for(kind)
    try:
        params = builder("test_site_key", "https://example.com/login", extra)
    except tc.TwoCaptchaError:
        # Builders that require extras raise with no extras — that's a valid
        # behaviour, just give them the minimum.
        params = builder("test_site_key", "https://example.com/login", extra)
    assert "method" in params, f"{kind} builder must set 'method'"


def test_two_builder_v3_carries_action_and_score():
    p = tc._b_recaptcha_v3("KEY", "https://x", {"action": "login", "min_score": 0.7})
    assert p["version"] == "v3"
    assert p["action"] == "login"
    assert p["min_score"] == 0.7


def test_two_builder_turnstile_optional_action_and_cdata():
    p = tc._b_turnstile("KEY", "https://x", {})
    assert "action" not in p
    p2 = tc._b_turnstile("KEY", "https://x", {"action": "x", "data": "cdata"})
    assert p2["action"] == "x"
    assert p2["data"] == "cdata"


def test_two_client_reads_api_key_file(monkeypatch: Any, tmp_path: Any):
    monkeypatch.delenv("TWO_CAPTCHA_API_KEY", raising=False)
    monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
    monkeypatch.delenv("CAPTCHA_API_KEY", raising=False)
    key_file = tmp_path / "api_key"
    key_file.write_text("file-key\n", encoding="utf-8")
    monkeypatch.setenv("TWO_CAPTCHA_API_KEY_FILE", str(key_file))
    client = tc.TwoCaptchaClient()
    try:
        assert client.api_key == "file-key"
    finally:
        asyncio.run(client.aclose())


def test_two_builder_geetest_requires_gt_challenge():
    with pytest.raises(tc.TwoCaptchaError):
        tc._b_geetest("", "https://x", {})
    p = tc._b_geetest("", "https://x", {"gt": "G", "challenge": "C"})
    assert p["gt"] == "G" and p["challenge"] == "C"


def test_two_builder_datadome_requires_captcha_url():
    with pytest.raises(tc.TwoCaptchaError):
        tc._b_datadome("", "https://x", {})
    p = tc._b_datadome("", "https://x", {"captcha_url": "https://c.datadome.co/captcha"})
    assert p["method"] == "datadome"


def test_two_builder_image_requires_body():
    with pytest.raises(tc.TwoCaptchaError):
        tc._b_image("", "", {})
    p = tc._b_image("", "", {"body": "BASE64DATA"})
    assert p["method"] == "base64" and p["body"] == "BASE64DATA"


# ---------------------------------------------------------------------------
# CapSolver parameter builders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(cs.SUPPORTED_KINDS))
def test_cap_builder_for_each_kind_returns_type(kind: str):
    extra = _example_extra_for(kind)
    task = cs._build_task(kind, "test_site_key", "https://example.com", extra)
    assert "type" in task, f"{kind} task must have 'type'"
    assert task["type"].endswith(("Task", "TaskProxyLess"))


def test_cap_builder_rejects_unsupported_kind():
    with pytest.raises(cs.CapSolverError):
        cs._build_task("geetest", "K", "https://x", {})


def test_cap_token_extractor_picks_recaptcha_field():
    tok = cs._extract_token("recaptcha_v2", {"gRecaptchaResponse": "TOKEN"})
    assert tok == "TOKEN"


def test_cap_token_extractor_picks_turnstile_token():
    tok = cs._extract_token("turnstile", {"token": "TOKEN"})
    assert tok == "TOKEN"


def test_cap_token_extractor_picks_datadome_cookie():
    tok = cs._extract_token("datadome", {"cookie": "datadome=xxxx"})
    assert tok == "datadome=xxxx"


# ---------------------------------------------------------------------------
# Router — preference table coverage
# ---------------------------------------------------------------------------


def test_router_preference_table_covers_every_kind():
    """Every kind supported by either provider must appear in the preference
    table — otherwise auto-mode would refuse it silently."""
    universe = set(tc.SUPPORTED_KINDS) | set(cs.SUPPORTED_KINDS)
    missing = universe - set(router_mod._PREFERRED.keys())
    assert not missing, f"router missing preference for kinds: {missing}"


def test_router_preference_only_lists_real_providers():
    for kind, providers in router_mod._PREFERRED.items():
        for p in providers:
            assert p in {"capsolver", "2captcha"}, f"{kind}: bad provider {p}"


@pytest.mark.parametrize("kind", ["hcaptcha", "turnstile", "datadome"])
def test_router_prefers_capsolver_for_hard_anti_bot(kind: str):
    assert router_mod._PREFERRED[kind][0] == "capsolver"


@pytest.mark.parametrize("kind", ["geetest", "mtcaptcha", "yandex", "tencent"])
def test_router_uses_only_two_captcha_for_2captcha_specifics(kind: str):
    assert router_mod._PREFERRED[kind] == ["2captcha"]


# ---------------------------------------------------------------------------
# Router — fallback to MANUAL_INTERVENTION_REQUIRED
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.mark.asyncio
async def test_router_falls_through_to_manual_when_no_keys(monkeypatch: Any):
    """Without API keys, every provider raises 'not set' which the router
    must convert to ManualInterventionRequired."""
    monkeypatch.delenv("CAPSOLVER_API_KEY", raising=False)
    monkeypatch.delenv("TWO_CAPTCHA_API_KEY", raising=False)
    monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
    monkeypatch.delenv("CAPTCHA_API_KEY", raising=False)
    monkeypatch.delenv("CAPTCHA_PROVIDER", raising=False)
    r = CaptchaRouter()
    with pytest.raises(ManualInterventionRequired) as exc:
        await r.solve("hcaptcha", site_key="K", url="https://x", extra={})
    # The sentinel string must match exactly so the LLM can detect it.
    assert str(exc.value) == MANUAL_INTERVENTION_REQUIRED


@pytest.mark.asyncio
async def test_router_unsupported_kind_blocks_to_human(monkeypatch: Any):
    monkeypatch.setenv("CAPSOLVER_API_KEY", "fake-cap-key")
    monkeypatch.setenv("TWO_CAPTCHA_API_KEY", "fake-two-key")
    r = CaptchaRouter()
    with pytest.raises(ManualInterventionRequired):
        await r.solve("nonexistent_kind", url="https://x")


@pytest.mark.asyncio
async def test_router_override_to_capsolver_only_skips_two(monkeypatch: Any):
    """When CAPTCHA_PROVIDER=capsolver and only 2captcha has a key, all
    2captcha-only kinds (e.g. geetest) must block to human."""
    monkeypatch.delenv("CAPSOLVER_API_KEY", raising=False)
    monkeypatch.setenv("TWO_CAPTCHA_API_KEY", "fake-two-key")
    r = CaptchaRouter(override_provider="capsolver")
    with pytest.raises(ManualInterventionRequired):
        await r.solve("geetest", url="https://x",
                      extra={"gt": "G", "challenge": "C"})


@pytest.mark.asyncio
async def test_router_normalises_twocaptcha_alias(monkeypatch: Any):
    monkeypatch.delenv("CAPSOLVER_API_KEY", raising=False)
    monkeypatch.delenv("TWO_CAPTCHA_API_KEY", raising=False)
    monkeypatch.delenv("TWOCAPTCHA_API_KEY", raising=False)
    monkeypatch.delenv("CAPTCHA_API_KEY", raising=False)
    r = CaptchaRouter(override_provider="twocaptcha")
    # No keys -> must block, but should at least normalise to '2captcha'
    assert r.override == "2captcha"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _example_extra_for(kind: str) -> dict:
    """Minimum extras to make a builder happy. Tests use these to exercise
    the FULL parameter shape, including required extras."""
    samples = {
        "geetest": {"gt": "G", "challenge": "C"},
        "geetest_v4": {"captcha_id": "ID"},
        "datadome": {"captcha_url": "https://c.datadome.co/captcha"},
        "lemin": {"captcha_id": "CID", "div_id": "DID"},
        "cybersiara": {"master_url": "https://master"},
        "cutcaptcha": {"misery_key": "MK", "api_key": "AK"},
        "tencent": {"app_id": "APP"},
        "image": {"body": "BASE64DATA"},
        "amazon_waf": {"iv": "IV", "context": "CTX"},
        "recaptcha_v3": {"action": "verify", "min_score": 0.9},
    }
    return samples.get(kind, {})
