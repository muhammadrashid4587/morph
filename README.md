# MORPH — desktop agent

**MORPH** is a mobile, embodied interface for a computer. Instead of fixed menus
and keyboard shortcuts, you *point*. MORPH works out what you meant, *physically
confirms* it by pointing back with its robot arm, and *adapts* its physical
controls (dial, buttons, LCD, LEDs) to the task at hand. Then it *acts*. The
same dial adjusts volume while music plays, flips slides during a presentation,
selects targets when steering the robot, and records gestures in teach mode.
The loop is **POINT → CONFIRM → ADAPT → ACT**. MORPH is an OwlHacks HCI project.

This repository holds the first building block: **`morph_desktop`**, a small,
local, strictly validated service that turns JSON actions into laptop actions.
It runs in **safe mock mode by default**.

## Architecture

```
                         ┌──────────── WebSocket (JSON) ────────────┐
                         │                                          ▼
┌──────────────────────────────┐                   ┌──────────────────────────────┐
│ Raspberry Pi vision / HCI    │                   │ morph_desktop (this repo)    │
│ (pointing, confirm, context) │                   │ validate → context → execute │──▶ laptop actions
└──────────────────────────────┘                   └──────────────────────────────┘    (slides, volume,
                         │                                                              mouse, click)
                         └──────────── serial JSON ───────────▶ ESP32 ──▶ motors / LCD / LEDs
```

- Raspberry Pi vision/HCI → **WebSocket** → `morph_desktop` → laptop actions
- Raspberry Pi vision/HCI → **serial JSON** → ESP32 → motors / LCD / LEDs

Only `morph_desktop` exists so far. The Pi and ESP32 code will come later.

| Module | Responsibility |
|---|---|
| `protocol.py` | Typed action dataclasses, strict validation, response builders |
| `context.py` | The four interaction contexts and what the dial means in each one |
| `actions.py` | `ActionExecutor` interface, `MockActionExecutor` (default), `RealActionExecutor` (opt-in) |
| `auth.py` | Optional shared-secret auth: per-connection sessions, log redaction |
| `server.py` | `MorphAgent` (network-free dispatcher that enforces auth) and the WebSocket server |
| `config.py` | Settings read from environment variables, plus logging setup |
| `demo_client.py` | Scripted 10-step demo |

## Setup

Requires **Python 3.11+**.

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Windows (cmd)
.venv\Scripts\activate.bat

pip install -r requirements.txt
```

## Run

```bash
# Terminal 1: start the agent (mock mode, ws://127.0.0.1:8765)
python -m morph_desktop.server

# Terminal 2: send the scripted demo sequence
python -m morph_desktop.demo_client

# Tests (these never touch the real mouse or keyboard)
pytest
```

Press `Ctrl+C` to stop the server cleanly.

### Local mock development (no token)

This is the default and needs no configuration. Only this laptop can connect,
and nothing touches the real mouse or keyboard:

```bash
python -m morph_desktop.server          # terminal 1
python -m morph_desktop.demo_client     # terminal 2
```

### Protected mode (with a token)

Set `MORPH_AUTH_TOKEN` to a long random secret. Every connection must then
authenticate before anything else. Use this whenever something other than
this laptop (the Raspberry Pi) connects.

```bash
# Generate a token once and share it with the Pi out-of-band. Never commit it.
python -c "import secrets; print(secrets.token_urlsafe(24))"

# macOS / Linux
export MORPH_AUTH_TOKEN='paste-the-token-here'
python -m morph_desktop.server                            # terminal 1, localhost only
MORPH_HOST=0.0.0.0 python -m morph_desktop.server         # ...or reachable by the Pi on the LAN
python -m morph_desktop.demo_client                       # terminal 2 (same token exported) authenticates automatically
```

```powershell
# Windows PowerShell
$env:MORPH_AUTH_TOKEN = "paste-the-token-here"
python -m morph_desktop.server
python -m morph_desktop.demo_client
```

The demo client reads `MORPH_AUTH_TOKEN` and sends `authenticate` first. It
prints the token as `<redacted>`. If the server needs a token and the client
has none, or the wrong one, the client says so and stops.

Options: `--host` and `--port` on the server, `--url` and `--delay` on the demo
client. Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `MORPH_HOST` | `127.0.0.1` | Interface to bind. Keep it on localhost unless you understand the risk. |
| `MORPH_PORT` | `8765` | TCP port |
| `MORPH_REAL_ACTIONS` | *(unset)* | Set to exactly `1` to allow real mouse/keyboard control |
| `MORPH_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, ... |
| `MORPH_AUTH_TOKEN` | *(unset)* | Shared secret. When set, clients must authenticate first (see below). Setting it to an empty or whitespace-padded value is a startup error. |

