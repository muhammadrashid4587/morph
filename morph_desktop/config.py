"""Runtime settings, read from environment variables.

Environment variables:
    MORPH_HOST          Interface to bind (default 127.0.0.1, localhost only).
    MORPH_PORT          TCP port (default 8765).
    MORPH_REAL_ACTIONS  Set to exactly "1" to allow real mouse/keyboard control.
    MORPH_LOG_LEVEL     Logging level name (default INFO).
    MORPH_AUTH_TOKEN    Optional shared secret. When set, every connection must
                        send {"action":"authenticate","token":...} first.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Largest accepted WebSocket frame. Protocol messages are tiny; anything
# bigger is either a bug or abuse and the connection is closed.
MAX_MESSAGE_BYTES = 4096

# Longest accepted MORPH_AUTH_TOKEN / authenticate.token.
MAX_TOKEN_LENGTH = 256

# Pause pyautogui inserts after every real action.
REAL_ACTION_PAUSE_S = 0.2

# Percentage points of volume represented by one media-key press.
VOLUME_STEP_PER_KEYPRESS = 5

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    real_actions: bool = False
    log_level: str = "INFO"
    auth_token: str | None = field(default=None, repr=False)  # secret: keep out of repr/logs

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK_HOSTS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        port_text = env.get("MORPH_PORT", str(DEFAULT_PORT))
        try:
            port = int(port_text)
        except ValueError:
            raise ValueError(f"MORPH_PORT must be an integer, got {port_text!r}") from None
        if not 0 <= port <= 65535:
            raise ValueError(f"MORPH_PORT must be 0-65535, got {port}")
        return cls(
            host=env.get("MORPH_HOST", DEFAULT_HOST),
            port=port,
            real_actions=env.get("MORPH_REAL_ACTIONS", "") == "1",
            log_level=env.get("MORPH_LOG_LEVEL", "INFO").upper(),
            auth_token=_auth_token(env.get("MORPH_AUTH_TOKEN")),
        )


def _auth_token(raw: str | None) -> str | None:
    # Error messages must not include the value.
    if raw is None:
        return None
    if not raw.strip():
        # Probably an unexpanded variable; don't silently run unprotected.
        raise ValueError("MORPH_AUTH_TOKEN is set but empty; unset it to disable auth")
    if raw != raw.strip():
        raise ValueError("MORPH_AUTH_TOKEN must not start or end with whitespace")
    if len(raw) > MAX_TOKEN_LENGTH:
        raise ValueError(f"MORPH_AUTH_TOKEN must be at most {MAX_TOKEN_LENGTH} characters")
    return raw


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # The websockets library is chatty at INFO; keep its warnings and errors.
    logging.getLogger("websockets").setLevel(logging.WARNING)
