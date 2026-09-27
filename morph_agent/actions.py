"""The fixed action allowlist, validation, and the mapping to laptop messages.

This module is the safety boundary: whatever the model returns is validated
here, and only these actions can ever be executed. There is deliberately no
action for servos, the arm, the Nano, shell commands or arbitrary messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ACTIONS: tuple[str, ...] = ("set_mode", "next_slide", "prev_slide", "volume_up", "volume_down", "say", "none")
MODES: tuple[str, ...] = ("robot_targeting", "presentation", "music")
TARGETS: tuple[str, ...] = ("blue", "yellow", "green")
VOLUME_STEP = 10  # laptop volume_delta allows -10..10
MAX_REPLY_CHARS = 200
FALLBACK_REPLY = "Sorry, I can't do that."


@dataclass(frozen=True, slots=True)
class MorphState:
    mode: str = "robot_targeting"
    locked_target: str | None = None  # "blue" / "yellow" / "green", or None

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.locked_target is not None and self.locked_target not in TARGETS:
            raise ValueError(f"locked_target must be one of {TARGETS} or None, got {self.locked_target!r}")


@dataclass(frozen=True, slots=True)
class AgentAction:
    action: str
    mode: str | None
    reply: str


def none_action(reply: str = FALLBACK_REPLY) -> AgentAction:
    return AgentAction("none", None, reply)


def validate(raw: Any) -> AgentAction:
    """Turn untrusted model output into an allowed action. Never raises: anything invalid is 'none'."""
    if not isinstance(raw, dict):
        return none_action()
    action, mode, reply = raw.get("action"), raw.get("mode"), raw.get("reply")
    if action not in ACTIONS:
        return none_action()
    if not isinstance(reply, str):
        reply = ""
    reply = " ".join(reply.split())[:MAX_REPLY_CHARS]
    if action == "set_mode":
        if mode not in MODES:
            return none_action()
        return AgentAction(action, mode, reply or f"{mode.replace('_', ' ').capitalize()} mode.")
    if action in ("say", "none") and not reply:
        return AgentAction(action, None, FALLBACK_REPLY if action == "none" else "Okay.")
    return AgentAction(action, None, reply or "Okay.")


def laptop_message(action: AgentAction) -> dict[str, Any] | None:
    """The morph_desktop WebSocket message for an action, or None if it only speaks."""
    if action.action == "next_slide":
        return {"action": "next_slide"}
    if action.action == "prev_slide":
        return {"action": "previous_slide"}
    if action.action == "volume_up":
        return {"action": "volume_delta", "delta": VOLUME_STEP}
    if action.action == "volume_down":
        return {"action": "volume_delta", "delta": -VOLUME_STEP}
    if action.action == "set_mode" and action.mode in MODES:
        return {"action": "set_context", "context": action.mode}
    return None  # say / none: speech only


def apply(action: AgentAction, state: MorphState) -> MorphState:
    """MORPH's in-memory state after an action (only set_mode changes it)."""
    if action.action == "set_mode" and action.mode in MODES:
        return MorphState(action.mode, state.locked_target)
    return state
