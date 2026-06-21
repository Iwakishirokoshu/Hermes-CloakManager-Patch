"""Drop-in replacement for ``cloakbrowser.human.mouse_async``.

This module is registered into ``sys.modules`` BEFORE cloakbrowser is
imported (see ``hermes_plugin_cloak.humanize.install()``), so that
cloakbrowser/human/__init__.py's

    from .mouse_async import AsyncRawMouse, async_human_move, async_human_click, async_human_idle

picks up our versions.

The public surface (names + signatures) MUST match cloakbrowser exactly:

    class AsyncRawMouse(Protocol):
        async def move(self, x: float, y: float) -> None
        async def down(self) -> None
        async def up(self) -> None
        async def wheel(self, dx: float, dy: float) -> None

    async def async_human_move(raw, sx, sy, ex, ey, cfg)
    async def async_human_click(raw, is_input, cfg)
    async def async_human_idle(raw, seconds, cx, cy, cfg)

Math is the pydoll port from `utils.py` in this package (Fitts's Law,
minimum-jerk, asymmetric Bezier, velocity-inverse Gaussian tremor,
distance-gated overshoot). Configuration tunables come from
cloakbrowser's HumanConfig — we read the cfg fields we can map cleanly
(click_hold_*, click_aim_delay_*, idle_drift_px, etc.), and fall back to
pydoll's defaults where cloakbrowser has no analogue (Fitts a/b coefficients,
minimum-jerk-specific knobs).

Header-only attribution: math derived from
https://github.com/autoscrape-labs/pydoll (MIT).
"""
from __future__ import annotations

import asyncio
import math
import random
from typing import Any, Protocol, Tuple

from .utils import bezier_2d, fitts_duration, minimum_jerk, random_control_points

# ----------------------------------------------------------------------------
# Pydoll-derived defaults that have no cloakbrowser analogue.
# ----------------------------------------------------------------------------

FITTS_A: float = 0.070            # base reaction time (sec)
FITTS_B: float = 0.150            # per-bit-of-difficulty cost (sec)

FRAME_INTERVAL: float = 0.012     # ~83 fps base
FRAME_INTERVAL_VARIANCE: float = 0.004

CURVATURE_MIN: float = 0.10
CURVATURE_MAX: float = 0.30
CURVATURE_ASYMMETRY: float = 0.6
SHORT_DISTANCE_THRESHOLD: float = 50.0

TREMOR_BASE: float = 1.0          # px; cloak's mouse_wobble_max overrides if present

# Distance threshold (CSS px) above which overshoot may fire.
# Cloakbrowser triggers overshoot on any move (constant 15%); pydoll only on
# fast, long moves — much more realistic.
OVERSHOOT_DISTANCE_GATE: float = 200.0

# Default click-hold + pre-click pause if cfg lacks them (cfg should provide).
DEFAULT_PRE_CLICK_PAUSE: Tuple[float, float] = (0.05, 0.20)
DEFAULT_CLICK_HOLD: Tuple[float, float] = (0.05, 0.15)

MIN_MOVE_DURATION: float = 0.08
MAX_MOVE_DURATION: float = 2.5

MICRO_PAUSE_PROBABILITY: float = 0.03
MICRO_PAUSE_RANGE: Tuple[float, float] = (0.015, 0.04)


# ----------------------------------------------------------------------------
# Public surface (matches cloakbrowser.human.mouse_async)
# ----------------------------------------------------------------------------


class AsyncRawMouse(Protocol):
    async def move(self, x: float, y: float) -> None: ...
    async def down(self) -> None: ...
    async def up(self) -> None: ...
    async def wheel(self, delta_x: float, delta_y: float) -> None: ...


