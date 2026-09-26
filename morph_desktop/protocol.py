"""MORPH wire protocol: strict, typed validation of incoming action messages.

Framing: newline-delimited JSON, carried over WebSocket. Every line is one
JSON object. A WebSocket frame normally carries one line; a frame carrying
several newline-separated objects is handled as several messages. Every
response is one compact JSON object followed by "\\n".

Anything that is not exactly a known, well-formed action raises
ProtocolError; callers turn that into an error response, never a crash.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from .config import MAX_MESSAGE_BYTES, MAX_TOKEN_LENGTH
from .context import Context

MouseButton = Literal["left", "right"]
MOUSE_BUTTONS: tuple[MouseButton, ...] = ("left", "right")

VOLUME_MIN, VOLUME_MAX = 0, 100
VOLUME_DELTA_MIN, VOLUME_DELTA_MAX = -10, 10


class ProtocolError(ValueError):
    """An incoming message is malformed or not allowed."""


# --- Actions -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Authenticate:
    token: str = field(repr=False)  # secret: never shown in repr/logs
    name: ClassVar[str] = "authenticate"


@dataclass(frozen=True, slots=True)
class Ping:
    name: ClassVar[str] = "ping"


@dataclass(frozen=True, slots=True)
class SetContext:
    context: Context
    name: ClassVar[str] = "set_context"


@dataclass(frozen=True, slots=True)
class NextSlide:
    name: ClassVar[str] = "next_slide"


@dataclass(frozen=True, slots=True)
class PreviousSlide:
    name: ClassVar[str] = "previous_slide"


@dataclass(frozen=True, slots=True)
class SetVolume:
    value: int
    name: ClassVar[str] = "set_volume"


@dataclass(frozen=True, slots=True)
class VolumeDelta:
    delta: int
    name: ClassVar[str] = "volume_delta"


@dataclass(frozen=True, slots=True)
class PlayPause:
    name: ClassVar[str] = "play_pause"


@dataclass(frozen=True, slots=True)
class MouseMove:
    x: float
    y: float
    name: ClassVar[str] = "mouse_move"


@dataclass(frozen=True, slots=True)
class Click:
    button: MouseButton
    name: ClassVar[str] = "click"


# Actions that touch the desktop (handled by an ActionExecutor).
DesktopAction = NextSlide | PreviousSlide | SetVolume | VolumeDelta | PlayPause | MouseMove | Click
# Everything a client may send.
Action = Authenticate | Ping | SetContext | DesktopAction


# --- Field validators ----------------------------------------------------------


def _field(msg: dict[str, Any], action: str, field: str) -> Any:
    if field not in msg:
        raise ProtocolError(f"'{action}' requires field '{field}'")
    return msg[field]


def _int_in_range(msg: dict[str, Any], action: str, field: str, lo: int, hi: int) -> int:
    value = _field(msg, action, field)
    # bool is a subclass of int in Python; true/false are not numbers here.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"'{action}.{field}' must be an integer, got {_json_type(value)}")
    if not lo <= value <= hi:
        raise ProtocolError(f"'{action}.{field}' must be between {lo} and {hi}, got {value}")
    return value


def _unit_float(msg: dict[str, Any], action: str, field: str) -> float:
    value = _field(msg, action, field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProtocolError(f"'{action}.{field}' must be a number, got {_json_type(value)}")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ProtocolError(f"'{action}.{field}' must be between 0 and 1 (normalized), got {value}")
    return float(value)


def _choice(msg: dict[str, Any], action: str, field: str, allowed: tuple[str, ...]) -> str:
    value = _field(msg, action, field)
    if not isinstance(value, str) or value not in allowed:
        raise ProtocolError(f"'{action}.{field}' must be one of {list(allowed)}, got {value!r}")
    return value


def _token(msg: dict[str, Any]) -> str:
    # Error messages here must never echo the value: it may be a secret.
    value = _field(msg, "authenticate", "token")
    if not isinstance(value, str) or not value:
        raise ProtocolError("'authenticate.token' must be a non-empty string")
    if len(value) > MAX_TOKEN_LENGTH:
        raise ProtocolError(f"'authenticate.token' must be at most {MAX_TOKEN_LENGTH} characters")
    return value


def _json_type(value: Any) -> str:
    return {
        bool: "boolean", int: "integer", float: "number", str: "string",
        list: "array", dict: "object", type(None): "null",
    }.get(type(value), type(value).__name__)


# --- Action table ------------------------------------------------------------------

_CONTEXTS = tuple(c.value for c in Context)

# action name -> (allowed fields besides "action", builder)
_PARSERS: dict[str, tuple[frozenset[str], Callable[[dict[str, Any]], Action]]] = {
    "authenticate": (frozenset({"token"}), lambda m: Authenticate(_token(m))),
    "ping": (frozenset(), lambda m: Ping()),
    "next_slide": (frozenset(), lambda m: NextSlide()),
    "previous_slide": (frozenset(), lambda m: PreviousSlide()),
    "play_pause": (frozenset(), lambda m: PlayPause()),
    "set_volume": (
        frozenset({"value"}),
        lambda m: SetVolume(_int_in_range(m, "set_volume", "value", VOLUME_MIN, VOLUME_MAX)),
    ),
    "volume_delta": (
        frozenset({"delta"}),
        lambda m: VolumeDelta(
            _int_in_range(m, "volume_delta", "delta", VOLUME_DELTA_MIN, VOLUME_DELTA_MAX)
        ),
    ),
    "mouse_move": (
        frozenset({"x", "y"}),
        lambda m: MouseMove(_unit_float(m, "mouse_move", "x"), _unit_float(m, "mouse_move", "y")),
    ),
    "click": (
        frozenset({"button"}),
        lambda m: Click(_choice(m, "click", "button", MOUSE_BUTTONS)),
    ),
    "set_context": (
        frozenset({"context"}),
        lambda m: SetContext(Context(_choice(m, "set_context", "context", _CONTEXTS))),
    ),
}

ACTION_NAMES: tuple[str, ...] = tuple(_PARSERS)


# --- Parsing ---------------------------------------------------------------------


def _reject_constant(token: str) -> Any:
    raise ProtocolError(f"invalid JSON number: {token}")


def decode_object(raw: str | bytes) -> dict[str, Any]:
    """Decode one JSON line into a dict, or raise ProtocolError."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ProtocolError(f"message too large (max {MAX_MESSAGE_BYTES} bytes)")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ProtocolError("message is not valid UTF-8") from None
    if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"message too large (max {MAX_MESSAGE_BYTES} bytes)")
    if not raw.strip():
        raise ProtocolError("empty message")
    try:
        obj = json.loads(raw, parse_constant=_reject_constant)
    except ProtocolError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ProtocolError(f"malformed JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise ProtocolError(f"message must be a JSON object, got {_json_type(obj)}")
    return obj


def parse_action(msg: dict[str, Any]) -> Action:
    """Validate a decoded message and build its typed action."""
    name = msg.get("action")
    if name is None:
        raise ProtocolError("missing 'action' field")
    if not isinstance(name, str):
        raise ProtocolError(f"'action' must be a string, got {_json_type(name)}")
    entry = _PARSERS.get(name)
    if entry is None:
        raise ProtocolError(f"unknown action {name!r}; expected one of {list(ACTION_NAMES)}")
    allowed, build = entry
    unexpected = sorted(set(msg) - allowed - {"action"})
    if unexpected:
        raise ProtocolError(f"unexpected field(s) for '{name}': {unexpected}")
    return build(msg)


def parse_message(raw: str | bytes) -> Action:
    """Decode and validate one JSON line."""
    return parse_action(decode_object(raw))


def split_lines(frame: str | bytes) -> list[str | bytes]:
    """Split a frame into its non-blank newline-delimited lines."""
    sep: Any = b"\n" if isinstance(frame, bytes) else "\n"
    return [line for line in frame.split(sep) if line.strip()]


# --- Responses ---------------------------------------------------------------------

Response = dict[str, Any]


def ok(action: str) -> Response:
    return {"status": "ok", "action": action}


def error(message: str) -> Response:
    return {"status": "error", "message": message}


def context_response(context: Context) -> Response:
    return {"status": "context", "context": context.value}


def pong(context: Context) -> Response:
    # Includes the current context so a client can sync state with one ping.
    return {"status": "pong", "context": context.value}


def encode(response: Response) -> str:
    """Serialize a response as one newline-terminated JSON line."""
    return json.dumps(response, separators=(",", ":")) + "\n"
