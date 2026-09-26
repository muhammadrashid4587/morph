"""Temporal smoothing: turns per-frame selections into one STABLE event per gesture.

Rules:
- A CANDIDATE for the same target on N consecutive frames (N = stable_frames)
  becomes STABLE on frame N.
- STABLE is emitted exactly once per sustained gesture. While the user keeps
  pointing, later frames report CANDIDATE again (the smoother is "latched").
- NONE, AMBIGUOUS, a different candidate target, or reset() ends the gesture:
  the streak restarts and the latch is released, so the same target can
  become STABLE again after a fresh N-frame streak.
"""

from __future__ import annotations

import dataclasses

from .config import DEFAULT_STABLE_FRAMES
from .models import SelectionResult, SelectionState, TargetId


class TemporalSmoother:
    def __init__(self, stable_frames: int = DEFAULT_STABLE_FRAMES) -> None:
        if isinstance(stable_frames, bool) or not isinstance(stable_frames, int) or stable_frames < 1:
            raise ValueError(f"stable_frames must be a positive integer, got {stable_frames!r}")
        self.stable_frames = stable_frames
        self._target: TargetId | None = None
        self._streak = 0
        self._latched = False

    # Debug view -------------------------------------------------------------------

    @property
    def streak(self) -> int:
        """Consecutive clear frames of the current candidate (0 when none)."""
        return self._streak

    @property
    def current_target(self) -> TargetId | None:
        return self._target

    @property
    def latched(self) -> bool:
        """True after STABLE was emitted, until the gesture is released."""
        return self._latched

    # Updates -----------------------------------------------------------------------

    def reset(self) -> None:
        """Release: forget the streak and re-arm the STABLE event."""
        self._target = None
        self._streak = 0
        self._latched = False

    def update(self, frame: SelectionResult) -> SelectionResult:
        """Feed one per-frame result from select_target(); returns the smoothed result."""
        if not isinstance(frame, SelectionResult):
            raise ValueError("update() expects a SelectionResult")
        if frame.state is SelectionState.STABLE:
            raise ValueError("update() expects a per-frame result from select_target(), not STABLE")
        if frame.state is not SelectionState.CANDIDATE:
            self.reset()  # NONE or AMBIGUOUS ends the gesture
            return frame

        assert frame.best_candidate is not None  # guaranteed by SelectionResult validation
        target = frame.best_candidate.target.id
        if target != self._target:
            self.reset()
            self._target = target
        self._streak += 1

        n = self.stable_frames
        if self._latched:
            return dataclasses.replace(
                frame, reason=f"holding {target.name}: STABLE already emitted; release to select again"
            )
        if self._streak >= n:
            self._latched = True
            return dataclasses.replace(
                frame,
                state=SelectionState.STABLE,
                stable_target=target,
                reason=f"{target.name} held for {n} consecutive clear frames",
            )
        return dataclasses.replace(frame, reason=f"{frame.reason} (streak {self._streak}/{n})")
