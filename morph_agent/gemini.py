"""Minimal Gemini API client (standard library only): one generateContent call with JSON output.

POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
The API key travels only in the `x-goog-api-key` header (never in the URL), and
never appears in errors or reprs. The HTTP transport is injectable for tests.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

API_URL = "https://generativelanguage.googleapis.com/v1beta"

# (request, timeout_s) -> (HTTP status, response body)
Transport = Callable[[urllib.request.Request, float], tuple[int, bytes]]


class GeminiError(RuntimeError):
    """A Gemini request failed. The message never contains the API key."""


class GeminiRateLimited(GeminiError):
    """HTTP 429 / RESOURCE_EXHAUSTED: a (free-tier) rate limit or quota was hit."""


def urllib_transport(request: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GeminiError(f"cannot reach Gemini: {getattr(exc, 'reason', exc)}") from None


class Gemini:
    def __init__(self, api_key: str, transport: Transport = urllib_transport, timeout: float = 20.0) -> None:
        if not api_key:
            raise GeminiError("a Gemini API key is required")
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def __repr__(self) -> str:
        return "Gemini(api_key=<hidden>)"

    def generate_json(self, model: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """The raw generateContent response for a JSON-only answer that follows `schema`."""
        path = f"/models/{urllib.parse.quote(model, safe='-._')}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "temperature": 0},
        }
        request = urllib.request.Request(
            API_URL + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
        )
        status, data = self._transport(request, self._timeout)
        if status == 429 or (status >= 400 and b"RESOURCE_EXHAUSTED" in data[:2000]):
            raise GeminiRateLimited(f"Gemini rate limit reached (HTTP {status})")
        if not 200 <= status < 300:
            raise GeminiError(f"Gemini request failed: HTTP {status}: {_error_detail(data)}")
        try:
            return json.loads(data)
        except ValueError:
            raise GeminiError("Gemini returned a response that is not JSON") from None


def response_text(response: dict[str, Any]) -> str | None:
    """The model's text, or None when the prompt or answer was blocked / missing."""
    if (response.get("promptFeedback") or {}).get("blockReason"):
        return None
    candidates = response.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return None
    candidate = candidates[0]
    if candidate.get("finishReason") not in (None, "STOP"):  # SAFETY, MAX_TOKENS, RECITATION, ...
        return None
    parts = (candidate.get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))


def _error_detail(data: bytes) -> str:
    try:
        error = json.loads(data).get("error") or {}
        return f"{error.get('status', '')} {error.get('message', '')}".strip()[:200] or data[:200].decode("utf-8", "replace")
    except (ValueError, AttributeError):
        return data[:200].decode("utf-8", "replace")
