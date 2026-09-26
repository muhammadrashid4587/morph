"""End-to-end tests over a real loopback WebSocket, using the mock executor."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedError, InvalidStatus

from morph_desktop.actions import MockActionExecutor
from morph_desktop.protocol import NextSlide
from morph_desktop.server import MorphServer

RunServer = Callable[..., MorphServer]


async def request(ws: ClientConnection, message: Any) -> dict[str, Any]:
    await ws.send(message if isinstance(message, str | bytes) else json.dumps(message))
    raw = await ws.recv()
    assert isinstance(raw, str) and raw.endswith("\n")
    return json.loads(raw)


def test_roundtrip_and_error_recovery(run_server: RunServer) -> None:
    async def scenario(url: str, server: MorphServer) -> None:
        async with connect(url) as ws:
            assert await request(ws, {"action": "ping"}) == {"status": "pong", "context": "music"}
            assert (await request(ws, "{not json"))["status"] == "error"
            assert (await request(ws, b"\xff\xfe"))["status"] == "error"
            assert (await request(ws, ""))["status"] == "error"
            # The connection survives bad input and keeps working.
            assert await request(ws, {"action": "next_slide"}) == {"status": "ok", "action": "next_slide"}

    server = run_server(scenario)
    assert isinstance(server.agent.executor, MockActionExecutor)
    assert list(server.agent.executor.history) == [NextSlide()]


def test_multiple_lines_in_one_frame_get_one_reply_each(run_server: RunServer) -> None:
    async def scenario(url: str, server: MorphServer) -> None:
        async with connect(url) as ws:
            await ws.send('{"action":"ping"}\n{"action":"next_slide"}\n')
            first = json.loads(await ws.recv())
            second = json.loads(await ws.recv())
            assert first["status"] == "pong"
            assert second == {"status": "ok", "action": "next_slide"}

    run_server(scenario)


def test_context_change_is_broadcast_to_other_clients(run_server: RunServer) -> None:
    async def scenario(url: str, server: MorphServer) -> None:
        async with connect(url) as sender, connect(url) as listener:
            await request(listener, {"action": "ping"})  # ensure both are registered
            reply = await request(sender, {"action": "set_context", "context": "presentation"})
            assert reply == {"status": "context", "context": "presentation"}
            pushed = json.loads(await asyncio.wait_for(listener.recv(), timeout=2))
            assert pushed == {"status": "context", "context": "presentation"}

    run_server(scenario)


def test_browser_origins_are_rejected(run_server: RunServer, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="morph.server")

    async def scenario(url: str, server: MorphServer) -> None:
        with pytest.raises(InvalidStatus) as info:
            async with connect(url, origin="https://evil.example"):
                pass
        assert info.value.response.status_code == 403

    run_server(scenario)
    assert "browser Origin 'https://evil.example' is not allowed" in caplog.text


def test_oversized_frame_closes_connection_without_crashing_server(run_server: RunServer) -> None:
    async def scenario(url: str, server: MorphServer) -> None:
        async with connect(url) as ws:
            await ws.send(json.dumps({"action": "ping", "pad": "x" * 10_000}))
            with pytest.raises(ConnectionClosedError):
                await ws.recv()
        async with connect(url) as ws:  # server is still healthy
            assert (await request(ws, {"action": "ping"}))["status"] == "pong"

    run_server(scenario)
