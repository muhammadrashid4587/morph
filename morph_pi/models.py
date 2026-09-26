"""Strict, validated data model for POINT target selection.

Every constructor validates its inputs and raises ValueError with a clear
message, so invalid numbers (NaN, infinities, out-of-range coordinates)
can never reach the geometry code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from .config import (
    DEFAULT_AMBIGUITY_MARGIN,
    DEFAULT_CLOSENESS_WEIGHT,
    DEFAULT_MAX_RAY_DISTANCE,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_RAY_LENGTH,
    DEFAULT_STABLE_FRAMES,
    DEFAULT_TARGET_SPECS,
)

# Longest possible distance inside the unit square.
MAX_NORMALIZED_DISTANCE = math.sqrt(2.0)


# --- validators --------------------------------------------------------------------


def finite(name: str, value: object) -> float:
    """Return value as a finite float, or raise ValueError."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number, got {type(value).__name__}")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{name} is too large") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return number


def in_range(name: str, value: object, lo: float, hi: float) -> float:
    number = finite(name, value)
    if not lo <= number <= hi:
        raise ValueError(f"{name} must be within [{lo}, {hi}], got {number}")
    return number


def _non_empty_str(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


# --- geometry primitives -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Point2D:
    """A point in normalized image coordinates: x right, y down, both in [0, 1]."""

    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", in_range("Point2D.x", self.x, 0.0, 1.0))
        object.__setattr__(self, "y", in_range("Point2D.y", self.y, 0.0, 1.0))


@dataclass(frozen=True, slots=True)
class Ray2D:
    """A pointing ray starting at `origin` and passing through `through`.

    A zero-length (degenerate) ray is a valid value; it simply selects nothing.
    """

    origin: Point2D
    through: Point2D

    def __post_init__(self) -> None:
        for name in ("origin", "through"):
            if not isinstance(getattr(self, name), Point2D):
                raise ValueError(f"Ray2D.{name} must be a Point2D")


# --- targets ---------------------------------------------------------------------------------


class TargetId(StrEnum):
    BLUE_BLOCK = "blue_block"
    YELLOW_BLOCK = "yellow_block"
    GREEN_BLOCK = "green_block"


TARGET_ORDER: tuple[TargetId, ...] = tuple(TargetId)


@dataclass(frozen=True, slots=True)
class Target:
    id: TargetId
    label: str
    color: str
    center: Point2D

    def __post_init__(self) -> None:
        if not isinstance(self.id, TargetId):
            raise ValueError(f"Target.id must be a TargetId, got {self.id!r}")
        _non_empty_str("Target.label", self.label)
        _non_empty_str("Target.color", self.color)
        if not isinstance(self.center, Point2D):
            raise ValueError("Target.center must be a Point2D")


def default_targets() -> tuple[Target, ...]:
    return tuple(
        Target(TargetId(tid), label, color, Point2D(x, y))
        for tid, label, color, x, y in DEFAULT_TARGET_SPECS
    )


@dataclass(frozen=True, slots=True)
class TargetConfig:
    """The known targets plus every tunable selection threshold."""

    targets: tuple[Target, ...] = field(default_factory=default_targets)
    max_ray_distance: float = DEFAULT_MAX_RAY_DISTANCE
    ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    stable_frames: int = DEFAULT_STABLE_FRAMES
    min_ray_length: float = DEFAULT_MIN_RAY_LENGTH
    closeness_weight: float = DEFAULT_CLOSENESS_WEIGHT

    def __post_init__(self) -> None:
        object.__setattr__(self, "targets", _validate_targets(self.targets))

        max_d = finite("max_ray_distance", self.max_ray_distance)
        if not 0.0 < max_d <= MAX_NORMALIZED_DISTANCE:
            raise ValueError(f"max_ray_distance must be in (0, {MAX_NORMALIZED_DISTANCE:.4f}], got {max_d}")
        margin = finite("ambiguity_margin", self.ambiguity_margin)
        if not 0.0 <= margin < max_d:
            raise ValueError(f"ambiguity_margin must be in [0, max_ray_distance={max_d}), got {margin}")
        min_len = finite("min_ray_length", self.min_ray_length)
        if not 0.0 < min_len < 1.0:
            raise ValueError(f"min_ray_length must be in (0, 1), got {min_len}")
        if isinstance(self.stable_frames, bool) or not isinstance(self.stable_frames, int):
            raise ValueError(f"stable_frames must be an integer, got {type(self.stable_frames).__name__}")
        if not 1 <= self.stable_frames <= 10_000:
            raise ValueError(f"stable_frames must be in [1, 10000], got {self.stable_frames}")

        object.__setattr__(self, "max_ray_distance", max_d)
        object.__setattr__(self, "ambiguity_margin", margin)
        object.__setattr__(self, "min_ray_length", min_len)
        object.__setattr__(self, "min_confidence", in_range("min_confidence", self.min_confidence, 0.0, 1.0))
        object.__setattr__(self, "closeness_weight", in_range("closeness_weight", self.closeness_weight, 0.0, 1.0))

    def target(self, target_id: TargetId) -> Target:
        for target in self.targets:
            if target.id == target_id:
                return target
        raise KeyError(target_id)  # unreachable: validation guarantees every id


def _validate_targets(targets: object) -> tuple[Target, ...]:
    if not isinstance(targets, tuple | list):
        raise ValueError("targets must be a sequence of Target")
    seen: dict[TargetId, Target] = {}
    for target in targets:
        if not isinstance(target, Target):
            raise ValueError(f"targets must contain only Target values, got {type(target).__name__}")
        if target.id in seen:
            raise ValueError(f"duplicate target id '{target.id.value}'")
        seen[target.id] = target
    missing = [tid.value for tid in TARGET_ORDER if tid not in seen]
    if missing:
        raise ValueError(f"missing target(s): {', '.join(missing)}")
    ordered = tuple(seen[tid] for tid in TARGET_ORDER)  # canonical order = tie-break order
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if a.center == b.center:
                raise ValueError(f"targets '{a.id.value}' and '{b.id.value}' share the same center")
    return ordered


# --- selection results -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """A target ahead of the ray and within max_ray_distance of it."""

    target: Target
    ray_distance: float  # perpendicular distance to the ray (normalized units)
    forward_projection: float  # distance along the ray from its origin; > 0 means ahead
    confidence: float  # closeness of this target alone, in [0, 1]

    def __post_init__(self) -> None:
        if not isinstance(self.target, Target):
            raise ValueError("Candidate.target must be a Target")
        if finite("Candidate.ray_distance", self.ray_distance) < 0.0:
            raise ValueError("Candidate.ray_distance must be >= 0")
        if finite("Candidate.forward_projection", self.forward_projection) <= 0.0:
            raise ValueError("Candidate.forward_projection must be > 0 (target ahead of the ray)")
        in_range("Candidate.confidence", self.confidence, 0.0, 1.0)


class SelectionState(StrEnum):
    NONE = "none"
    CANDIDATE = "candidate"
    STABLE = "stable"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """What MORPH understood this frame.

    `stable_target` is set only on the single frame where a target becomes
    STABLE, so consumers can act on `state is STABLE` without de-duplicating.
    """

    state: SelectionState
    best_candidate: Candidate | None
    second_candidate: Candidate | None
    stable_target: TargetId | None
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.state, SelectionState):
            raise ValueError(f"state must be a SelectionState, got {self.state!r}")
        in_range("SelectionResult.confidence", self.confidence, 0.0, 1.0)
        if not isinstance(self.reason, str):
            raise ValueError("reason must be a string")
        best, second = self.best_candidate, self.second_candidate
        if self.state is SelectionState.NONE:
            if best is not None or second is not None:
                raise ValueError("a NONE result must not carry candidates")
            if self.confidence != 0.0:
                raise ValueError("a NONE result must have confidence 0.0")
        elif best is None:
            raise ValueError(f"a {self.state.name} result requires a best_candidate")
        if second is not None and (best is None or second.ray_distance < best.ray_distance):
            raise ValueError("second_candidate must not be closer than best_candidate")
        if self.state is SelectionState.STABLE:
            if best is None or self.stable_target is not best.target.id:
                raise ValueError("a STABLE result's stable_target must be the best candidate's id")
        elif self.stable_target is not None:
            raise ValueError("stable_target is only allowed on a STABLE result")

    @property
    def target_id(self) -> TargetId | None:
        return None if self.best_candidate is None else self.best_candidate.target.id
