"""Interaction contexts: what MORPH's physical controls currently mean.

The same dial does different things depending on the context. Context is
set explicitly by incoming `set_context` messages; there is no active-app
detection yet.
"""

from __future__ import annotations

import logging
from enum import StrEnum

logger = logging.getLogger("morph.context")


class Context(StrEnum):
    MUSIC = "music"
    PRESENTATION = "presentation"
    ROBOT_TARGETING = "robot_targeting"
    TEACH = "teach"


# What the physical dial means in each context (used for logs and, later,
# for the LCD/LED feedback on the robot).
DIAL_ROLES: dict[Context, str] = {
    Context.MUSIC: "volume",
    Context.PRESENTATION: "next/previous slide",
    Context.ROBOT_TARGETING: "select target / confirm",
    Context.TEACH: "record/map a gesture",
}

DEFAULT_CONTEXT = Context.MUSIC


class ContextState:
    """Holds the current context in memory."""

    def __init__(self, initial: Context = DEFAULT_CONTEXT) -> None:
        self._current = initial

    @property
    def current(self) -> Context:
        return self._current

    def set(self, context: Context) -> bool:
        """Switch context. Returns True if it actually changed."""
        previous = self._current
        if context == previous:
            logger.info("context unchanged: %s", context.value)
            return False
        self._current = context
        logger.info(
            "CONTEXT %s -> %s (dial now controls: %s)",
            previous.value,
            context.value,
            DIAL_ROLES[context],
        )
        return True
