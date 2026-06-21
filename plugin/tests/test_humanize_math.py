"""Unit tests for the pydoll-ported humanize math.

These have no Playwright / cloakbrowser dependency — they exercise the
pure helpers in `hermes_plugin_cloak.humanize.utils` and the cfg-mapping
logic in `mouse_async`.
"""
from __future__ import annotations

import math
import random

import pytest

# Importing this triggers humanize.install() — which is fine here because
# cloakbrowser isn't imported in this test file at all, so the hook
# succeeds and stays out of the way.
from hermes_plugin_cloak.humanize import utils
from hermes_plugin_cloak.humanize import mouse_async as ma


# ----------------------------------------------------------------------------
# minimum_jerk
# ----------------------------------------------------------------------------


def test_minimum_jerk_boundary_values():
    assert utils.minimum_jerk(0.0) == pytest.approx(0.0, abs=1e-12)
    assert utils.minimum_jerk(1.0) == pytest.approx(1.0, abs=1e-12)


def test_minimum_jerk_monotone_increasing():
    """The S-curve should be strictly increasing on (0, 1)."""
    samples = [utils.minimum_jerk(i / 100) for i in range(101)]
    for prev, curr in zip(samples, samples[1:]):
        assert curr >= prev


def test_minimum_jerk_symmetric_around_half():
    """minimum_jerk(t) + minimum_jerk(1-t) == 1 for symmetric polynomial."""
    for t in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0):
        assert utils.minimum_jerk(t) + utils.minimum_jerk(1.0 - t) == pytest.approx(1.0, abs=1e-12)


# ----------------------------------------------------------------------------
# bezier_2d
# ----------------------------------------------------------------------------


def test_bezier_endpoints_exact():
    p0, p1, p2, p3 = (0.0, 0.0), (10.0, 20.0), (30.0, -5.0), (40.0, 50.0)
    assert utils.bezier_2d(0.0, p0, p1, p2, p3) == pytest.approx(p0)
    assert utils.bezier_2d(1.0, p0, p1, p2, p3) == pytest.approx(p3)


def test_bezier_midpoint_is_inside_convex_hull():
    """The Bezier at t=0.5 stays inside the rectangle bounding all 4 control points."""
    p0, p1, p2, p3 = (0.0, 0.0), (10.0, 20.0), (30.0, -5.0), (40.0, 50.0)
    xs = [p0[0], p1[0], p2[0], p3[0]]
    ys = [p0[1], p1[1], p2[1], p3[1]]
    mid = utils.bezier_2d(0.5, p0, p1, p2, p3)
    assert min(xs) <= mid[0] <= max(xs)
    assert min(ys) <= mid[1] <= max(ys)


# ----------------------------------------------------------------------------
# fitts_duration
# ----------------------------------------------------------------------------


def test_fitts_duration_zero_distance():
    """Zero distance returns the base time `a`."""
    assert utils.fitts_duration(0.0, 20.0, 0.07, 0.15) == pytest.approx(0.07)


def test_fitts_duration_monotone_in_distance():
    """Longer move = longer duration, all else equal."""
    target_w = 20.0
    durations = [utils.fitts_duration(d, target_w, 0.07, 0.15) for d in (10, 50, 100, 500, 2000)]
    for prev, curr in zip(durations, durations[1:]):
        assert curr > prev


def test_fitts_duration_smaller_target_takes_longer():
    """Same distance, smaller target -> longer move (higher ID)."""
    d = 400.0
    big_target = utils.fitts_duration(d, 100.0, 0.07, 0.15)
    small_target = utils.fitts_duration(d, 5.0, 0.07, 0.15)
    assert small_target > big_target


def test_fitts_realistic_400px_to_20px():
    """A 400px move to a 20px target should land near ~0.6s with default coeffs."""
    dur = utils.fitts_duration(400.0, 20.0, 0.07, 0.15)
    assert 0.4 < dur < 0.8


# ----------------------------------------------------------------------------
# random_control_points
# ----------------------------------------------------------------------------


def test_control_points_perpendicular_offset_magnitude():
    """Control points should sit perpendicular to the line, offset proportional
    to distance (when distance >= short_distance_threshold)."""
    random.seed(42)
    start, end = (0.0, 0.0), (300.0, 0.0)
    cp1, cp2 = utils.random_control_points(
        start, end,
        curvature_min=0.10, curvature_max=0.30,
        curvature_asymmetry=0.6, short_distance_threshold=50.0,
    )
    # Movement is along x-axis, so perpendicular offset is along y.
    # cp1.y should be non-zero (perpendicular component).
    assert abs(cp1[1]) > 1.0
    assert abs(cp2[1]) > 0.5


