"""Hand-landmark helpers for live tracking. Pure Python: no cv2 or mediapipe.

MediaPipe reports 21 hand landmarks in normalized image coordinates
(x right, y down, nominally [0, 1]). The pointing ray runs from the index
finger MCP (landmark 5) through the index fingertip (landmark 8).

Landmarks are validated strictly: a hand is rejected, not clamped, if any
landmark is non-finite or outside [0, 1]. MediaPipe extrapolates
off-frame landmarks, so a partly off-frame hand is not trusted.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Sequence
from dataclasses import dataclass

from .config import DEFAULT_MIN_RAY_LENGTH, INDEX_FINGER_MCP, INDEX_FINGER_TIP
from .geometry import direction, is_degenerate
from .models import Point2D, Ray2D

HAND_LANDMARK_COUNT = 21

# MediaPipe hand topology (landmark index pairs), as in
# mediapipe.tasks.python.vision.HandLandmarksConnections.HAND_CONNECTIONS.
HAND_CONNECTIONS: tuple[tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 4),  # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),  # index finger
    (5, 9), (9, 10), (10, 11), (11, 12),  # middle finger
    (9, 13), (13, 14), (14, 15), (15, 16),  # ring finger
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),  # pinky and palm
)


def _as_point(landmark: object) -> Point2D:
    """Point2D from a Point2D, an (x, y[, ...]) sequence, or an object with .x/.y."""
    if isinstance(landmark, Point2D):
        return landmark
    if hasattr(landmark, "x") and hasattr(landmark, "y"):
        x, y = landmark.x, landmark.y  # e.g. MediaPipe NormalizedLandmark
    elif isinstance(landmark, Sequence) and not isinstance(landmark, str) and len(landmark) >= 2:
        x, y = landmark[0], landmark[1]
    else:
        raise ValueError(f"unsupported landmark type {type(landmark).__name__}")
    # numpy float32 is a numbers.Real but not a float; bools and strings are rejected.
    coords = []
    for value in (x, y):
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise ValueError(f"landmark coordinate must be a number, got {type(value).__name__}")
        coords.append(float(value))
    return Point2D(*coords)  # validates finite and within [0, 1]


def landmarks_to_points(landmarks: Sequence[object]) -> tuple[Point2D, ...]:
    """Validate exactly 21 landmarks. Raises ValueError with the first problem found."""
    if len(landmarks) != HAND_LANDMARK_COUNT:
        raise ValueError(f"expected {HAND_LANDMARK_COUNT} landmarks, got {len(landmarks)}")
    points = []
    for index, landmark in enumerate(landmarks):
        try:
            points.append(_as_point(landmark))
        except ValueError as exc:
            raise ValueError(f"landmark {index}: {exc}") from None
    return tuple(points)


@dataclass(frozen=True, slots=True)
class HandObservation:
    """One detected hand: 21 validated landmarks in camera (unmirrored) coordinates."""

    landmarks: tuple[Point2D, ...]
    score: float | None = None  # detector confidence, if known

    def __post_init__(self) -> None:
        object.__setattr__(self, "landmarks", landmarks_to_points(self.landmarks))
        if self.score is not None and not (
            isinstance(self.score, int | float) and not isinstance(self.score, bool)
            and math.isfinite(self.score) and 0.0 <= self.score <= 1.0
        ):
            raise ValueError(f"score must be None or within [0, 1], got {self.score!r}")

    @property
    def ray(self) -> Ray2D | None:
        return ray_from_landmarks(self.landmarks)

    def mirrored(self) -> HandObservation:
        """The same hand as seen in a horizontally mirrored image."""
        return HandObservation(tuple(mirror_point(p) for p in self.landmarks), self.score)


def ray_rejection_reason(
    landmarks: Sequence[object], min_length: float = DEFAULT_MIN_RAY_LENGTH
) -> str | None:
    """Why no pointing ray can be built from these landmarks (None when it can)."""
    if len(landmarks) < HAND_LANDMARK_COUNT:
        return f"only {len(landmarks)} of {HAND_LANDMARK_COUNT} landmarks"
    try:
        points = landmarks_to_points(landmarks[:HAND_LANDMARK_COUNT])
    except ValueError as exc:
        return str(exc)
    if is_degenerate(Ray2D(points[INDEX_FINGER_MCP], points[INDEX_FINGER_TIP]), min_length):
        return "index finger ray too short (landmarks 5 and 8 overlap)"
    return None


def ray_from_landmarks(
    landmarks: Sequence[object], min_length: float = DEFAULT_MIN_RAY_LENGTH
) -> Ray2D | None:
    """Pointing ray from landmark 5 through landmark 8, or None if the hand is unusable.

    None when: fewer than 21 landmarks, any landmark non-finite or outside
    [0, 1] (never clamped), or landmarks 5 and 8 closer than min_length.
    """
    if ray_rejection_reason(landmarks, min_length) is not None:
        return None
    points = landmarks_to_points(landmarks[:HAND_LANDMARK_COUNT])
    return Ray2D(points[INDEX_FINGER_MCP], points[INDEX_FINGER_TIP])


# --- display helpers (pure math, used by the camera viewer) ------------------------------


def mirror_point(point: Point2D) -> Point2D:
    """Reflect across the vertical center line: x -> 1 - x."""
    return Point2D(1.0 - point.x, point.y)


def mirror_ray(ray: Ray2D) -> Ray2D:
    return Ray2D(mirror_point(ray.origin), mirror_point(ray.through))


def ray_exit_point(ray: Ray2D) -> Point2D:
    """Where the forward ray leaves the unit square (the image border).

    A straight line in normalized coordinates is also straight in pixels, so
    this endpoint is valid for any image aspect ratio.
    """
    dx, dy = direction(ray)
    t_exit = math.inf
    for origin, d in ((ray.origin.x, dx), (ray.origin.y, dy)):
        if d > 0.0:
            t_exit = min(t_exit, (1.0 - origin) / d)
        elif d < 0.0:
            t_exit = min(t_exit, -origin / d)
    x = ray.origin.x + t_exit * dx
    y = ray.origin.y + t_exit * dy
    return Point2D(min(1.0, max(0.0, x)), min(1.0, max(0.0, y)))  # absorb float error
