"""In-page captcha detector.

Runs a single ``page.evaluate(...)`` call in the active CloakBrowser tab
that classifies the page and (when possible) extracts ``site_key`` + any
extra fields the solver needs.

The JS is ported / consolidated from captcha-solver's ``detect_captcha``
but rewritten as one self-contained expression so we don't pay 18
``page.content()`` roundtrips.

Returns a dict::

    {
      "kind":      "turnstile" | "recaptcha_v2" | ... | None,
      "site_key":  "0x4..." | None,
      "page_url":  "https://...",
      "extra":     {"action": "...", "gt": "...", "captcha_url": "..."},
      "confidence": "high" | "medium" | "low",
    }

``kind == None`` means no captcha detected.
"""
from __future__ import annotations

from typing import Any, Optional

# Pure JS — single expression returning a plain object. Designed to be
# fed straight into Playwright's ``page.evaluate(_DETECT_JS)``.
_DETECT_JS = r"""
(() => {
  const out = { kind: null, site_key: null, page_url: location.href, extra: {}, confidence: "high" };
  const $ = (sel) => document.querySelector(sel);

  // --- Cloudflare interstitial (full block, not just embedded Turnstile) ---
  const html = document.documentElement.outerHTML.toLowerCase();
  if (html.includes("checking your browser") && html.includes("cloudflare")) {
    out.kind = "cloudflare_interstitial"; return out;
  }
  if ($("#cf-challenge-running, [class*=cf-challenge-running]")) {
    out.kind = "cloudflare_interstitial"; return out;
  }

  // --- Cloudflare Turnstile ---
  const tDiv = $(".cf-turnstile, [data-sitekey][class*=turnstile], turnstile-wrapper");
  const tIframe = $('iframe[src*="challenges.cloudflare.com"]');
  if (tDiv || tIframe) {
    out.kind = "turnstile";
    out.site_key = (tDiv && tDiv.getAttribute("data-sitekey")) || null;
    const action = tDiv && tDiv.getAttribute("data-action");
    if (action) out.extra.action = action;
    const cdata = tDiv && tDiv.getAttribute("data-cdata");
    if (cdata) out.extra.data = cdata;
    return out;
  }

  // --- hCaptcha ---
  const hDiv = $(".h-captcha, [data-hcaptcha-sitekey]");
  const hIframe = $('iframe[src*="hcaptcha"]');
  if (hDiv || hIframe) {
    out.kind = "hcaptcha";
    out.site_key = (hDiv && (hDiv.getAttribute("data-sitekey") || hDiv.getAttribute("data-hcaptcha-sitekey"))) || null;
    return out;
  }

  // --- reCAPTCHA family ---
  const rDiv = $(".g-recaptcha");
  const rIframe = $('iframe[src*="recaptcha"]');
  const rScript = $('script[src*="recaptcha"]');
  if (rDiv || rIframe || rScript) {
    // Enterprise vs v3 vs v2 (priority order matters).
    const isEnterprise =
      !!$('script[src*="recaptcha/enterprise"]') ||
      (typeof window.___grecaptcha_cfg !== "undefined" &&
        Object.values(window.___grecaptcha_cfg.clients || {}).some(c => c && c.enterprise));
    const isV3 =
      !!$(".grecaptcha-badge") &&
      !$('iframe[src*="recaptcha"][src*="bframe"]');
    out.kind = isEnterprise ? "recaptcha_enterprise" : (isV3 ? "recaptcha_v3" : "recaptcha_v2");
    out.site_key = (rDiv && rDiv.getAttribute("data-sitekey")) || null;
    // Some sites stash the key in iframe ?k=...
    if (!out.site_key && rIframe) {
      try {
        const u = new URL(rIframe.src, location.href);
        out.site_key = u.searchParams.get("k");
      } catch (e) {}
    }
    return out;
  }

  // --- GeeTest ---
  if ($(".geetest_holder, .geetest_panel") || $('script[src*="geetest"]')) {
    out.kind = "geetest";
    out.confidence = "medium";
    // gt + challenge usually injected via initGeetest() — we can't read them
    // synchronously, caller must inspect network or call window.initGeetest.
    return out;
  }

  // --- FunCaptcha / Arkose ---
  const fDiv = $('#arkose, [data-arkose], .arkose-iframe-container, .funcaptcha, [data-pkey]');
  const fScript = $('script[src*="arkose"], script[src*="funcaptcha"], script[src*="arkoselabs"]');
  const fIframe = $('iframe[src*="arkose"], iframe[src*="funcaptcha"]');
  if (fDiv || fScript || fIframe) {
    out.kind = "funcaptcha";
    out.site_key = (fDiv && (fDiv.getAttribute("data-pkey") || fDiv.getAttribute("data-public-key"))) || null;
    if (fIframe) {
      try {
        const u = new URL(fIframe.src, location.href);
        const surl = u.origin + u.pathname.split("/").slice(0, -1).join("/");
        out.extra.surl = surl;
      } catch (e) {}
    }
    return out;
  }

  // --- Amazon WAF ---
  if ($('#captchacharacters, .captchacharacters') ||
      $('script[src*="captcha.js"], script[src*="aws-waf"]') ||
      html.includes("aws-waf")) {
    out.kind = "amazon_waf";
    out.confidence = "medium";
    return out;
  }

  // --- Friendly Captcha ---
  const frDiv = $('.frc-captcha, frc-captcha');
  if (frDiv || $('script[src*="friendly-challenge"], script[src*="friendlycaptcha"]')) {
    out.kind = "friendly_captcha";
    out.site_key = (frDiv && frDiv.getAttribute("data-sitekey")) || null;
    return out;
  }

  // --- KeyCaptcha ---
  if ($('script[src*="keycaptcha"]') || $('#kc_div, .keycaptcha')) {
    out.kind = "keycaptcha";
    out.confidence = "low";
    return out;
  }

  // --- DataDome ---
  if ($('script[src*="datadome"], script[src*="dd-js"]') ||
      $('iframe[src*="datadome"], iframe[src*="captcha.datadome"]') ||
      document.cookie.includes("datadome")) {
    out.kind = "datadome";
    const dIframe = $('iframe[src*="datadome"], iframe[src*="captcha.datadome"]');
    if (dIframe) out.extra.captcha_url = dIframe.src;
    out.extra.userAgent = navigator.userAgent;
    return out;
  }

  // --- Kasada ---
  if ($('script[src*="kasada"], script[src*="cdn.cas"], script[id*="kasada"]')) {
    out.kind = "kasada"; out.confidence = "medium"; return out;
  }

  // --- Akamai ---
  if ($('script[src*="akamai"], script[src*="akamai.bmp"], script[src*=".akamaized.net"]')) {
    out.kind = "akamai"; out.confidence = "low"; return out;
  }

  // --- Imperva / Incapsula ---
  if ($('script[src*="incapsula"], script[src*="imperva"]') ||
      document.cookie.includes("incap_ses") || document.cookie.includes("visid_incap")) {
    out.kind = "imperva"; out.confidence = "low"; return out;
  }

  // --- Yandex SmartCaptcha ---
  const yDiv = $('.smart-captcha, #js-captcha-si, [data-sitekey][class*=smart-captcha]');
  if (yDiv || $('script[src*="smartcaptcha"]')) {
    out.kind = "yandex";
    out.site_key = (yDiv && yDiv.getAttribute("data-sitekey")) || null;
    return out;
  }

  // --- Tencent ---
  const tcDiv = $('#tcaptcha_transform_dy, .tcaptcha, [data-appid]');
  if (tcDiv || $('script[src*="tencent.com/captcha"]')) {
    out.kind = "tencent";
    if (tcDiv) out.extra.app_id = tcDiv.getAttribute("data-appid") || "";
    return out;
  }

  // --- Lemin ---
  const lDiv = $('[data-captcha-id], .lemin-captcha, #lemin-captcha');
  if (lDiv || $('script[src*="leminnow"]')) {
    out.kind = "lemin";
    if (lDiv) {
      out.extra.captcha_id = lDiv.getAttribute("data-captcha-id") || "";
      out.extra.div_id = lDiv.id || "";
    }
    return out;
  }

  // --- MTCaptcha ---
  const mDiv = $('.mtcaptcha, [data-sitekey][class*=mtcaptcha]');
  if (mDiv || $('script[src*="mtcaptcha"]')) {
    out.kind = "mtcaptcha";
    out.site_key = (mDiv && mDiv.getAttribute("data-sitekey")) || null;
    return out;
  }

  // --- Generic image captcha (last resort) ---
  const img = $('img[src*="captcha"], img[alt*="captcha" i]');
  if (img) {
    out.kind = "image";
    out.extra.image_src = img.src;
    out.confidence = "low";
    return out;
  }

  return out; // kind: null
})();
"""


async def detect_in_playwright_page(page: Any) -> dict:
    """Run the detector in a Playwright page; returns the dict described above.

    ``page`` may be either an async Playwright Page or anything with a
    compatible ``evaluate`` coroutine.
    """
    result = await page.evaluate(_DETECT_JS)
    if not isinstance(result, dict):
        return {"kind": None, "site_key": None, "page_url": "", "extra": {}, "confidence": "high"}
    # Normalise.
    result.setdefault("kind", None)
    result.setdefault("site_key", None)
    result.setdefault("page_url", "")
    result.setdefault("extra", {})
    result.setdefault("confidence", "high")
    return result


def detector_js() -> str:
    """Expose the JS snippet for callers that have raw CDP only."""
    return _DETECT_JS
