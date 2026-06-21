"""Drop-in replacement for ``cloakbrowser.human.keyboard_async``.

Same public surface as the original:

    class AsyncRawKeyboard(Protocol):
        async def down(self, key: str) -> None
        async def up(self, key: str) -> None
        async def type(self, text: str) -> None
        async def insert_text(self, text: str) -> None

    async def async_human_type(page, raw, text, cfg, cdp_session=None)

What changes vs cloakbrowser:
  - Cloakbrowser fires ONLY adjacent-key typos. We add four more typo types
    from pydoll (transpose, double, skip, missed_space), pulled from a
    weighted random.choices on each typo trigger.
  - We use OUR ``constants.QWERTY_NEIGHBORS`` map (richer / smaller, all
    lowercase) instead of cloak's NEARBY_KEYS (mixed case, slightly
    different topology).
  - We add pydoll's "distraction" pause (0.5% chance, 500-1200ms) on top of
    cloak's "thinking" pause (typing_pause_chance/range, typically 10%).

Shift-symbol typing path is COPIED VERBATIM from cloakbrowser (it goes
through CDP Input.dispatchKeyEvent which produces isTrusted=true events,
critical for stealth). We do not improve on what already works.

Math/timing attribution: pydoll (autoscrape-labs, MIT).
"""
from __future__ import annotations

import asyncio
import random
from typing import Any, Optional, Protocol

from .constants import (
    DEFAULT_TYPO_PROBABILITY,
    QWERTY_NEIGHBORS,
    TYPO_WEIGHTS,
    TypoType,
)

# Cloakbrowser stores SHIFT symbol metadata in its sync sibling. We pull
# those from the ORIGINAL cloak module — but lazily on first use, so that
# this module remains importable in environments without cloakbrowser
# (test-only scenarios, fresh CI venv). The values are constants — once
# loaded, cached for the life of the process.

_SHIFT_SYMBOLS: Any = None
_SHIFT_CODES: Any = None
_SHIFT_KEYCODES: Any = None


def _load_shift_tables() -> tuple:
    global _SHIFT_SYMBOLS, _SHIFT_CODES, _SHIFT_KEYCODES
    if _SHIFT_SYMBOLS is None:
        # Imported lazily; cloakbrowser is in our project deps but tests
        # may run against it without installing.
        from cloakbrowser.human.keyboard import (  # type: ignore  # noqa: E402
            SHIFT_SYMBOLS,
            _SHIFT_SYMBOL_CODES,
            _SHIFT_SYMBOL_KEYCODES,
        )
        _SHIFT_SYMBOLS = SHIFT_SYMBOLS
        _SHIFT_CODES = _SHIFT_SYMBOL_CODES
        _SHIFT_KEYCODES = _SHIFT_SYMBOL_KEYCODES
    return _SHIFT_SYMBOLS, _SHIFT_CODES, _SHIFT_KEYCODES


def _is_shift_symbol(ch: str) -> bool:
    """Cheap membership check that loads cloak's table on first call."""
    symbols, _, _ = _load_shift_tables()
    return ch in symbols


# Tuned defaults — used when cfg lacks a particular knob.
DISTRACTION_PROBABILITY: float = 0.005
DISTRACTION_RANGE_MS = (500, 1200)


class AsyncRawKeyboard(Protocol):
    async def down(self, key: str) -> None: ...
    async def up(self, key: str) -> None: ...
    async def type(self, text: str) -> None: ...
    async def insert_text(self, text: str) -> None: ...


# ----------------------------------------------------------------------------
# Public surface
# ----------------------------------------------------------------------------


async def async_human_type(
    page: Any,
    raw: AsyncRawKeyboard,
    text: str,
    cfg: Any,
    cdp_session: Any = None,
) -> None:
    """Type ``text`` one character at a time with realistic timing and 5 typo varieties."""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        next_ch = text[i + 1] if i + 1 < n else None

        # Non-ASCII (Cyrillic, CJK, emoji) — bypass typing and use insert_text.
        if not ch.isascii():
            await _sleep_range_ms(getattr(cfg, "key_hold", (15, 35)))
            await raw.insert_text(ch)
            if i < n - 1:
                await _inter_char_delay(cfg)
            i += 1
            continue

        # Decide whether to fire a typo on this char.
        typo_prob = float(getattr(cfg, "mistype_chance", DEFAULT_TYPO_PROBABILITY))
        consumed_next = False
        if random.random() < typo_prob and (ch.isalnum() or ch == " "):
            consumed_next = await _dispatch_typo(page, raw, ch, next_ch, cfg, cdp_session)

        if not consumed_next:
            # Actually type the character.
            if ch.isupper() and ch.isalpha():
                await _type_shifted(raw, ch, cfg)
            elif _is_shift_symbol(ch):
                await _type_shift_symbol(page, raw, ch, cfg, cdp_session)
            else:
                await _type_normal(raw, ch, cfg)

        # Step forward (typo handlers may have consumed an extra char).
        i += 2 if consumed_next else 1

        if i < n:
            await _inter_char_delay(cfg)


# ----------------------------------------------------------------------------
# Typing primitives
# ----------------------------------------------------------------------------


