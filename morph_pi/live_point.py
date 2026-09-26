"""Live POINT pipeline: hand landmarks -> ray -> select_target() -> TemporalSmoother.

Pure logic (no cv2 or mediapipe), so it is testable without a camera.
Create ONE PointPipeline per camera session: its TemporalSmoother persists
across frames, which is what lets the 12-frame streak build up.

Coordinates are camera-space (unmirrored), the same space as the target
config. Mirroring is a display concern only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .hand_tracking import ray_from_landmarks
from .models import Ray2D, SelectionResult, SelectionState, TargetConfig, TargetId
from .selection import select_target
from .smoothing import TemporalSmoother


def short_name(target_id: TargetId) -> str:
    """BLUE_BLOCK -> BLUE."""
    return target_id.name.removesuffix("_BLOCK")


@dataclass(frozen=True, slots=True)
class PointFrame:
    """What the pipeline concluded for one camera frame."""

    hand_detected: bool
    ray: Ray2D | None
    result: SelectionResult | None  # smoothed result; None when there is no hand or no ray
    streak: int
    latched: bool
    stable_frames: int

    @property
    def state(self) -> SelectionState:
        return SelectionState.NONE if self.result is None else self.result.state

    @property
    def just_locked(self) -> bool:
        """True only on the single frame where STABLE is emitted."""
        return self.state is SelectionState.STABLE

    @property
    def locked(self) -> bool:
        """STABLE was emitted for this gesture and the user is still pointing at that target."""
        return self.just_locked or (self.latched and self.state is SelectionState.CANDIDATE)

    @property
    def status(self) -> str:
        if not self.hand_detected:
            return "NO HAND"
        if self.ray is None:
            return "NO RAY"
        if self.locked:
            return "LOCKED"
        return {
            SelectionState.NONE: "NO TARGET",
            SelectionState.AMBIGUOUS: "AMBIGUOUS",
            SelectionState.CANDIDATE: "CANDIDATE",
        }[self.state]

    def summary(self) -> str:
        """e.g. state=CANDIDATE target=BLUE confidence=0.78 frames=5/12"""
        result = self.result
        target = short_name(result.target_id) if result is not None and result.target_id is not None else "-"
        confidence = result.confidence if result is not None else 0.0
        line = (
            f"state={self.state.name} target={target} confidence={confidence:.2f} "
            f"frames={min(self.streak, self.stable_frames)}/{self.stable_frames}"
        )
        if not self.hand_detected:
            return line + " (no hand)"
        if self.ray is None:
            return line + " (no ray)"
        if self.locked and not self.just_locked:
            return line + " (locked)"
        return line


class PointPipeline:
    """Per-session POINT state. The smoother is created once and reused for every frame."""

    def __init__(self, config: TargetConfig | None = None) -> None:
        self.config = config if config is not None else TargetConfig()
        self.smoother = TemporalSmoother(self.config.stable_frames)

    def process(self, landmarks: Sequence[object] | None) -> PointFrame:
        """Feed one frame's hand landmarks (camera space); None or empty means no hand."""
        if not landmarks:
            self.smoother.reset()
            return self._frame(hand_detected=False, ray=None, result=None)
        ray = ray_from_landmarks(landmarks, self.config.min_ray_length)
        if ray is None:
            self.smoother.reset()
            return self._frame(hand_detected=True, ray=None, result=None)
        result = self.smoother.update(select_target(ray, self.config))
        return self._frame(hand_detected=True, ray=ray, result=result)

    def _frame(self, hand_detected: bool, ray: Ray2D | None, result: SelectionResult | None) -> PointFrame:
        return PointFrame(
            hand_detected, ray, result, self.smoother.streak, self.smoother.latched, self.config.stable_frames
        )
