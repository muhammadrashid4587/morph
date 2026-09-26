"""Optional shared-secret authorization (MORPH_AUTH_TOKEN)."""

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedError

from morph_desktop import server as server_module
from morph_desktop.actions import MockActionExecutor
from morph_desktop.auth import MAX_FAILED_AUTH_ATTEMPTS, REDACTED, redact
from morph_desktop.config import Settings
from morph_desktop.context import Context, ContextState
from morph_desktop.demo_client import run_demo
from morph_desktop.protocol import NextSlide, parse_message
from morph_desktop.server import MorphAgent, MorphServer

SECRET = "morph-test-SECRET-7f3a9c1e2b"
AUTH = json.dumps({"action": "authenticate", "token": SECRET})
WRONG_AUTH = json.dumps({"action": "authenticate", "token": "not-the-secret"})

RunServer = Callable[..., MorphServer]


def protected_agent() -> MorphAgent:
    return MorphAgent(MockActionExecutor(), ContextState(Context.MUSIC), auth_token=SECRET)


def history(agent: MorphAgent) -> list[Any]:
    assert isinstance(agent.executor, MockActionExecutor)
    return list(agent.executor.history)


async def request(ws: ClientConnection, message: str) -> dict[str, Any]:
    await ws.send(message)
    return json.loads(await ws.recv())


# --- token absent: existing behavior ----------------------------------------------


def test_no_token_preserves_existing_behavior() -> None:
    agent = MorphAgent(MockActionExecutor())
    session = agent.new_session("pi")
    assert agent.auth_required is False
    assert session.authorized is True and session.auth_state == "open"
    assert agent.handle_line('{"action":"ping"}', session).response == {"status": "pong", "context": "music"}
    assert agent.handle_line('{"action":"next_slide"}', session).response == {"status": "ok", "action": "next_slide"}
    reply = agent.handle_line('{"action":"set_context","context":"teach"}', session)
    assert reply.response == {"status": "context", "context": "teach"}
    assert history(agent) == [NextSlide()]


def test_authenticate_is_harmless_when_no_token_configured() -> None:
    agent = MorphAgent(MockActionExecutor())
    assert agent.handle_line(AUTH).response == {"status": "ok", "action": "authenticate"}


def test_no_token_server_roundtrip(run_server: RunServer) -> None:
    async def scenario(url: str, server: MorphServer) -> None:
        async with connect(url) as ws:
            assert await request(ws, '{"action":"next_slide"}') == {"status": "ok", "action": "next_slide"}

    run_server(scenario)


# --- token configured: unauthenticated clients can do nothing ------------------------


@pytest.mark.parametrize(
    "line",
    [
        '{"action":"ping"}',
        '{"action":"next_slide"}',
        '{"action":"previous_slide"}',
        '{"action":"set_volume","value":60}',
        '{"action":"volume_delta","delta":-5}',
        '{"action":"play_pause"}',
        '{"action":"mouse_move","x":0.5,"y":0.5}',
        '{"action":"click","button":"left"}',
        '{"action":"set_context","context":"presentation"}',
    ],
)
def test_unauthenticated_commands_are_rejected(line: str) -> None:
    agent = protected_agent()
    session = agent.new_session("pi")
    reply = agent.handle_line(line, session)
    assert reply.response["status"] == "error"
    assert reply.response["message"].startswith("not authenticated")
    assert reply.broadcast is None
    assert history(agent) == []  # no action ran
    assert agent.context.current == Context.MUSIC  # context unchanged
    assert session.authorized is False


def test_wrong_token_is_rejected() -> None:
    agent = protected_agent()
    session = agent.new_session("pi")
    reply = agent.handle_line(WRONG_AUTH, session)
    assert reply.response["status"] == "error"
    assert "invalid token" in reply.response["message"]
    assert session.authorized is False
    assert agent.handle_line('{"action":"next_slide"}', session).response["status"] == "error"
    assert history(agent) == []


