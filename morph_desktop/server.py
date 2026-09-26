"""MORPH desktop agent: local WebSocket server.

Run with:  python -m morph_desktop.server  [--host 127.0.0.1] [--port 8765]

MorphAgent turns one JSON line into one response and knows nothing about the
network, so it is easy to test. It also enforces optional token auth, per
ClientSession. MorphServer owns the WebSocket transport: connection
bookkeeping, framing, and broadcasting context changes.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import signal
import sys
from dataclasses import dataclass
from http import HTTPStatus

from websockets.asyncio.server import Server, ServerConnection, broadcast, serve
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK
from websockets.http11 import Request
from websockets.http11 import Response as HTTPResponse

from .actions import ActionExecutor, ActionUnavailable, create_executor
from .auth import (
    MAX_FAILED_AUTH_ATTEMPTS,
    MIN_RECOMMENDED_TOKEN_LENGTH,
    UNAUTHENTICATED_MESSAGE,
    ClientSession,
    redact,
    tokens_match,
)
from .config import MAX_MESSAGE_BYTES, Settings, setup_logging
from .context import ContextState
from .protocol import (
    Authenticate,
    Ping,
    ProtocolError,
    Response,
    SetContext,
    context_response,
    encode,
    error,
    ok,
    parse_message,
    pong,
    split_lines,
)

logger = logging.getLogger("morph.server")

# The websockets library logs raw frames (including authenticate tokens) at
# DEBUG. Its logs go through this logger, fixed at WARNING, so that no logging
# configuration can expose secrets.
transport_logger = logging.getLogger("morph.transport")
transport_logger.setLevel(logging.WARNING)

# WebSocket close code for "policy violation" (RFC 6455).
CLOSE_POLICY_VIOLATION = 1008


@dataclass(frozen=True, slots=True)
class Reply:
    response: Response  # sent back to the sender
    broadcast: Response | None = None  # sent to every *other* authorized client
    close: bool = False  # close the sender's connection after replying


class MorphAgent:
    """Validates one incoming line and dispatches it. Never raises on bad input.

    With auth_token set, a session may only authenticate until it presents the
    token; nothing else runs and the context cannot change before that.
    """

    def __init__(
        self,
        executor: ActionExecutor,
        context: ContextState | None = None,
        auth_token: str | None = None,
    ) -> None:
        if auth_token is not None and not auth_token:
            raise ValueError("auth_token must be None or a non-empty string")
        self.executor = executor
        self.context = context or ContextState()
        self._auth_token = auth_token  # never log this

    @property
    def auth_required(self) -> bool:
        return self._auth_token is not None

    def new_session(self, name: str = "local", user_agent: str | None = None) -> ClientSession:
        return ClientSession(
            name=name,
            auth_required=self.auth_required,
            authorized=not self.auth_required,
            user_agent=user_agent,
        )

    def redact(self, text: str) -> str:
        return redact(text, self._auth_token)

    def handle_line(self, line: str | bytes, session: ClientSession | None = None) -> Reply:
        if session is None:
            session = self.new_session()
        session.received += 1
        try:
            action = parse_message(line)
        except ProtocolError as exc:
            return self._reject(session, str(exc))

        if isinstance(action, Authenticate):
            return self._authenticate(session, action.token)
        if not session.authorized:
            return self._reject(session, UNAUTHENTICATED_MESSAGE, action.name)

        match action:
            case Ping():
                return Reply(pong(self.context.current))
            case SetContext(context=new_context):
                changed = self.context.set(new_context)
                logger.info("ACCEPTED set_context from %s", session.name)
                response = context_response(new_context)
                return Reply(response, broadcast=response if changed else None)
            case _:
                try:
                    self.executor.execute(action)
                except ActionUnavailable as exc:
                    return self._reject(session, str(exc), action.name)
                logger.info("ACCEPTED %s from %s", action.name, session.name)
                return Reply(ok(action.name))

    def _authenticate(self, session: ClientSession, token: str) -> Reply:
        if self._auth_token is None:
            logger.warning(
                "%s sent authenticate, but MORPH_AUTH_TOKEN is not set: no auth is required",
                session.name,
            )
            return Reply(ok(Authenticate.name))
        if tokens_match(token, self._auth_token):
            session.authorized = True
            session.failed_auth = 0
            logger.info("AUTHENTICATED %s", session.name)
            return Reply(ok(Authenticate.name))

        session.authorized = False  # a wrong token also revokes earlier success
        session.failed_auth += 1
        reply = self._reject(
            session,
            f"authentication failed: invalid token "
            f"(attempt {session.failed_auth} of {MAX_FAILED_AUTH_ATTEMPTS})",
            Authenticate.name,
        )
        if session.failed_auth >= MAX_FAILED_AUTH_ATTEMPTS:
            logger.warning("closing %s after %d failed authentication attempts",
                           session.name, session.failed_auth)
            return Reply(reply.response, close=True)
        return reply

    def _reject(self, session: ClientSession, message: str, action: str | None = None) -> Reply:
        message = self.redact(message)
        session.rejected += 1
        logger.warning("REJECTED %sfrom %s [auth=%s]: %s",
                       f"{action} " if action else "", session.name, session.auth_state, message)
        return Reply(error(message))


class MorphServer:
    def __init__(self, agent: MorphAgent) -> None:
        self.agent = agent
        self.clients: dict[ServerConnection, ClientSession] = {}
        self._lock = asyncio.Lock()  # one action at a time, across all clients
        self._next_id = 1

    def serve(self, host: str, port: int) -> Server:
        return serve(
            self.handler,
            host,
            port,
            # Only non-browser clients (no Origin header) may connect, so a web
            # page open on this laptop cannot drive the mouse/keyboard.
            process_request=self._reject_browsers,
            origins=[None],
            max_size=MAX_MESSAGE_BYTES,
            compression=None,
            logger=transport_logger,
        )

    def _reject_browsers(self, ws: ServerConnection, request: Request) -> HTTPResponse | None:
        origin = request.headers.get("Origin")
        if origin is None:
            return None
        logger.warning(
            "REJECTED connection from %s: browser Origin %r is not allowed",
            _format_addr(ws.remote_address), self.agent.redact(origin),
        )
        return ws.respond(HTTPStatus.FORBIDDEN, "MORPH accepts local non-browser clients only.\n")

    def _user_agent(self, ws: ServerConnection) -> str | None:
        value = ws.request.headers.get("User-Agent") if ws.request is not None else None
        return None if value is None else _truncate(self.agent.redact(value), 80)

    async def handler(self, ws: ServerConnection) -> None:
        session = self.agent.new_session(
            name=f"client#{self._next_id}@{_format_addr(ws.remote_address)}",
            user_agent=self._user_agent(ws),
        )
        self._next_id += 1
        self.clients[ws] = session
        logger.info("CONNECTED %s (user-agent=%r, auth=%s, %d connected)",
                    session.name, session.user_agent, session.auth_state, len(self.clients))
        try:
            async for frame in ws:
                if not await self._handle_frame(ws, session, frame):
                    break
        except ConnectionClosedOK:
            pass
        except ConnectionClosed as exc:
            logger.warning("connection error on %s: %s", session.name, exc)
        finally:
            self.clients.pop(ws, None)
            logger.info(
                "DISCONNECTED %s after %.1fs (auth=%s, messages=%d, rejected=%d, %d connected)",
                session.name, session.age_s, session.auth_state,
                session.received, session.rejected, len(self.clients),
            )

    async def _handle_frame(self, ws: ServerConnection, session: ClientSession, frame: str | bytes) -> bool:
        """Handle every line in a frame. Returns False once the connection should close."""
        # A blank frame still gets exactly one ("empty message") error reply.
        for line in split_lines(frame) or [frame]:
            logger.info("RECV %s [auth=%s]: %s", session.name, session.auth_state,
                        _truncate(self.agent.redact(_as_text(line)).strip()))
            try:
                async with self._lock:
                    # Real actions block (pyautogui pauses), so keep them off the event loop.
                    reply = await asyncio.to_thread(self.agent.handle_line, line, session)
            except Exception:
                logger.exception("internal error while handling message from %s", session.name)
                reply = Reply(error("internal server error"))
            await ws.send(encode(reply.response))
            if reply.broadcast is not None:
                others = [c for c, s in self.clients.items() if c is not ws and s.authorized]
                broadcast(others, encode(reply.broadcast))
            if reply.close:
                await ws.close(CLOSE_POLICY_VIOLATION, "too many failed authentication attempts")
                return False
        return True


def _format_addr(addr: object) -> str:
    if isinstance(addr, tuple) and len(addr) >= 2:
        return f"{addr[0]}:{addr[1]}"
    return str(addr)


def _as_text(line: str | bytes) -> str:
    return line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line


def _truncate(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


async def run(settings: Settings, executor: ActionExecutor | None = None) -> None:
    if executor is None:
        executor = create_executor(settings.real_actions)
    server = MorphServer(MorphAgent(executor, auth_token=settings.auth_token))

    loop = asyncio.get_running_loop()
    stop: asyncio.Future[None] = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))
        except (NotImplementedError, RuntimeError):
            pass  # e.g. Windows: Ctrl+C arrives as KeyboardInterrupt instead

    async with server.serve(settings.host, settings.port):
        logger.info(
            "MORPH desktop agent listening on %s (mode: %s, auth: %s, context: %s). Ctrl+C to stop.",
            settings.url, executor.mode.upper(),
            "TOKEN REQUIRED" if server.agent.auth_required else "off",
            server.agent.context.current.value,
        )
        await stop
        logger.info("shutting down, closing %d connection(s)...", len(server.clients))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MORPH desktop agent (WebSocket server)")
    parser.add_argument("--host", help="interface to bind (default: MORPH_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="port (default: MORPH_PORT or 8765)")
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    overrides = {k: v for k, v in (("host", args.host), ("port", args.port)) if v is not None}
    settings = dataclasses.replace(settings, **overrides)

    setup_logging(settings.log_level)
    if not settings.is_loopback and settings.auth_token is None:
        if settings.real_actions:
            logger.error(
                "refusing to start: real actions on non-localhost host %s without "
                "MORPH_AUTH_TOKEN would let anyone on this network control this machine",
                settings.host,
            )
            return 2
        logger.warning(
            "binding to %s, NOT localhost, without MORPH_AUTH_TOKEN: other devices on "
            "this network can send actions",
            settings.host,
        )
    if settings.auth_token is not None and len(settings.auth_token) < MIN_RECOMMENDED_TOKEN_LENGTH:
        logger.warning("MORPH_AUTH_TOKEN is shorter than %d characters; use a longer random token",
                       MIN_RECOMMENDED_TOKEN_LENGTH)

    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        logger.error("could not start server on %s: %s", settings.url, exc)
        return 1
    logger.info("MORPH desktop agent stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