async def async_human_move(
    raw: AsyncRawMouse,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    cfg: Any,
) -> None:
    """Move the mouse from (start_x, start_y) to (end_x, end_y) like a human.

    Uses Fitts's Law for total duration, minimum-jerk for the velocity
    profile, asymmetric cubic Bezier for the path, velocity-inverse Gaussian
    tremor along the way, micro-pauses, and distance-gated overshoot.
    """
    distance = math.hypot(end_x - start_x, end_y - start_y)
    if distance < 1.0:
        await raw.move(round(end_x), round(end_y))
        return

    # Total duration — Fitts's Law against a nominal 20px target.
    duration = fitts_duration(distance, 20.0, FITTS_A, FITTS_B)
    duration = max(MIN_MOVE_DURATION, min(duration, MAX_MOVE_DURATION))

    overshoot_chance = float(getattr(cfg, "mouse_overshoot_chance", 0.70))
    should_overshoot = (
        distance > OVERSHOOT_DISTANCE_GATE
        and random.random() < overshoot_chance
    )

    start = (start_x, start_y)
    end = (end_x, end_y)

    if should_overshoot:
        await _move_with_overshoot(raw, start, end, duration, cfg)
    else:
        cp1, cp2 = random_control_points(
            start, end,
            CURVATURE_MIN, CURVATURE_MAX, CURVATURE_ASYMMETRY,
            SHORT_DISTANCE_THRESHOLD,
        )
        await _move_loop(raw, start, end, duration, cp1, cp2, cfg)

    # Always land exactly on the target.
    await raw.move(round(end_x), round(end_y))


async def async_human_click(raw: AsyncRawMouse, is_input: bool, cfg: Any) -> None:
    """Press-and-release at the current cursor position with realistic
    pre-click aim pause and randomized hold time."""
    aim_range = _resolve_range(
        cfg,
        "click_aim_delay_input" if is_input else "click_aim_delay_button",
        DEFAULT_PRE_CLICK_PAUSE,
    )
    hold_range = _resolve_range(
        cfg,
        "click_hold_input" if is_input else "click_hold_button",
        DEFAULT_CLICK_HOLD,
    )

    await asyncio.sleep(_rand_range(aim_range))
    await raw.down()
    await asyncio.sleep(_rand_range(hold_range))
    await raw.up()


async def async_human_idle(
    raw: AsyncRawMouse,
    seconds: float,
    cx: float,
    cy: float,
    cfg: Any,
) -> None:
    """Drift the cursor randomly around (cx, cy) for `seconds`.

    Behaves identically to cloakbrowser's original — small per-step
    displacement bounded by cfg.idle_drift_px, with pauses from
    cfg.idle_pause_range. Kept simple; the per-step jitter is plenty
    natural-looking and matches cloakbrowser's idle micro-movements.
    """
    drift_px = float(getattr(cfg, "idle_drift_px", 3))
    pause_range = _resolve_range(cfg, "idle_pause_range", (0.300, 1.000))

    loop = asyncio.get_running_loop()
    end_time = loop.time() + seconds
    x, y = cx, cy
    while loop.time() < end_time:
        dx = (random.random() - 0.5) * 2 * drift_px
        dy = (random.random() - 0.5) * 2 * drift_px
        x += dx
        y += dy
        await raw.move(round(x), round(y))
        await asyncio.sleep(_rand_range(pause_range))


# ----------------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------------


async def _move_with_overshoot(
    raw: AsyncRawMouse,
    start: Tuple[float, float],
    target: Tuple[float, float],
    duration: float,
    cfg: Any,
) -> None:
    """Run an overshoot move: travel past the target, then correct back.

    Pydoll's split: 85% of duration on the outbound arc to the overshot
    point, 15% on the correction arc back to target. The overshoot
    distance is a small fraction of the original travel distance.
    """
    dx = target[0] - start[0]
    dy = target[1] - start[1]
    overshoot_fraction = random.uniform(0.03, 0.12)
    overshoot = (
        target[0] + dx * overshoot_fraction,
        target[1] + dy * overshoot_fraction,
    )

    cp1, cp2 = random_control_points(
        start, overshoot,
        CURVATURE_MIN, CURVATURE_MAX, CURVATURE_ASYMMETRY, SHORT_DISTANCE_THRESHOLD,
    )
    await _move_loop(raw, start, overshoot, duration * 0.85, cp1, cp2, cfg)

    cp1, cp2 = random_control_points(
        overshoot, target,
        CURVATURE_MIN, CURVATURE_MAX, CURVATURE_ASYMMETRY, SHORT_DISTANCE_THRESHOLD,
    )
    await _move_loop(raw, overshoot, target, duration * 0.15, cp1, cp2, cfg)


