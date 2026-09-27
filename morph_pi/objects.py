"""Any-object pointing: pick the detected box the pointing ray hits (pure logic, no cv2/mediapipe).

Selection, in order:
1. "ray":     the box the forward ray enters first (smallest entry distance). A box under
              the fingertip is always hit, because the ray passes through the fingertip.
2. "nearest": otherwise the box nearest the fingertip, within NEAREST_MAX (normalized units).
3. "region":  nothing detected there -> a fixed region under the fingertip, named OBJECT.
No usable ray (e.g. index finger ray too short) -> select_at_fingertip(): the box under the
fingertip ("fingertip"), else the nearest one, else the region. The same lock applies.
Boxes containing the pointing hand's own knuckle (landmark 5) are skipped: that is the
person/hand doing the pointing, not the target.

Locking uses the same rule as colors (smoothing.py): the same key on stable_frames
consecutive frames -> STABLE once, latched while held; any change or no ray releases it.
Coordinates are camera-space normalized [0, 1], like the ray.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .config import DEFAULT_MIN_RAY_LENGTH, DEFAULT_STABLE_FRAMES, INDEX_FINGER_MCP, INDEX_FINGER_TIP
from .geometry import direction
from .hand_tracking import HAND_LANDMARK_COUNT, landmarks_to_points, ray_from_landmarks
from .models import Point2D, Ray2D

NEAREST_MAX = 0.15   # a box farther than this from the fingertip is not "near"
REGION_HALF = 0.06   # the fallback region is 0.12 x 0.12 around the fingertip
REGION_NAME = "OBJECT"


@dataclass(frozen=True, slots=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float
    label: str
    score: float

    @property
    def area(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    def contains(self, p: Point2D) -> bool:
        return self.x0 <= p.x <= self.x1 and self.y0 <= p.y <= self.y1

    def distance_to(self, p: Point2D) -> float:
        dx = max(self.x0 - p.x, 0.0, p.x - self.x1)
        dy = max(self.y0 - p.y, 0.0, p.y - self.y1)
        return math.hypot(dx, dy)


@dataclass(frozen=True, slots=True)
class Choice:
    box: Box
    how: str  # "ray", "fingertip", "nearest" or "region"

    @property
    def name(self) -> str:
        return self.box.label.upper()


def ray_entry(ray: Ray2D, box: Box) -> float | None:
    """Distance along the ray where it enters `box` (slab method), or None if it misses.

    A box containing the ray origin is not "hit" (the ray starts inside it).
    """
    if box.contains(ray.origin):
        return None
    dx, dy = direction(ray)
    t_min, t_max = 0.0, math.inf
    for o, d, lo, hi in ((ray.origin.x, dx, box.x0, box.x1), (ray.origin.y, dy, box.y0, box.y1)):
        if abs(d) < 1e-12:
            if not lo <= o <= hi:
                return None
            continue
        t1, t2 = (lo - o) / d, (hi - o) / d
        t_min, t_max = max(t_min, min(t1, t2)), min(t_max, max(t1, t2))
        if t_min > t_max:
            return None
    return t_min


def region_box(tip: Point2D) -> Box:
    return Box(
        max(0.0, tip.x - REGION_HALF), max(0.0, tip.y - REGION_HALF),
        min(1.0, tip.x + REGION_HALF), min(1.0, tip.y + REGION_HALF),
        REGION_NAME, 0.0,
    )


def select_box(ray: Ray2D, boxes: Sequence[Box]) -> Choice:
    """The box the user points at (see module doc for the order). Never None."""
    candidates = [b for b in boxes if not b.contains(ray.origin)]  # skip the pointing hand/person
    hits = [(t, b) for b in candidates if (t := ray_entry(ray, b)) is not None]
    if hits:
        return Choice(min(hits, key=lambda h: (h[0], -h[1].score))[1], "ray")
    return _nearest_or_region(ray.through, candidates)


def select_at_fingertip(tip: Point2D, knuckle: Point2D, boxes: Sequence[Box]) -> Choice:
    """No usable ray: the box under the fingertip (smallest), else the nearest one, else the region.

    A "person" box containing the knuckle (landmark 5) is the pointing hand's owner and is skipped.
    Other boxes under the hand stay: a curled finger often rests on the very object it means.
    """
    candidates = [b for b in boxes if not (b.label.lower() == "person" and b.contains(knuckle))]
    under = [b for b in candidates if b.contains(tip)]
    if under:
        return Choice(min(under, key=lambda b: (b.area, -b.score)), "fingertip")
    return _nearest_or_region(tip, candidates)


def _nearest_or_region(tip: Point2D, candidates: Sequence[Box]) -> Choice:
    near = [(b.distance_to(tip), b) for b in candidates]
    near = [n for n in near if n[0] <= NEAREST_MAX]
    if near:
        return Choice(min(near, key=lambda n: (n[0], -n[1].score))[1], "nearest")
    return Choice(region_box(tip), "region")


def boxes_from_detections(result: Any, width: int, height: int) -> list[Box]:
    """MediaPipe DetectionResult (pixel boxes on a width x height image) -> normalized Boxes."""
    boxes = []
    for det in getattr(result, "detections", None) or []:
        bb, cats = det.bounding_box, det.categories
        if not cats or width <= 0 or height <= 0:
            continue
        x0 = min(1.0, max(0.0, bb.origin_x / width))
        y0 = min(1.0, max(0.0, bb.origin_y / height))
        x1 = min(1.0, max(0.0, (bb.origin_x + bb.width) / width))
        y1 = min(1.0, max(0.0, (bb.origin_y + bb.height) / height))
        if x1 > x0 and y1 > y0:
            cat = cats[0]
            boxes.append(Box(x0, y0, x1, y1, cat.category_name or cat.display_name or "object", float(cat.score or 0.0)))
    return boxes


class EveryNth:
    """Run the detector on frame 1, n+1, 2n+1, ... and reuse its boxes in between."""

    def __init__(self, n: int = 3) -> None:
        if n < 1:
            raise ValueError("n must be >= 1")
        self.n = n
        self.count = 0

    def due(self) -> bool:
        due = self.count % self.n == 0
        self.count += 1
        return due


class ObjectLock:
    """The colors lock rule for arbitrary keys: stable_frames in a row -> STABLE once, latched while held."""

    def __init__(self, stable_frames: int = DEFAULT_STABLE_FRAMES) -> None:
        self.stable_frames = stable_frames
        self.key: str | None = None
        self.streak = 0
        self.latched = False

    def reset(self) -> None:
        self.key, self.streak, self.latched = None, 0, False

    def update(self, key: str) -> bool:
        """Feed this frame's target key; True only on the frame it becomes STABLE."""
        if key != self.key:
            self.reset()
            self.key = key
        self.streak += 1
        if not self.latched and self.streak >= self.stable_frames:
            self.latched = True
            return True
        return False


