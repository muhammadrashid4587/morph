import math

import pytest

from morph_pi.geometry import (
    DegenerateRayError,
    direction,
    forward_projection,
    is_behind,
    is_degenerate,
    ray_distance,
    ray_from_hand_landmarks,
    ray_length,
)
from morph_pi.models import Point2D, Ray2D


def ray(ox: float, oy: float, tx: float, ty: float) -> Ray2D:
    return Ray2D(Point2D(ox, oy), Point2D(tx, ty))


# --- Point2D / Ray2D validation --------------------------------------------------------


@pytest.mark.parametrize(("x", "y"), [(0, 0), (1, 1), (0.0, 1.0), (0.5, 0.25)])
def test_point_accepts_normalized_coordinates(x: float, y: float) -> None:
    p = Point2D(x, y)
    assert (p.x, p.y) == (float(x), float(y))
    assert isinstance(p.x, float) and isinstance(p.y, float)


@pytest.mark.parametrize(
    ("x", "y", "match"),
    [
        (math.nan, 0.5, "finite"),
        (0.5, math.inf, "finite"),
        (-math.inf, 0.5, "finite"),
        (-0.01, 0.5, r"within \[0.0, 1.0\]"),
        (0.5, 1.01, r"within \[0.0, 1.0\]"),
        (1920, 1080, r"within \[0.0, 1.0\]"),
        (True, 0.5, "must be a number"),
        ("0.5", 0.5, "must be a number"),
        (None, 0.5, "must be a number"),
        (10**400, 0.5, "too large"),
    ],
)
def test_point_rejects_invalid_values(x: object, y: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        Point2D(x, y)  # type: ignore[arg-type]


def test_ray_requires_points() -> None:
    with pytest.raises(ValueError, match="Ray2D.origin"):
        Ray2D((0.1, 0.2), Point2D(0.3, 0.4))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Ray2D.through"):
        Ray2D(Point2D(0.1, 0.2), None)  # type: ignore[arg-type]


# --- direction / length / degeneracy ----------------------------------------------------------


def test_direction_is_a_unit_vector() -> None:
    assert direction(ray(0.1, 0.5, 0.6, 0.5)) == pytest.approx((1.0, 0.0))
    assert direction(ray(0.5, 0.9, 0.5, 0.8)) == pytest.approx((0.0, -1.0))  # pointing up the image
    dx, dy = direction(ray(0.1, 0.1, 0.4, 0.5))
    assert math.hypot(dx, dy) == pytest.approx(1.0)
    assert (dx, dy) == pytest.approx((0.6, 0.8))


def test_zero_length_ray_has_no_direction() -> None:
    zero = ray(0.3, 0.3, 0.3, 0.3)
    assert ray_length(zero) == 0.0
    assert is_degenerate(zero)
    for fn in (direction, lambda r: forward_projection(Point2D(0.5, 0.5), r), lambda r: ray_distance(Point2D(0.5, 0.5), r)):
        with pytest.raises(DegenerateRayError):
            fn(zero)
    assert issubclass(DegenerateRayError, ValueError)


def test_is_degenerate_with_minimum_length() -> None:
    short = ray(0.5, 0.5, 0.505, 0.5)
    assert ray_length(short) == pytest.approx(0.005)
    assert is_degenerate(short, min_length=0.01)
    assert not is_degenerate(short, min_length=0.001)
    assert not is_degenerate(short)  # default: only (near-)zero length is degenerate


@pytest.mark.parametrize("bad", [-0.1, math.nan, math.inf])
def test_is_degenerate_rejects_invalid_minimum(bad: float) -> None:
    with pytest.raises(ValueError):
        is_degenerate(ray(0.1, 0.1, 0.2, 0.2), min_length=bad)


# --- projection / behind / distance -----------------------------------------------------------


def test_forward_projection_sign() -> None:
    r = ray(0.2, 0.5, 0.3, 0.5)  # pointing right
    assert forward_projection(Point2D(0.7, 0.5), r) == pytest.approx(0.5)
    assert forward_projection(Point2D(0.7, 0.9), r) == pytest.approx(0.5)  # off-axis still projects ahead
    assert forward_projection(Point2D(0.1, 0.5), r) == pytest.approx(-0.1)
    assert forward_projection(Point2D(0.2, 0.9), r) == pytest.approx(0.0)


def test_is_behind() -> None:
    r = ray(0.2, 0.5, 0.3, 0.5)
    assert not is_behind(Point2D(0.21, 0.5), r)
    assert is_behind(Point2D(0.1, 0.5), r)
    assert is_behind(Point2D(0.2, 0.1), r)  # level with the origin is not "ahead"


def test_ray_distance_is_perpendicular_for_points_ahead() -> None:
    r = ray(0.1, 0.5, 0.2, 0.5)
    assert ray_distance(Point2D(0.7, 0.6), r) == pytest.approx(0.1)
    assert ray_distance(Point2D(0.9, 0.5), r) == pytest.approx(0.0)
    diag = ray(0.0, 0.0, 0.1, 0.1)
    assert ray_distance(Point2D(0.5, 0.3), diag) == pytest.approx(0.2 / math.sqrt(2))


def test_ray_distance_behind_origin_is_distance_to_origin() -> None:
    r = ray(0.5, 0.5, 0.6, 0.5)
    assert ray_distance(Point2D(0.2, 0.5), r) == pytest.approx(0.3)  # on the line, but behind
    assert ray_distance(Point2D(0.2, 0.9), r) == pytest.approx(0.5)


def test_results_do_not_depend_on_fingertip_distance() -> None:
    near, far = ray(0.1, 0.9, 0.15, 0.85), ray(0.1, 0.9, 0.6, 0.4)
    p = Point2D(0.5, 0.62)
    assert ray_distance(p, near) == pytest.approx(ray_distance(p, far))
    assert forward_projection(p, near) == pytest.approx(forward_projection(p, far))


def test_no_nan_across_a_grid_of_rays() -> None:
    steps = [i / 10 for i in range(11)]
    targets = [Point2D(x, y) for x in steps[::2] for y in steps[::2]]
    for ox in steps:
        for oy in steps:
            for dx, dy in ((1e-6, 0.0), (0.05, 0.03), (-0.2, 0.2), (0.0, -1e-6)):
                tx, ty = ox + dx, oy + dy
                if not (0.0 <= tx <= 1.0 and 0.0 <= ty <= 1.0):
                    continue
                r = ray(ox, oy, tx, ty)
                if is_degenerate(r):
                    continue
                for p in targets:
                    d, t = ray_distance(p, r), forward_projection(p, r)
                    assert math.isfinite(d) and d >= 0.0 and math.isfinite(t)


# --- landmarks ------------------------------------------------------------------------------------


def test_ray_from_hand_landmarks_uses_mcp_5_and_tip_8() -> None:
    landmarks = [Point2D(i / 40, i / 40) for i in range(21)]
    r = ray_from_hand_landmarks(landmarks)
    assert r.origin == landmarks[5]
    assert r.through == landmarks[8]


def test_ray_from_hand_landmarks_needs_enough_points() -> None:
    with pytest.raises(ValueError, match="at least 9"):
        ray_from_hand_landmarks([Point2D(0.5, 0.5)] * 8)