## Protocol

Messages are **newline-delimited JSON carried over WebSocket**. Each message is
one JSON object. A frame normally carries one message. A frame with several
`\n`-separated objects is treated as several messages, and each one gets its
own reply. Each reply is one compact JSON object followed by `\n`.

### Incoming actions

```json
{"action": "authenticate", "token": "..."}           // only needed when MORPH_AUTH_TOKEN is set
{"action": "ping"}
{"action": "set_context", "context": "music" | "presentation" | "robot_targeting" | "teach"}
{"action": "next_slide"}
{"action": "previous_slide"}
{"action": "set_volume", "value": 60}                 // integer 0..100
{"action": "volume_delta", "delta": -10}              // integer -10..10
{"action": "play_pause"}
{"action": "mouse_move", "x": 0.75, "y": 0.5}         // normalized 0..1, not pixels
{"action": "click", "button": "left" | "right"}
```

### Responses

```json
{"status": "pong", "context": "music"}                // reply to ping, includes current context
{"status": "context", "context": "presentation"}      // reply to set_context
{"status": "ok", "action": "next_slide"}              // desktop action accepted
{"status": "ok", "action": "authenticate"}            // token accepted for this connection
{"status": "error", "message": "'set_volume.value' must be between 0 and 100, got 150"}
```

When the context changes, the `context` message also goes to **every other
connected, authorized client**, so they stay in sync. A future LCD bridge, for example,
would learn about the change immediately. Send `ping` at any time to read the
current context.

### Authorization (optional)

| `MORPH_AUTH_TOKEN` | Behavior |
|---|---|
| unset | Unchanged from before: every connection may send actions right away. `authenticate` is accepted, but does nothing. |
| set | Each connection starts **unauthorized**. Until it sends `{"action":"authenticate","token":"<secret>"}`, every other message, including `ping` and `set_context`, gets `{"status":"error","message":"not authenticated: ..."}`. No action runs, the context doesn't change, and the connection receives no context broadcasts. |

- Authorization belongs to **one WebSocket connection**. Reconnecting means
  authenticating again.
- A wrong token gets `{"status":"error","message":"authentication failed: invalid token (attempt N of 3)"}`.
  A wrong token also revokes earlier success on that connection. After **3
  failures**, the server closes the connection with code 1008.
- Tokens are compared in constant time.
- **The token is never logged or echoed.** Any `"token"` value, correct or
  wrong, is logged as `<redacted>`. The configured secret is scrubbed from
  every log line and error message. The websockets library's raw frame logs
  are muted, even at `MORPH_LOG_LEVEL=DEBUG`.
- This is a shared-secret gate for a trusted LAN, not encryption. `ws://`
  traffic is plaintext, so anyone who can sniff the network could see the
  token. Treat it like a Wi-Fi password.

### Validation (strict by design)

The server rejects, with an `error` reply, and never crashes on:

- malformed JSON, non-UTF-8 data, empty messages, `NaN` or `Infinity`
- anything that is not a JSON object
- a missing, non-string or unknown `action`
- missing fields, **unexpected extra fields**, or wrong types
  (`true` is not a number, and `"60"` is not an integer)
- volume outside 0..100, deltas outside -10..10
- mouse coordinates outside 0..1. Coordinates are normalized, so the sender
  never needs to know the screen size.

Frames larger than 4 KB close the connection. The server keeps running.

## Mock vs. Real mode