@dataclass(frozen=True, slots=True)
class ObjectFrame:
    hand_detected: bool
    ray: Ray2D | None
    choice: Choice | None
    boxes: tuple[Box, ...]
    streak: int
    locked: bool       # STABLE emitted for this gesture and still held
    just_locked: bool  # the single STABLE frame
    stable_frames: int

    @property
    def status(self) -> str:
        if not self.hand_detected:
            return "NO HAND"
        if self.choice is None:
            return "NO RAY"  # landmarks unusable: no ray and no fingertip
        return "LOCKED" if self.locked else "CANDIDATE"

    @property
    def label(self) -> str:
        """LOCKED: CUP (0.87) / CUP (0.87), or LOCKED: OBJECT for the fingertip region."""
        if self.choice is None:
            return self.status
        name = self.choice.name
        conf = "" if self.choice.how == "region" else f" ({self.choice.box.score:.2f})"
        return f"LOCKED: {name}{conf}" if self.locked else f"{name}{conf}"

    def summary(self) -> str:
        state = "STABLE" if self.just_locked else ("NONE" if self.choice is None else "CANDIDATE")
        target = self.choice.name if self.choice else "-"
        conf = self.choice.box.score if self.choice else 0.0
        how = f" via {self.choice.how}" if self.choice else ""
        line = f"state={state} target={target} confidence={conf:.2f} frames={min(self.streak, self.stable_frames)}/{self.stable_frames}{how}"
        if not self.hand_detected:
            return line + " (no hand)"
        if self.choice is None:
            return line + " (no ray)"
        no_ray = " (no ray: fingertip)" if self.ray is None else ""
        return line + no_ray + (" (locked)" if self.locked and not self.just_locked else "")


class ObjectPointPipeline:
    """Per-session any-object pointing: one lock reused across frames (like PointPipeline)."""

    def __init__(self, stable_frames: int = DEFAULT_STABLE_FRAMES, min_ray_length: float = DEFAULT_MIN_RAY_LENGTH) -> None:
        self.lock = ObjectLock(stable_frames)
        self.min_ray_length = min_ray_length

    def process(self, landmarks: Sequence[object] | None, boxes: Sequence[Box]) -> ObjectFrame:
        boxes = tuple(boxes)
        if not landmarks:
            self.lock.reset()
            return self._frame(False, None, None, boxes, False)
        ray = ray_from_landmarks(landmarks, self.min_ray_length)
        if ray is None:
            try:  # e.g. "index finger ray too short": fall back to the fingertip
                points = landmarks_to_points(landmarks[:HAND_LANDMARK_COUNT])
            except ValueError:  # too few / off-frame / non-numeric landmarks: nothing usable
                self.lock.reset()
                return self._frame(True, None, None, boxes, False)
            choice = select_at_fingertip(points[INDEX_FINGER_TIP], points[INDEX_FINGER_MCP], boxes)
            return self._frame(True, None, choice, boxes, self.lock.update(choice.name))
        choice = select_box(ray, boxes)
        just = self.lock.update(choice.name)
        return self._frame(True, ray, choice, boxes, just)

    def _frame(self, hand: bool, ray: Ray2D | None, choice: Choice | None, boxes: tuple[Box, ...], just: bool) -> ObjectFrame:
        return ObjectFrame(hand, ray, choice, boxes, self.lock.streak, self.lock.latched, just, self.lock.stable_frames)
