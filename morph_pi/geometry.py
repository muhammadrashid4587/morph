"""Pure 2D ray geometry in normalized image coordinates.

Inputs are validated Point2D values (finite, in [0, 1]), so the only way to
produce NaN would be dividing by a zero-length ray. Functions that need a
direction raise DegenerateRayError instead of dividing by zero.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .config import INDEX_FINGER_MCP, INDEX_FINGER_TIP
from .models import Point2D, Ray2D, finite

Vector2D = tuple[float, float]

# Rays shorter than this have no meaningful direction.
EPSILON = 1e-9


class DegenerateRayError(ValueError):
    """The ray's origin and through-point coincide, so it has no direction."""


def ray_length(ray: Ray2D) -> float:
    return math.hypot(ray.through.x - ray.origin.x, ray.through.y - ray.origin.y)


def is_degenerate(ray: Ray2D, min_length: float = EPSILON) -> bool:
    """True when the ray is too short to have a usable direction."""
    if finite("min_length", min_length) < 0.0:
        raise ValueError("min_length must be >= 0")
    return ray_length(ray) <= max(min_length, EPSILON)


def direction(ray: Ray2D) -> Vector2D:
    """Unit vector from origin toward the through-point."""
    length = ray_length(ray)
    if length <= EPSILON:
        raise DegenerateRayError("ray origin and through-point coincide; direction is undefined")
    return ((ray.through.x - ray.origin.x) / length, (ray.through.y - ray.origin.y) / length)


def forward_projection(point: Point2D, ray: Ray2D) -> float:
    """Signed distance along the ray from its origin to the point's projection.

    Positive: ahead of the origin. Zero or negative: level with or behind it.
    """
    dx, dy = direction(ray)
    return (point.x - ray.origin.x) * dx + (point.y - ray.origin.y) * dy


def is_behind(point: Point2D, ray: Ray2D) -> bool:
    """True when the point is not strictly ahead of the ray origin."""
    return forward_projection(point, ray) <= 0.0


def ray_distance(point: Point2D, ray: Ray2D) -> float:
    """Shortest distance from the point to the forward ray (a half-line).

    Perpendicular distance for points ahead of the origin; distance to the
    origin itself for points behind it, so nothing behind the hand looks close.
    """
    dx, dy = direction(ray)
    px, py = point.x - ray.origin.x, point.y - ray.origin.y
    if px * dx + py * dy <= 0.0:
        return math.hypot(px, py)
    return abs(dx * py - dy * px)


def ray_from_hand_landmarks(landmarks: Sequence[Point2D]) -> Ray2D:
    """Pointing ray from index MCP (landmark 5) through index fingertip (landmark 8)."""
    if len(landmarks) <= max(INDEX_FINGER_MCP, INDEX_FINGER_TIP):
        raise ValueError(
            f"need at least {max(INDEX_FINGER_MCP, INDEX_FINGER_TIP) + 1} hand landmarks, got {len(landmarks)}"
        )
    return Ray2D(origin=landmarks[INDEX_FINGER_MCP], through=landmarks[INDEX_FINGER_TIP])
