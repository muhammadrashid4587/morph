import asyncio
from collections.abc import Awaitable, Callable

import pytest

from morph_desktop.actions import MockActionExecutor
from morph_desktop.server import MorphAgent, MorphServer

Scenario = Callable[[str, MorphServer], Awaitable[None]]


@pytest.fixture
def run_server() -> Callable[..., MorphServer]:
    """Run `scenario(url, server)` against a real loopback server on a free port.

    Uses the mock executor unless an agent is given; returns the server afterwards
    so tests can inspect executor history and context.
    """

    def runner(scenario: Scenario, agent: MorphAgent | None = None) -> MorphServer:
        async def main() -> MorphServer:
            server = MorphServer(agent or MorphAgent(MockActionExecutor()))
            async with server.serve("127.0.0.1", 0) as ws_server:
                port = ws_server.sockets[0].getsockname()[1]
                await asyncio.wait_for(scenario(f"ws://127.0.0.1:{port}", server), timeout=10)
            return server

        return asyncio.run(main())

    return runner