async def _type_normal(raw: AsyncRawKeyboard, ch: str, cfg: Any) -> None:
    await raw.down(ch)
    await _sleep_range_ms(getattr(cfg, "key_hold", (15, 35)))
    await raw.up(ch)


async def _type_shifted(raw: AsyncRawKeyboard, ch: str, cfg: Any) -> None:
    await raw.down("Shift")
    await _sleep_range_ms(getattr(cfg, "shift_down_delay", (30, 70)))
    await raw.down(ch)
    await _sleep_range_ms(getattr(cfg, "key_hold", (15, 35)))
    await raw.up(ch)
    await _sleep_range_ms(getattr(cfg, "shift_up_delay", (20, 50)))
    await raw.up("Shift")


async def _type_shift_symbol(
    page: Any,
    raw: AsyncRawKeyboard,
    ch: str,
    cfg: Any,
    cdp_session: Any = None,
) -> None:
    """Type a shift-symbol character (e.g. '@', '!', '#', '{').

    Uses CDP Input.dispatchKeyEvent for isTrusted=true events when a CDP
    session is provided (stealth path). Falls back to insert_text +
    page.evaluate dispatch (detectable) when no CDP session.

    Behaviour copied from cloakbrowser unchanged; this path is correct.
    """
    if cdp_session is not None:
        _, codes, keycodes = _load_shift_tables()
        code = codes.get(ch, "")
        key_code = keycodes.get(ch, 0)

        await raw.down("Shift")
        await _sleep_range_ms(getattr(cfg, "shift_down_delay", (30, 70)))

        await cdp_session.send("Input.dispatchKeyEvent", {
            "type": "keyDown",
            "modifiers": 8,  # Shift flag
            "key": ch,
            "code": code,
            "windowsVirtualKeyCode": key_code,
            "text": ch,
            "unmodifiedText": ch,
        })
        await _sleep_range_ms(getattr(cfg, "key_hold", (15, 35)))

        await cdp_session.send("Input.dispatchKeyEvent", {
            "type": "keyUp",
            "modifiers": 8,
            "key": ch,
            "code": code,
            "windowsVirtualKeyCode": key_code,
        })

        await _sleep_range_ms(getattr(cfg, "shift_up_delay", (20, 50)))
        await raw.up("Shift")
    else:
        # Detectable fallback — same as cloakbrowser's.
        await raw.down("Shift")
        await _sleep_range_ms(getattr(cfg, "shift_down_delay", (30, 70)))
        await raw.insert_text(ch)
        await page.evaluate(
            """(key) => {
                const el = document.activeElement;
                if (el) {
                    el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
                    el.dispatchEvent(new KeyboardEvent('keyup', { key, bubbles: true }));
                }
            }""",
            ch,
        )
        await _sleep_range_ms(getattr(cfg, "shift_up_delay", (20, 50)))
        await raw.up("Shift")


# ----------------------------------------------------------------------------
# Typo dispatcher
# ----------------------------------------------------------------------------


async def _dispatch_typo(
    page: Any,
    raw: AsyncRawKeyboard,
    ch: str,
    next_ch: Optional[str],
    cfg: Any,
    cdp_session: Any,
) -> bool:
    """Pick one of the 5 typo varieties, execute it, return True iff the
    typo consumed the next character (i.e. caller must skip it).

    Falls back to typing the intended character normally if the picked
    typo can't apply (no QWERTY neighbour, no next char for transpose, etc.).
    """
    typo_type = _pick_typo_type()

    if typo_type == TypoType.ADJACENT:
        wrong = _nearby_key(ch)
        if wrong is None or wrong == ch:
            return False
        await _do_adjacent(page, raw, ch, wrong, cfg, cdp_session)
        return False

    if typo_type == TypoType.TRANSPOSE and next_ch and next_ch.isalpha():
        await _do_transpose(page, raw, ch, next_ch, cfg, cdp_session)
        return True

    if typo_type == TypoType.DOUBLE:
        await _do_double(page, raw, ch, cfg, cdp_session)
        return False

    if typo_type == TypoType.SKIP:
        # Hesitate before typing — pydoll's "skip" type doesn't actually skip,
        # it adds a thinking pause then types normally.
        await _sleep_range_ms(getattr(cfg, "typing_pause_range", (150, 300)))
        await _type_intended(page, raw, ch, cfg, cdp_session)
        return False

    if typo_type == TypoType.MISSED_SPACE and ch == " " and next_ch:
        # Forgot the space, type next char, realise, backspace, re-type space + next.
        await _type_intended(page, raw, next_ch, cfg, cdp_session)
        await _sleep_range_ms(getattr(cfg, "mistype_delay_notice", (100, 300)))
        await raw.down("Backspace")
        await _sleep_range_ms(getattr(cfg, "key_hold", (15, 35)))
        await raw.up("Backspace")
        await _sleep_range_ms(getattr(cfg, "mistype_delay_correct", (50, 150)))
        await _type_normal(raw, " ", cfg)
        await _sleep_range_ms(getattr(cfg, "mistype_delay_correct", (50, 150)))
        await _type_intended(page, raw, next_ch, cfg, cdp_session)
        return True

    # Fallback — couldn't apply the picked typo, just type normally.
    return False