async def _move_loop(
    raw: AsyncRawMouse,
    start: Tuple[float, float],
    end: Tuple[float, float],
    duration: float,
    cp1: Tuple[float, float],
    cp2: Tuple[float, float],
    cfg: Any,
) -> None:
    """Frame-by-frame movement loop along a Bezier path with min-jerk timing
    and velocity-inverse Gaussian tremor."""
    tremor_amplitude = float(getattr(cfg, "mouse_wobble_max", TREMOR_BASE))

    loop = asyncio.get_running_loop()
    start_time = loop.time()
    prev = (start[0], start[1], start_time)

    while True:
        now = loop.time()
        elapsed = now - start_time
        if elapsed >= duration:
            break

        # Min-jerk maps real-time progress to position-progress (S-curve).
        t_pos = minimum_jerk(elapsed / duration)
        x, y = bezier_2d(t_pos, start, cp1, cp2, end)

        # Tremor amplitude scales INVERSELY with velocity. Slow movements
        # jitter more (real hand physiology). Cloak's symmetric sin-shaped
        # wobble does the OPPOSITE — middle of move has max jitter, which
        # is detectably wrong.
        sigma = _compute_tremor_sigma(x, y, now, prev, tremor_amplitude)
        x += random.gauss(0.0, sigma)
        y += random.gauss(0.0, sigma)

        await raw.move(round(x), round(y))
        prev = (x, y, now)

        # Per-frame delay with small variance.
        frame_delay = FRAME_INTERVAL + random.uniform(
            -FRAME_INTERVAL_VARIANCE, FRAME_INTERVAL_VARIANCE
        )
        await asyncio.sleep(max(0.001, frame_delay))

        # Stochastic micro-pause (3% per frame, 15-40ms). Extends start_time
        # so the move doesn't get visually rushed afterward.
        if random.random() < MICRO_PAUSE_PROBABILITY:
            pause = random.uniform(*MICRO_PAUSE_RANGE)
            await asyncio.sleep(pause)
            start_time += pause


def _compute_tremor_sigma(
    x: float,
    y: float,
    now: float,
    prev: Tuple[float, float, float],
    amplitude: float,
) -> float:
    """Return Gaussian-noise sigma for the current step.

    Sigma scales as max(0.2, 1 - v/500) * amplitude, so:
      - at 0 px/s velocity → sigma = amplitude (max)
      - at 500 px/s velocity → sigma = 0.2 * amplitude (min)
      - linearly interpolates between
    """
    dt = now - prev[2]
    if dt > 0:
        velocity = math.hypot(x - prev[0], y - prev[1]) / dt
        speed_factor = max(0.2, 1.0 - velocity / 500.0)
    else:
        speed_factor = 1.0
    return amplitude * speed_factor


def _resolve_range(cfg: Any, attr: str, default: Tuple[float, float]) -> Tuple[float, float]:
    """Look up a [low, high] range attribute on cfg, normalising:
      - the value may be a tuple/list (cloakbrowser stores ranges this way),
      - the units may be ms (cloak) or sec (we want sec) — we treat any
        upper bound >= 5 as ms and divide by 1000.
    """
    value = getattr(cfg, attr, None)
    if value is None or not hasattr(value, "__len__") or len(value) < 2:
        return default
    low = float(value[0])
    high = float(value[1])
    # Cloak's keyboard/aim/hold ranges are in ms; idle_pause_range is also ms.
    # Convert anything > 5 to seconds.
    if high > 5.0:
        low /= 1000.0
        high /= 1000.0
    return (low, high)


def _rand_range(rng: Tuple[float, float]) -> float:
    return random.uniform(rng[0], rng[1])
