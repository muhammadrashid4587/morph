"""Carrying out an allowed action. DryRunExecutor (default) only prints.

LaptopExecutor sends the single mapped message to the morph_desktop agent over
WebSocket (authenticating with MORPH_AUTH_TOKEN from .env if set). Nothing here
can reach servos, the Nano or a shell: only actions.laptop_message() output is sent.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from morph_voice.env import get_setting

from .actions import AgentAction, laptop_message


class DryRunExecutor:
    def __init__(self, out: Callable[[str], None] = print) -> None:
        self.out = out

    def execute(self, action: AgentAction) -> str:
        message = laptop_message(action)
        line = f"[DRY RUN] would send {json.dumps(message)}" if message else "[DRY RUN] speech only, nothing to send"
        self.out(line)
        return line


class LaptopExecutor:
    def __init__(self, url: str | None = None, connect: Callable[..., Any] | None = None) -> None:
        self.url = url or get_setting("MORPH_LAPTOP_URL", "ws://127.0.0.1:8765")
        self._connect = connect

    def execute(self, action: AgentAction) -> str:
        message = laptop_message(action)
        if message is None:
            return "speech only, nothing sent"
        connect = self._connect
        if connect is None:
            from websockets.sync.client import connect
        token = get_setting("MORPH_AUTH_TOKEN")
        with connect(self.url, open_timeout=5) as ws:
            if token:
                ws.send(json.dumps({"action": "authenticate", "token": token}))
                if json.loads(ws.recv(timeout=5)).get("status") != "ok":
                    return "laptop rejected MORPH_AUTH_TOKEN"
            ws.send(json.dumps(message))
            reply = json.loads(ws.recv(timeout=5))
        return f"sent {json.dumps(message)} -> {json.dumps(reply)}"
