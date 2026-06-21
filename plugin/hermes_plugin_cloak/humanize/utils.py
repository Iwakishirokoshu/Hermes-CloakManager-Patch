"""Pure motor-control math for humanized mouse and keyboard.

Ported from pydoll's `pydoll/interactions/utils.py` (MIT, autoscrape-labs).
We adopt the math wholesale because cloakbrowser's stock implementation is
weaker on every axis: it uses symmetric easeInOut instead of a minimum-jerk
profile, has symmetric sine-shaped wobble (real human tremor scales inversely
with velocity), fixed control-point bias (real hand movement has asymmetric
ballistic/correction phases), unconditional overshoot (only fast moves
overshoot in reality), and no Fitts's Law sizing of duration vs distance.

All functions here are deterministic given a (seeded) random source — but
they call `random.random` / `random.uniform` directly for simplicity. Tests
seed `random.seed(...)` to make them reproducible.
"""
from __future__ import annotations

import math
import random
from typing import Tuple

Point = Tuple[float, float]


def minimum_jerk(t: float) -> float:
    """Position curve for a minimum-jerk movement at normalized time t in [0, 1].

    Returns 10t^3 - 15t^4 + 6t^5, which produces a bell-shaped velocity
    profile (slow start, peak in the middle, slow end) — the standard model
    of a single-joint reaching movement from Flash & Hogan (1985).

    At t = 0 returns 0; at t = 1 returns 1. Monotone in between.
    """
    t2 = t * t
    t3 = t2 * t
    t4 = t3 * t
    t5 = t4 * t
    return 10.0 * t3 - 15.0 * t4 + 6.0 * t5


def bezier_2d(t: float, p0: Point, p1: Point, p2: Point, p3: Point) -> Point:
    """Evaluate a 2D cubic Bezier curve at parameter t.

    B(t) = (1-t)^3*P0 + 3(1-t)^2*t*P1 + 3(1-t)*t^2*P2 + t^3*P3
    """
    u = 1.0 - t
    u2 = u * u
    u3 = u2 * u
    t2 = t * t
    t3 = t2 * t
    x = u3 * p0[0] + 3.0 * u2 * t * p1[0] + 3.0 * u * t2 * p2[0] + t3 * p3[0]
    y = u3 * p0[1] + 3.0 * u2 * t * p1[1] + 3.0 * u * t2 * p2[1] + t3 * p3[1]
    return (x, y)


def fitts_duration(distance: float, target_width: float, a: float, b: float) -> float:
    """Fitts's Law: MT = a + b * log2(D/W + 1).

    For mouse movements the index of difficulty (ID) is log2(D/W + 1).
    Pydoll's defaults a=0.07s, b=0.15s/bit produce realistic clip times
    (e.g. ~250ms for a 400px move to a 20px target).
    """
    if distance <= 0:
        return a
    return a + b * math.log2(distance / target_width + 1.0)


def random_control_points(
    start: Point,
    end: Point,
    curvature_min: float,
    curvature_max: float,
    curvature_asymmetry: float,
    short_distance_threshold: float,
) -> Tuple[Point, Point]:
    """Generate randomized 2D Bezier control points for a curved mouse path.

    Both control points are offset perpendicular to the straight-line start-end
    segment by a random amount in [curvature_min, curvature_max] * distance.
    The first control point sits earlier along the path (ballistic phase),
    the second later (correction phase), giving a realistic ballistic /
    correction asymmetry that cloakbrowser's fixed 25%/75% bias lacks.
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = math.hypot(dx, dy)

    if distance < 1.0:
        return (start, end)

    perp = (-dy / distance, dx / distance)

    scale = min(1.0, distance / short_distance_threshold)
    offsets = (
        random.uniform(curvature_min, curvature_max) * distance * scale,
        random.uniform(curvature_min, curvature_max) * distance * scale,
    )

    sign = random.choice([-1.0, 1.0])
    t1 = random.uniform(0.2, curvature_asymmetry)
    t2 = random.uniform(curvature_asymmetry, 0.8)

    cp1 = (
        start[0] + dx * t1 + perp[0] * offsets[0] * sign,
        start[1] + dy * t1 + perp[1] * offsets[0] * sign,
    )

    counter = random.uniform(0.3, 1.0)
    cp2 = (
        start[0] + dx * t2 + perp[0] * offsets[1] * sign * counter,
        start[1] + dy * t2 + perp[1] * offsets[1] * sign * counter,
    )

    return (cp1, cp2)
