"""Scripted MORPH demo: sends a fixed action sequence and prints every reply.

Start the server first (python -m morph_desktop.server), then run:
    python -m morph_desktop.demo_client [--url ws://127.0.0.1:8765] [--delay 0.5]

If MORPH_AUTH_TOKEN is set, the client authenticates first. The token is
never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from .auth import NOT_AUTHENTICATED, REDACTED
from .config import Settings

DEMO_SEQUENCE: list[dict[str, Any]] = [
    {"action": "ping"},
    {"action": "set_context", "context": "presentation"},
    {"action": "next_slide"},
    {"action": "previous_slide"},
    {"action": "set_context", "context": "music"},
    {"action": "set_volume", "value": 60},
    {"action": "volume_delta", "delta": -10},
    {"action": "play_pause"},
    {"action": "mouse_move", "x": 0.5, "y": 0.5},
    {"action": "click", "button": "left"},
]

REPLY_TIMEOUT_S = 5.0

# websockets logs raw frames (including the token) at DEBUG; never let it.
transport_logger = logging.getLogger("morph.demo.transport")
transport_logger.setLevel(logging.WARNING)


async def _exchange(ws: ClientConnection, label: str, message: dict[str, Any]) -> dict[str, Any]:
    """Send one message, print it (token hidden) and the reply, and return the reply."""
    shown = {**message, "token": REDACTED} if "token" in message else message
    print(f"{label} SENT     -> {json.dumps(shown)}")
    await ws.send(json.dumps(message))
    raw = await asyncio.wait_for(ws.recv(), timeout=REPLY_TIMEOUT_S)
    text = raw.decode() if isinstance(raw, bytes) else raw
    print(f"{' ' * len(label)} RECEIVED <- {text.strip()}")
    return json.loads(text)


async def run_demo(url: str, delay_s: float = 0.5, token: str | None = None) -> int:
    """Run the sequence. Returns the number of error replies."""
    errors = 0
    print(f"Connecting to MORPH desktop agent at {url} ...")
    async with connect(url, open_timeout=REPLY_TIMEOUT_S, logger=transport_logger) as ws:
        print("Connected.\n")
        if token is not None:
            reply = await _exchange(ws, "[auth]", {"action": "authenticate", "token": token})
            if reply.get("status") != "ok":
                print("\nAuthentication failed: MORPH_AUTH_TOKEN must match the server's token.")
                return 1
        for step, message in enumerate(DEMO_SEQUENCE, start=1):
            reply = await _exchange(ws, f"[{step:2d}/{len(DEMO_SEQUENCE)}]", message)
            if reply.get("status") == "error":
                errors += 1
                if str(reply.get("message", "")).startswith(NOT_AUTHENTICATED):
                    print("\nThe server requires a token: set MORPH_AUTH_TOKEN to the server's value.")
                    return errors
            await asyncio.sleep(delay_s)
    print(f"\nDemo finished: {len(DEMO_SEQUENCE)} messages sent, {errors} error repl{'y' if errors == 1 else 'ies'}.")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MORPH scripted demo client")
    parser.add_argument("--url", help="server URL (default: from MORPH_HOST/MORPH_PORT)")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between steps (default 0.5)")
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    url = args.url or settings.url

    try:
        errors = asyncio.run(run_demo(url, args.delay, settings.auth_token))
    except KeyboardInterrupt:
        print("\nDemo interrupted.")
        return 130
    except (OSError, WebSocketException, TimeoutError) as exc:
        print(f"\nCould not complete the demo against {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Is the server running?  python -m morph_desktop.server", file=sys.stderr)
        return 1
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