def test_control_points_short_distance_returns_endpoints():
    """For distance < 1, returns (start, end) unchanged."""
    p0, p1 = (10.0, 10.0), (10.1, 10.1)
    cp1, cp2 = utils.random_control_points(
        p0, p1, 0.10, 0.30, 0.6, 50.0,
    )
    assert cp1 == p0
    assert cp2 == p1


def test_control_points_distance_scaled():
    """Larger distance -> larger absolute perpendicular offset."""
    random.seed(7)
    cp_small = utils.random_control_points(
        (0.0, 0.0), (60.0, 0.0), 0.20, 0.20, 0.6, 50.0,
    )
    random.seed(7)
    cp_large = utils.random_control_points(
        (0.0, 0.0), (600.0, 0.0), 0.20, 0.20, 0.6, 50.0,
    )
    # Same seed, same fixed curvature → same proportional offset; absolute
    # offset must scale ~linearly with distance.
    ratio = abs(cp_large[0][1]) / max(abs(cp_small[0][1]), 1e-9)
    assert 8.0 < ratio < 12.0  # ~10x distance -> ~10x offset


# ----------------------------------------------------------------------------
# mouse_async helpers (cfg resolution)
# ----------------------------------------------------------------------------


class _FakeCfg:
    """Minimal cloakbrowser HumanConfig-like for cfg lookups."""
    click_aim_delay_button = (80, 200)         # cloak ms
    click_aim_delay_input = (60, 140)
    click_hold_button = (60, 150)
    click_hold_input = (40, 100)
    idle_drift_px = 3
    idle_pause_range = (300, 1000)
    mouse_wobble_max = 1.5
    mouse_overshoot_chance = 0.15


def test_resolve_range_ms_to_seconds():
    """cloak ranges are in ms; _resolve_range normalises to seconds."""
    cfg = _FakeCfg()
    lo, hi = ma._resolve_range(cfg, "click_aim_delay_button", (0.05, 0.20))
    # Cloak default (80,200) ms -> (0.080, 0.200) sec
    assert lo == pytest.approx(0.080)
    assert hi == pytest.approx(0.200)


def test_resolve_range_seconds_left_alone():
    """If the value is already in seconds (upper bound < 5), keep as-is."""
    class _CfgSec:
        x = (0.05, 0.20)
    lo, hi = ma._resolve_range(_CfgSec(), "x", (0.0, 0.0))
    assert lo == pytest.approx(0.05)
    assert hi == pytest.approx(0.20)


def test_resolve_range_missing_returns_default():
    class _Empty:
        pass
    default = (0.1, 0.2)
    assert ma._resolve_range(_Empty(), "nonexistent", default) == default


def test_compute_tremor_sigma_velocity_inverse():
    """Tremor sigma must DECREASE as velocity increases.

    This is the key insight pydoll has and cloakbrowser doesn't — real
    human tremor scales inversely with velocity, while cloak's sin-shaped
    wobble peaks in the middle of the move (opposite).
    """
    # Same position, same time -> velocity 0 -> max sigma.
    slow_sigma = ma._compute_tremor_sigma(
        x=10.0, y=10.0, now=2.0,
        prev=(10.0, 10.0, 1.0),  # dt=1s, distance=0 -> v=0
        amplitude=1.0,
    )
    # Position jumped 500px in 1s -> v=500 -> min sigma (0.2 * amplitude).
    fast_sigma = ma._compute_tremor_sigma(
        x=510.0, y=10.0, now=2.0,
        prev=(10.0, 10.0, 1.0),
        amplitude=1.0,
    )
    assert slow_sigma > fast_sigma
    assert slow_sigma == pytest.approx(1.0)        # full amplitude at v=0
    assert fast_sigma == pytest.approx(0.2)        # floor at v>=500


def test_compute_tremor_sigma_handles_zero_dt():
    """When dt=0 we fall back to speed_factor=1.0 (full amplitude)."""
    sigma = ma._compute_tremor_sigma(
        x=10.0, y=10.0, now=1.0,
        prev=(0.0, 0.0, 1.0),  # dt=0
        amplitude=2.5,
    )
    assert sigma == pytest.approx(2.5)
