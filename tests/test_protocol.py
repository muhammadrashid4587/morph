import json

import pytest

from morph_desktop.context import Context
from morph_desktop.protocol import (
    Click,
    MouseMove,
    NextSlide,
    Ping,
    PlayPause,
    PreviousSlide,
    ProtocolError,
    SetContext,
    SetVolume,
    VolumeDelta,
    context_response,
    encode,
    error,
    ok,
    parse_message,
    pong,
    split_lines,
)


# --- valid messages ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"action":"ping"}', Ping()),
        ('{"action":"next_slide"}', NextSlide()),
        ('{"action":"previous_slide"}', PreviousSlide()),
        ('{"action":"play_pause"}', PlayPause()),
        ('{"action":"set_volume","value":60}', SetVolume(60)),
        ('{"action":"volume_delta","delta":-10}', VolumeDelta(-10)),
        ('{"action":"mouse_move","x":0.75,"y":0.5}', MouseMove(0.75, 0.5)),
        ('{"action":"click","button":"left"}', Click("left")),
        ('{"action":"click","button":"right"}', Click("right")),
        ('{"action":"set_context","context":"presentation"}', SetContext(Context.PRESENTATION)),
        ('{"action":"set_context","context":"robot_targeting"}', SetContext(Context.ROBOT_TARGETING)),
    ],
)
def test_valid_messages(raw: str, expected: object) -> None:
    assert parse_message(raw) == expected


def test_accepts_bytes_and_trailing_newline() -> None:
    assert parse_message(b'{"action":"ping"}\n') == Ping()


def test_mouse_move_accepts_integer_edges() -> None:
    assert parse_message('{"action":"mouse_move","x":0,"y":1}') == MouseMove(0.0, 1.0)


# --- volume bounds -----------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 50, 99, 100])
def test_set_volume_in_range(value: int) -> None:
    assert parse_message(json.dumps({"action": "set_volume", "value": value})) == SetVolume(value)


@pytest.mark.parametrize("value", [-1, 101, 1000, -100])
def test_set_volume_out_of_range(value: int) -> None:
    with pytest.raises(ProtocolError, match="between 0 and 100"):
        parse_message(json.dumps({"action": "set_volume", "value": value}))


@pytest.mark.parametrize("delta", [-10, -1, 0, 1, 10])
def test_volume_delta_in_range(delta: int) -> None:
    assert parse_message(json.dumps({"action": "volume_delta", "delta": delta})) == VolumeDelta(delta)


@pytest.mark.parametrize("delta", [-11, 11, 100])
def test_volume_delta_out_of_range(delta: int) -> None:
    with pytest.raises(ProtocolError, match="between -10 and 10"):
        parse_message(json.dumps({"action": "volume_delta", "delta": delta}))


# --- invalid types and missing fields ------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        {"action": "set_volume", "value": "60"},
        {"action": "set_volume", "value": 60.5},
        {"action": "set_volume", "value": True},  # bool is not a number here
        {"action": "set_volume", "value": None},
        {"action": "set_volume", "value": [60]},
        {"action": "volume_delta", "delta": 1.5},
        {"action": "volume_delta", "delta": False},
    ],
)
def test_rejects_invalid_number_types(msg: dict) -> None:
    with pytest.raises(ProtocolError, match="must be an integer"):
        parse_message(json.dumps(msg))


@pytest.mark.parametrize(
    ("msg", "field"),
    [
        ({"action": "set_volume"}, "value"),
        ({"action": "volume_delta"}, "delta"),
        ({"action": "mouse_move", "x": 0.5}, "y"),
        ({"action": "mouse_move", "y": 0.5}, "x"),
        ({"action": "click"}, "button"),
        ({"action": "set_context"}, "context"),
    ],
)
def test_rejects_missing_fields(msg: dict, field: str) -> None:
    with pytest.raises(ProtocolError, match=f"requires field '{field}'"):
        parse_message(json.dumps(msg))