async def _do_adjacent(
    page: Any,
    raw: AsyncRawKeyboard,
    correct: str,
    wrong: str,
    cfg: Any,
    cdp_session: Any,
) -> None:
    await _type_intended(page, raw, wrong, cfg, cdp_session)
    await _sleep_range_ms(getattr(cfg, "mistype_delay_notice", (100, 300)))
    await raw.down("Backspace")
    await _sleep_range_ms(getattr(cfg, "key_hold", (15, 35)))
    await raw.up("Backspace")
    await _sleep_range_ms(getattr(cfg, "mistype_delay_correct", (50, 150)))
    await _type_intended(page, raw, correct, cfg, cdp_session)


async def _do_transpose(
    page: Any,
    raw: AsyncRawKeyboard,
    a: str,
    b: str,
    cfg: Any,
    cdp_session: Any,
) -> None:
    # Type b then a (transposed)
    await _type_intended(page, raw, b, cfg, cdp_session)
    await _sleep_range_ms(getattr(cfg, "key_hold", (15, 35)))
    await _type_intended(page, raw, a, cfg, cdp_session)
    await _sleep_range_ms(getattr(cfg, "mistype_delay_notice", (100, 300)))
    # Backspace twice, retype correctly
    for _ in range(2):
        await raw.down("Backspace")
        await _sleep_range_ms(getattr(cfg, "key_hold", (15, 35)))
        await raw.up("Backspace")
    await _sleep_range_ms(getattr(cfg, "mistype_delay_correct", (50, 150)))
    await _type_intended(page, raw, a, cfg, cdp_session)
    await _sleep_range_ms(getattr(cfg, "key_hold", (15, 35)))
    await _type_intended(page, raw, b, cfg, cdp_session)


async def _do_double(
    page: Any,
    raw: AsyncRawKeyboard,
    ch: str,
    cfg: Any,
    cdp_session: Any,
) -> None:
    await _type_intended(page, raw, ch, cfg, cdp_session)
    await _sleep_range_ms(getattr(cfg, "key_hold", (15, 35)))
    await _type_intended(page, raw, ch, cfg, cdp_session)
    await _sleep_range_ms(getattr(cfg, "mistype_delay_notice", (100, 300)))
    await raw.down("Backspace")
    await _sleep_range_ms(getattr(cfg, "key_hold", (15, 35)))
    await raw.up("Backspace")


async def _type_intended(
    page: Any,
    raw: AsyncRawKeyboard,
    ch: str,
    cfg: Any,
    cdp_session: Any,
) -> None:
    """Type ``ch`` correctly — caller-side router for case + shift symbols."""
    if ch.isupper() and ch.isalpha():
        await _type_shifted(raw, ch, cfg)
    elif _is_shift_symbol(ch):
        await _type_shift_symbol(page, raw, ch, cfg, cdp_session)
    else:
        await _type_normal(raw, ch, cfg)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _pick_typo_type() -> TypoType:
    keys = list(TYPO_WEIGHTS.keys())
    weights = [TYPO_WEIGHTS[k] for k in keys]
    return random.choices(keys, weights=weights, k=1)[0]


def _nearby_key(ch: str) -> Optional[str]:
    """Pick a QWERTY-adjacent key, preserving case."""
    lower = ch.lower()
    neighbours = QWERTY_NEIGHBORS.get(lower)
    if not neighbours:
        return None
    pick = random.choice(neighbours)
    if ch.isupper() and pick.isalpha():
        return pick.upper()
    return pick


async def _inter_char_delay(cfg: Any) -> None:
    """Apply a realistic delay after a character.

    Composition of:
      - "thinking" pause (cloakbrowser knob: typing_pause_chance / typing_pause_range)
      - "distraction" pause (pydoll add-on: 0.5% chance, 500-1200ms)
      - normal jitter around typing_delay
    """
    pause_chance = float(getattr(cfg, "typing_pause_chance", 0.1))
    if random.random() < pause_chance:
        await _sleep_range_ms(getattr(cfg, "typing_pause_range", (400, 1000)))
        return

    if random.random() < DISTRACTION_PROBABILITY:
        await _sleep_range_ms(DISTRACTION_RANGE_MS)
        return

    base = float(getattr(cfg, "typing_delay", 70))
    spread = float(getattr(cfg, "typing_delay_spread", 40))
    delay_ms = base + (random.random() - 0.5) * 2 * spread
    await asyncio.sleep(max(0.010, delay_ms / 1000.0))


async def _sleep_range_ms(rng: Any) -> None:
    """Sleep for a random number of MILLISECONDS in rng.

    Accepts (lo, hi) tuples/lists or a single number. Cloakbrowser passes
    everything in ms, so we just divide by 1000.
    """
    if isinstance(rng, (int, float)):
        await asyncio.sleep(max(0.0, float(rng) / 1000.0))
        return
    lo, hi = float(rng[0]), float(rng[1])
    await asyncio.sleep(max(0.0, random.uniform(lo, hi) / 1000.0))