@pytest.mark.parametrize(
    "line",
    [
        '{"action":"authenticate"}',
        '{"action":"authenticate","token":""}',
        '{"action":"authenticate","token":12345}',
        '{"action":"authenticate","token":null}',
        json.dumps({"action": "authenticate", "token": "x" * 300}),
    ],
)
def test_malformed_authenticate_is_rejected(line: str) -> None:
    agent = protected_agent()
    session = agent.new_session("pi")
    assert agent.handle_line(line, session).response["status"] == "error"
    assert session.authorized is False


def test_correct_token_permits_commands() -> None:
    agent = protected_agent()
    session = agent.new_session("pi")
    assert agent.handle_line(AUTH, session).response == {"status": "ok", "action": "authenticate"}
    assert session.authorized is True and session.auth_state == "authorized"
    assert agent.handle_line('{"action":"next_slide"}', session).response == {"status": "ok", "action": "next_slide"}
    reply = agent.handle_line('{"action":"set_context","context":"presentation"}', session)
    assert reply.broadcast == {"status": "context", "context": "presentation"}
    assert history(agent) == [NextSlide()]


def test_authorization_is_per_session() -> None:
    agent = protected_agent()
    alice, bob = agent.new_session("alice"), agent.new_session("bob")
    agent.handle_line(AUTH, alice)
    assert agent.handle_line('{"action":"next_slide"}', alice).response["status"] == "ok"
    assert agent.handle_line('{"action":"next_slide"}', bob).response["status"] == "error"
    assert bob.authorized is False


def test_wrong_token_revokes_earlier_authorization() -> None:
    agent = protected_agent()
    session = agent.new_session("pi")
    agent.handle_line(AUTH, session)
    agent.handle_line(WRONG_AUTH, session)
    assert session.authorized is False


def test_repeated_failures_request_close() -> None:
    agent = protected_agent()
    session = agent.new_session("pi")
    replies = [agent.handle_line(WRONG_AUTH, session) for _ in range(MAX_FAILED_AUTH_ATTEMPTS)]
    assert [r.close for r in replies] == [False] * (MAX_FAILED_AUTH_ATTEMPTS - 1) + [True]


# --- over a real WebSocket ---------------------------------------------------------------


def test_protected_server_end_to_end(run_server: RunServer) -> None:
    async def scenario(url: str, server: MorphServer) -> None:
        async with connect(url) as authed, connect(url) as anon:
            assert (await request(anon, '{"action":"next_slide"}'))["status"] == "error"
            assert (await request(authed, WRONG_AUTH))["status"] == "error"
            assert await request(authed, AUTH) == {"status": "ok", "action": "authenticate"}
            assert await request(authed, '{"action":"next_slide"}') == {"status": "ok", "action": "next_slide"}
            # Authorization is per connection: `anon` is still locked out.
            assert (await request(anon, '{"action":"previous_slide"}'))["status"] == "error"
            # Context broadcasts only reach authorized connections: anon's next
            # message must be the reply to its own ping, not the broadcast.
            await request(authed, '{"action":"set_context","context":"teach"}')
            reply = await request(anon, '{"action":"ping"}')
            assert reply["status"] == "error" and reply["message"].startswith("not authenticated")

    server = run_server(scenario, protected_agent())
    assert history(server.agent) == [NextSlide()]
    assert server.agent.context.current == Context.TEACH


def test_too_many_failed_attempts_close_the_connection(run_server: RunServer) -> None:
    async def scenario(url: str, server: MorphServer) -> None:
        async with connect(url) as ws:
            for _ in range(MAX_FAILED_AUTH_ATTEMPTS):
                assert (await request(ws, WRONG_AUTH))["status"] == "error"
            with pytest.raises(ConnectionClosedError) as info:
                await ws.recv()
            assert info.value.rcvd is not None and info.value.rcvd.code == 1008
        async with connect(url) as ws:  # a fresh connection can still authenticate
            assert (await request(ws, AUTH))["status"] == "ok"

    run_server(scenario, protected_agent())


# --- the secret never leaks ----------------------------------------------------------------


