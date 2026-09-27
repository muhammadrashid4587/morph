"""Keyword fallback: used when there is no GEMINI_API_KEY or the Gemini API fails.

Whole-word matching on the transcript. If words for more than one different
action are heard (e.g. "next ... louder"), it is ambiguous and becomes "none".
"""

from __future__ import annotations

import re

from .actions import AgentAction, none_action

# (action, mode, reply, phrases). Phrases are matched as whole words, case-insensitive.
RULES: tuple[tuple[str, str | None, str, tuple[str, ...]], ...] = (
    ("next_slide", None, "Next slide.", ("next", "forward")),
    ("prev_slide", None, "Previous slide.", ("back", "previous", "go back")),
    ("volume_up", None, "Volume up.", ("louder", "volume up", "turn it up")),
    ("volume_down", None, "Volume down.", ("quieter", "softer", "volume down", "turn it down")),
    ("set_mode", "presentation", "Presentation mode.", ("presentation", "slides mode")),
    ("set_mode", "music", "Music mode.", ("music",)),
    ("set_mode", "robot_targeting", "Targeting mode.", ("targeting", "robot mode", "target mode")),
)
NOT_CAUGHT = "Sorry, I didn't catch that."


def keyword_action(transcript: str) -> AgentAction:
    text = " ".join(re.findall(r"[a-z]+", transcript.lower()))
    matches = {
        (action, mode, reply)
        for action, mode, reply, phrases in RULES
        if any(re.search(rf"\b{re.escape(p)}\b", text) for p in phrases)
    }
    if len(matches) != 1:
        return none_action(NOT_CAUGHT)  # nothing heard, or ambiguous
    action, mode, reply = matches.pop()
    return AgentAction(action, mode, reply)
