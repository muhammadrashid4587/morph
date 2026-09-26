"""Deterministic POINT scenarios: scripted rays -> selection -> smoothing.

Rays are built from the active config's target centers, so scenarios stay
meaningful for a custom target layout. Each scenario states its expected
STABLE events and allowed states; a run reports PASS/FAIL against them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import Point2D, Ray2D, SelectionResult, SelectionState, Target, TargetConfig, TargetId
from .selection import select_target
from .smoothing import TemporalSmoother

SCENARIO_NAMES: tuple[str, ...] = ("blue", "yellow", "green", "ambiguous", "none", "reacquire", "release")

FINGER_LENGTH = 0.08  # MCP -> fingertip distance used for synthetic rays
AIM_ERROR = 0.01  # clear rays miss the target center by this much (realistic, not perfect)
HOLD_FRAMES = 4  # extra frames held after STABLE, proving it is emitted only once
HAND_DISTANCE = 0.33  # hand-to-target distance for clear rays

_S = SelectionState
_TARGET_FOR = {"blue": TargetId.BLUE_BLOCK, "yellow": TargetId.YELLOW_BLOCK, "green": TargetId.GREEN_BLOCK}


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    description: str
    rays: tuple[Ray2D, ...]
    expected_events: tuple[tuple[int, TargetId], ...]  # (1-based frame, target) STABLE events
    allowed_states: frozenset[SelectionState]
    required_states: frozenset[SelectionState] = frozenset()


@dataclass(frozen=True, slots=True)
class FrameRecord:
    index: int  # 1-based
    result: SelectionResult
    streak: int
    latched: bool


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    scenario: Scenario
    frames: tuple[FrameRecord, ...]

    @property
    def events(self) -> tuple[tuple[int, TargetId], ...]:
        return tuple(
            (f.index, f.result.stable_target) for f in self.frames if f.result.stable_target is not None
        )

    @property
    def failures(self) -> list[str]:
        problems: list[str] = []
        if self.events != self.scenario.expected_events:
            problems.append(
                f"expected STABLE events {format_events(self.scenario.expected_events)}, got {format_events(self.events)}"
            )
        for f in self.frames:
            if f.result.state not in self.scenario.allowed_states:
                problems.append(f"unexpected state {f.result.state.name} at frame {f.index}: {f.result.reason}")
                break
        seen = {f.result.state for f in self.frames}
        for state in sorted(self.scenario.required_states - seen, key=lambda s: s.value):
            problems.append(f"expected at least one {state.name} frame")
        return problems

    @property
    def passed(self) -> bool:
        return not self.failures


def format_events(events: tuple[tuple[int, TargetId], ...]) -> str:
    return "[" + ", ".join(f"{tid.name}@{index}" for index, tid in events) + "]"


# --- synthetic rays ---------------------------------------------------------------------


def _unit(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    if length == 0.0:
        raise ValueError("cannot aim a ray at its own origin")
    return dx / length, dy / length


def _ray(origin: tuple[float, float], dx: float, dy: float, length: float = FINGER_LENGTH) -> Ray2D:
    ux, uy = _unit(dx, dy)
    return Ray2D(Point2D(*origin), Point2D(origin[0] + length * ux, origin[1] + length * uy))


def clear_ray(target: Target) -> Ray2D:
    """A hand below (or above) the target pointing at it, missing by AIM_ERROR."""
    cx, cy = target.center.x, target.center.y
    hand_y = cy + HAND_DISTANCE if cy + HAND_DISTANCE <= 0.97 else cy - HAND_DISTANCE
    hand = (0.5 + (cx - 0.5) * 0.4, hand_y)
    ux, uy = _unit(cx - hand[0], cy - hand[1])
    aim = (cx - uy * AIM_ERROR, cy + ux * AIM_ERROR)  # offset perpendicular to the ray
    return _ray(hand, aim[0] - hand[0], aim[1] - hand[1])


def ambiguous_ray(a: Target, b: Target) -> Ray2D:
    """A ray through the midpoint of a and b, at an angle that keeps both equally close."""
    mx, my = (a.center.x + b.center.x) / 2, (a.center.y + b.center.y) / 2
    ux, uy = _unit(b.center.x - a.center.x, b.center.y - a.center.y)
    half = math.hypot(b.center.x - a.center.x, b.center.y - a.center.y) / 2
    nx, ny = -uy, ux  # normal toward the user (down the image)
    if ny < 0 or (ny == 0 and nx < 0):
        nx, ny = -nx, -ny
    sin_t = min(0.6, 0.06 / half)  # both targets end up ~0.06 from the ray
    cos_t = math.sqrt(1.0 - sin_t * sin_t)
    dx, dy = cos_t * ux - sin_t * nx, cos_t * uy - sin_t * ny
    return _ray((mx - 0.25 * dx, my - 0.25 * dy), dx, dy)


def no_target_rays(config: TargetConfig) -> tuple[Ray2D, ...]:
    """Degenerate, too-short, and pointing-away rays (4 each, then 4 more away)."""
    degenerate = Ray2D(Point2D(0.5, 0.9), Point2D(0.5, 0.9))
    too_short = Ray2D(Point2D(0.5, 0.9), Point2D(0.5 + config.min_ray_length / 2, 0.9))
    # Hand at the image edge farthest from the targets, pointing out of the image.
    cx = sum(t.center.x for t in config.targets) / len(config.targets)
    cy = sum(t.center.y for t in config.targets) / len(config.targets)
    edges = [((0.5, 0.02), (0.0, -1.0)), ((0.5, 0.98), (0.0, 1.0)), ((0.02, 0.5), (-1.0, 0.0)), ((0.98, 0.5), (1.0, 0.0))]
    hand, (ox, oy) = max(edges, key=lambda e: math.hypot(e[0][0] - cx, e[0][1] - cy))
    away = _ray(hand, ox, oy, length=0.015)
    return (degenerate,) * 4 + (too_short,) * 4 + (away,) * 8


# --- scenarios -------------------------------------------------------------------------------


def build_scenarios(config: TargetConfig) -> dict[str, Scenario]:
    n = config.stable_frames
    pointing = frozenset({_S.CANDIDATE, _S.STABLE})
    away = no_target_rays(config)[-1]
    scenarios: dict[str, Scenario] = {}

    for name, tid in _TARGET_FOR.items():
        ray = clear_ray(config.target(tid))
        scenarios[name] = Scenario(
            name, f"clear ray at {tid.name} for {n + HOLD_FRAMES} frames: STABLE once, at frame {n}",
            (ray,) * (n + HOLD_FRAMES), ((n, tid),), pointing,
        )

    blue, yellow = config.target(TargetId.BLUE_BLOCK), config.target(TargetId.YELLOW_BLOCK)
    scenarios["ambiguous"] = Scenario(
        "ambiguous", "ray between BLUE_BLOCK and YELLOW_BLOCK: always AMBIGUOUS, never STABLE",
        (ambiguous_ray(blue, yellow),) * (n + HOLD_FRAMES), (), frozenset({_S.AMBIGUOUS}), frozenset({_S.AMBIGUOUS}),
    )
    scenarios["none"] = Scenario(
        "none", "degenerate, too-short and pointing-away rays: always NONE, never STABLE",
        no_target_rays(config), (), frozenset({_S.NONE}), frozenset({_S.NONE}),
    )

    blue_ray = clear_ray(blue)
    first = max(1, n - 4)  # shorter than a full streak (when n > 1)
    second_start = first + 3
    events = (((n, blue.id),) if first >= n else ()) + ((second_start + n, blue.id),)
    scenarios["reacquire"] = Scenario(
        "reacquire",
        f"BLUE_BLOCK for {first} frames, no target for 3, BLUE_BLOCK again: "
        f"needs a fresh {n}-frame streak (STABLE at frame {second_start + n})",
        (blue_ray,) * first + (away,) * 3 + (blue_ray,) * (n + 2),
        events, pointing | {_S.NONE}, frozenset({_S.NONE}),
    )

    held = n + HOLD_FRAMES
    scenarios["release"] = Scenario(
        "release",
        f"BLUE_BLOCK held {held} frames (STABLE once), released for 2, pointed again: STABLE again",
        (blue_ray,) * held + (away,) * 2 + (blue_ray,) * (n + 2),
        ((n, blue.id), (held + 2 + n, blue.id)), pointing | {_S.NONE},
    )
    assert tuple(scenarios) == SCENARIO_NAMES
    return scenarios


def run_scenario(scenario: Scenario, config: TargetConfig) -> ScenarioRun:
    smoother = TemporalSmoother(config.stable_frames)
    frames = []
    for index, ray in enumerate(scenario.rays, start=1):
        result = smoother.update(select_target(ray, config))
        frames.append(FrameRecord(index, result, smoother.streak, smoother.latched))
    return ScenarioRun(scenario, tuple(frames))


def format_frame(record: FrameRecord, width: int = 2, verbose: bool = False) -> str:
    result = record.result
    target = result.target_id.name if result.target_id is not None else "-"
    line = (
        f"frame={record.index:0{width}d} state={result.state.name} target={target} "
        f"confidence={result.confidence:.2f} streak={record.streak}"
    )
    if record.latched and result.state is _S.CANDIDATE:
        line += " (held)"
    if verbose:
        line += f"  | {result.reason}"
    return line
