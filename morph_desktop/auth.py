"""Optional shared-secret authorization, per WebSocket connection.

When MORPH_AUTH_TOKEN is set, each connection must send
    {"action": "authenticate", "token": "..."}
before anything else. When it is unset, every connection is authorized
immediately (localhost development behavior).

The secret must never appear in logs or error messages: log text goes
through redact() first.
"""

from __future__ import annotations

import hmac
import re
import time
from dataclasses import dataclass, field

# Failed authenticate attempts before the server closes the connection.
MAX_FAILED_AUTH_ATTEMPTS = 3
MIN_RECOMMENDED_TOKEN_LENGTH = 16

NOT_AUTHENTICATED = "not authenticated"
UNAUTHENTICATED_MESSAGE = f"{NOT_AUTHENTICATED}: send an 'authenticate' action with the token first"
REDACTED = "<redacted>"

# The value of any "token" field, including an unterminated string in malformed JSON.
_TOKEN_FIELD = re.compile(r'("token"\s*:\s*)"(?:[^"\\]|\\.?)*(?:"|$)')


def redact(text: str, secret: str | None = None) -> str:
    """Hide "token" field values and any literal occurrence of the secret."""
    text = _TOKEN_FIELD.sub(lambda m: f'{m.group(1)}"{REDACTED}"', text)
    if secret:
        text = text.replace(secret, REDACTED)
    return text


def tokens_match(candidate: str, secret: str) -> bool:
    """Constant-time comparison, so response timing does not leak the secret."""
    return hmac.compare_digest(candidate.encode("utf-8"), secret.encode("utf-8"))


@dataclass(slots=True)
class ClientSession:
    """Per-connection state and metadata. Holds no secrets."""

    name: str
    auth_required: bool
    authorized: bool
    user_agent: str | None = None
    connected_at: float = field(default_factory=time.monotonic)
    received: int = 0
    rejected: int = 0
    failed_auth: int = 0

    @property
    def auth_state(self) -> str:
        if not self.auth_required:
            return "open"
        return "authorized" if self.authorized else "pending"

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.connected_at