def test_rejects_unexpected_fields() -> None:
    with pytest.raises(ProtocolError, match="unexpected field"):
        parse_message('{"action":"next_slide","value":1}')
    with pytest.raises(ProtocolError, match="unexpected field"):
        parse_message('{"action":"set_volume","value":10,"force":true}')


@pytest.mark.parametrize(
    "msg",
    [
        {"action": "click", "button": "middle"},
        {"action": "click", "button": "LEFT"},
        {"action": "click", "button": 1},
        {"action": "set_context", "context": "gaming"},
        {"action": "set_context", "context": None},
    ],
)
def test_rejects_invalid_choices(msg: dict) -> None:
    with pytest.raises(ProtocolError, match="must be one of"):
        parse_message(json.dumps(msg))


# --- unsafe mouse coordinates --------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '{"action":"mouse_move","x":-0.01,"y":0.5}',
        '{"action":"mouse_move","x":0.5,"y":1.01}',
        '{"action":"mouse_move","x":1920,"y":1080}',
        '{"action":"mouse_move","x":1e308,"y":0.5}',
        '{"action":"mouse_move","x":NaN,"y":0.5}',
        '{"action":"mouse_move","x":Infinity,"y":0.5}',
        '{"action":"mouse_move","x":"0.5","y":0.5}',
        '{"action":"mouse_move","x":true,"y":0.5}',
    ],
)
def test_rejects_unsafe_mouse_coordinates(raw: str) -> None:
    with pytest.raises(ProtocolError):
        parse_message(raw)


# --- unknown actions and malformed data ----------------------------------------


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ('{"action":"shutdown"}', "unknown action"),
        ('{"action":"NEXT_SLIDE"}', "unknown action"),
        ('{"action":42}', "must be a string"),
        ("{}", "missing 'action'"),
        ('{"value":60}', "missing 'action'"),
        ("not json", "malformed JSON"),
        ('{"action":"ping"', "malformed JSON"),
        ('{"action":"ping"} {"action":"ping"}', "malformed JSON"),
        ("[]", "must be a JSON object"),
        ('"ping"', "must be a JSON object"),
        ("42", "must be a JSON object"),
        ("null", "must be a JSON object"),
        ("", "empty message"),
        ("   \n", "empty message"),
        ("[" * 3000, "malformed JSON"),
    ],
)
def test_rejects_malformed_and_unknown(raw: str, match: str) -> None:
    with pytest.raises(ProtocolError, match=match):
        parse_message(raw)


def test_rejects_invalid_utf8() -> None:
    with pytest.raises(ProtocolError, match="UTF-8"):
        parse_message(b'{"action":"\xff"}')


def test_rejects_oversized_message() -> None:
    raw = json.dumps({"action": "ping", "pad": "x" * 5000})
    with pytest.raises(ProtocolError, match="too large"):
        parse_message(raw)


# --- framing and responses -----------------------------------------------------------


def test_split_lines_ignores_blank_lines() -> None:
    assert split_lines('{"action":"ping"}\n\n{"action":"next_slide"}\n') == [
        '{"action":"ping"}',
        '{"action":"next_slide"}',
    ]
    assert split_lines(b'{"a":1}\n') == [b'{"a":1}']
    assert split_lines("  \n") == []


def test_response_shapes() -> None:
    assert ok("next_slide") == {"status": "ok", "action": "next_slide"}
    assert error("bad") == {"status": "error", "message": "bad"}
    assert context_response(Context.PRESENTATION) == {"status": "context", "context": "presentation"}
    assert pong(Context.MUSIC) == {"status": "pong", "context": "music"}


def test_encode_is_one_json_line() -> None:
    line = encode(ok("ping"))
    assert line.endswith("\n") and line.count("\n") == 1
    assert json.loads(line) == {"status": "ok", "action": "ping"}