| | **Mock (default)** | **Real (opt-in)** |
|---|---|---|
| Enabled by | nothing, it is the default | `MORPH_REAL_ACTIONS=1` **and** `pip install pyautogui` |
| Effect | Logs what *would* happen, e.g. `[MOCK] set volume to 60`, and records it in memory | Presses keys and moves or clicks the mouse through pyautogui |
| `next_slide` / `previous_slide` | logged | Right / Left arrow key |
| `set_volume` | simulated level | **Unavailable.** Media keys can't set an absolute level, so the client gets an error reply |
| `volume_delta` | simulated level | Volume media keys, one press per 5 points, if the platform supports them. Otherwise an error reply |
| `play_pause` | logged | Play/pause media key, if the platform supports it |
| `mouse_move` | logged | Scaled to the current screen size, kept 2 px away from the edges |
| `click` | logged | Left or right click |
| Permissions | none | macOS: grant your terminal *Accessibility* permission |

Real mode waits **0.2 s** between actions. If pyautogui is missing or fails to
load, the server logs an error and **falls back to mock mode**. If a real action
throws an error during the session, real control is **disabled** for the rest of
that session and actions continue in mock mode.

```bash
pip install pyautogui
MORPH_REAL_ACTIONS=1 python -m morph_desktop.server          # macOS / Linux
$env:MORPH_REAL_ACTIONS="1"; python -m morph_desktop.server  # Windows PowerShell
```

## ⚠️ Safety

- **Mock mode is the default.** Nothing touches your machine unless you opt in.
- Real mouse/keyboard control requires `MORPH_REAL_ACTIONS=1`. The server prints
  a large warning at startup. **Use real mode only under direct supervision.**
- **Emergency stop:** press `Ctrl+C` in the server terminal, or move the mouse
  hard into any screen corner. That triggers pyautogui's failsafe, which disables
  real control.
- The server binds to **localhost only** by default.
- **Remote access needs a token.** Set `MORPH_AUTH_TOKEN` before binding to a
  network interface. The server **refuses to start** with real actions on a
  non-localhost host and no token, because anyone on the hackathon Wi-Fi could
  then drive your mouse. Mock mode on the network without a token still runs,
  but logs a warning.
- **Browsers are refused.** Any connection that carries an `Origin` header gets
  HTTP 403. Without this, any website open on the laptop could quietly connect to
  `ws://127.0.0.1:8765` and drive your mouse.
- There is no shell execution and no system-volume scripting. The only actions
  possible are the nine listed above.

## Integration: Raspberry Pi → desktop agent

The Pi sends exactly the same JSON that the demo client sends, and
authenticates first. Minimal Python sketch for the Pi side:

```python
import asyncio, json, os
from websockets.asyncio.client import connect

async def main():
    async with connect("ws://<laptop-ip>:8765") as ws:
        await ws.send(json.dumps({"action": "authenticate", "token": os.environ["MORPH_AUTH_TOKEN"]}))
        assert json.loads(await ws.recv())["status"] == "ok", "bad MORPH_AUTH_TOKEN"
        await ws.send(json.dumps({"action": "set_context", "context": "presentation"}))
        print(await ws.recv())
        await ws.send(json.dumps({"action": "next_slide"}))   # after the user confirms on the dial/button
        print(await ws.recv())

asyncio.run(main())
```

To accept a Pi on the LAN, start the laptop agent in protected mode:
`MORPH_AUTH_TOKEN=... MORPH_HOST=0.0.0.0 python -m morph_desktop.server`. Give
the Pi the same token through its own environment, never in code or git. For
local development, keep the default `127.0.0.1` and no token.

Tips for the Pi code:
- Wait for each reply before sending the next action, and treat any
  `"status": "error"` as "don't confirm to the user".
- Messages that start with `{"status":"context", ...}` can also arrive
  unprompted when another client changes the context.
- Send `ping` right after authenticating to learn the current context.
- On reconnect, authenticate again: authorization never carries over to a
  new connection.

## Scope

In scope today: the local desktop agent, the protocol, context state, the
mock/real executors, the demo client and the tests.

Not in scope yet: Pi vision and MediaPipe, camera tracking, ESP32 firmware,
speech recognition, active-app detection, dashboards, and any cloud service.