LEAKY_LINES = [
    AUTH,
    json.dumps({"action": "authenticate", "token": SECRET + "-typo"}),
    '{"action":"authenticate","token":"' + SECRET,  # malformed, unterminated
    '{"action":"' + SECRET + '"}',  # unknown action echoes its name
    '{"action":"next_slide","token":"' + SECRET + '"}',  # unexpected field
    json.dumps({"action": "authenticate", "token": SECRET, "extra": 1}),
    json.dumps({"action": "set_context", "context": SECRET}),
    SECRET,  # not JSON at all
]


def test_secret_not_in_logs_or_errors(run_server: RunServer, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    responses: list[dict[str, Any]] = []

    # This test's own client is not MORPH code; keep its frame dumps out of caplog.
    quiet = logging.getLogger("test.client")
    quiet.setLevel(logging.WARNING)

    async def scenario(url: str, server: MorphServer) -> None:
        async with connect(url, user_agent_header=f"pi-client {SECRET}", logger=quiet) as ws:
            for line in LEAKY_LINES:
                responses.append(await request(ws, line))

    run_server(scenario, protected_agent())
    assert responses and any(r["status"] == "error" for r in responses)
    assert SECRET not in json.dumps(responses)
    # Everything the server side logged, at every level, including websockets internals.
    assert SECRET not in caplog.text
    assert "AUTHENTICATED" in caplog.text and REDACTED in caplog.text  # logging did happen


def test_secret_not_in_reprs() -> None:
    assert SECRET not in repr(parse_message(AUTH))
    assert SECRET not in repr(Settings.from_env({"MORPH_AUTH_TOKEN": SECRET}))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"action":"authenticate","token":"abc"}', '{"action":"authenticate","token":"<redacted>"}'),
        ('{"token" : "a\\"b"}', '{"token" : "<redacted>"}'),
        ('{"token":"unterminated', '{"token":"<redacted>"'),
        ('{"action":"ping"}', '{"action":"ping"}'),
    ],
)
def test_redact_token_fields(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_redact_literal_secret() -> None:
    assert redact(f"unknown action '{SECRET}'", SECRET) == "unknown action '<redacted>'"


# --- configuration ----------------------------------------------------------------------------


def test_settings_token_from_env() -> None:
    assert Settings.from_env({}).auth_token is None
    assert Settings.from_env({"MORPH_AUTH_TOKEN": SECRET}).auth_token == SECRET


@pytest.mark.parametrize("raw", ["", "   ", f" {SECRET}", f"{SECRET}\n", "x" * 257])
def test_settings_rejects_bad_tokens_without_echoing_them(raw: str) -> None:
    with pytest.raises(ValueError) as info:
        Settings.from_env({"MORPH_AUTH_TOKEN": raw})
    assert SECRET not in str(info.value)


def test_refuses_real_actions_on_network_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORPH_REAL_ACTIONS", "1")
    monkeypatch.delenv("MORPH_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(server_module.asyncio, "run", lambda *_: pytest.fail("server must not start"))
    assert server_module.main(["--host", "0.0.0.0"]) == 2


# --- demo client -------------------------------------------------------------------------------


def test_demo_client_authenticates_without_printing_token(
    run_server: RunServer, capsys: pytest.CaptureFixture[str]
) -> None:
    results: list[int] = []

    async def scenario(url: str, server: MorphServer) -> None:
        results.append(await run_demo(url, delay_s=0, token=SECRET))

    server = run_server(scenario, protected_agent())
    out = capsys.readouterr().out
    assert results == [0]
    assert SECRET not in out and REDACTED in out
    assert len(history(server.agent)) == 7  # every desktop action in the demo ran


@pytest.mark.parametrize(("token", "hint"), [(None, "requires a token"), ("wrong-token", "Authentication failed")])
def test_demo_client_reports_auth_problems(
    run_server: RunServer, capsys: pytest.CaptureFixture[str], token: str | None, hint: str
) -> None:
    results: list[int] = []

    async def scenario(url: str, server: MorphServer) -> None:
        results.append(await run_demo(url, delay_s=0, token=token))

    server = run_server(scenario, protected_agent())
    assert results[0] > 0
    assert hint in capsys.readouterr().out
    assert history(server.agent) == []
