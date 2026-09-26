# MORPH

**MORPH** is a mobile, embodied interface for a computer. Instead of fixed menus
and keyboard shortcuts, you *point*. MORPH works out what you meant, *physically
confirms* it by pointing back with its robot arm, and *adapts* its physical
controls (dial, buttons, LCD, LEDs) to the task at hand. Then it *acts*. The
same dial adjusts volume while music plays, flips slides during a presentation,
selects targets when steering the robot, and records gestures in teach mode.
The loop is **POINT → CONFIRM → ADAPT → ACT**. MORPH is an OwlHacks HCI project.

This repository holds two building blocks so far:

- **`morph_desktop`**: a small, local, strictly validated service that turns
  JSON actions into laptop actions. It runs in **safe mock mode by default**.
- **`morph_pi`**: the Pi-side **POINT** logic. It turns an index-finger
  pointing ray into "which block did the user mean?". It is pure,
  deterministic Python and needs no camera or hardware. See
  [POINT target selection](#point-target-selection-morph_pi).

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

So far this repo has `morph_desktop` and the pure-logic core of the Pi side
(`morph_pi`). Camera capture, serial and ESP32 code will come later.

| `morph_desktop` module | Responsibility |
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

## POINT target selection (`morph_pi`)

`morph_pi` implements the first step of **POINT → CONFIRM → ADAPT → ACT**:
the user points at one of three known blocks on the table, and MORPH decides
which one they mean. It outputs clean semantic results only. A later module
will use a STABLE result to make the robot point back for confirmation.
`morph_pi` itself never controls hardware.

### Pipeline

```
hand landmarks ──▶ pointing ray ──▶ select_target() ──▶ TemporalSmoother ──▶ SelectionResult
 (future camera     landmark 5 →      one frame:           12-frame streak,     state, target,
  + MediaPipe)      landmark 8        NONE / CANDIDATE /   emits STABLE once    confidence, reason
                                      AMBIGUOUS
```

| `morph_pi` module | Responsibility |
|---|---|
| `models.py` | Validated dataclasses and enums: `Point2D`, `Ray2D`, `TargetId`, `Target`, `Candidate`, `SelectionState`, `SelectionResult`, `TargetConfig` |
| `geometry.py` | Pure ray math: direction, forward projection, distance to the ray, behind and degenerate checks, `ray_from_hand_landmarks` |
| `selection.py` | Per-frame geometric selection and confidence. It **never** returns STABLE |
| `smoothing.py` | `TemporalSmoother`: turns a streak of frames into one STABLE event |
| `targets.py` | Default targets, JSON config load/save/validation |
| `simulate.py` | Deterministic scenarios with built-in expectations |
| `app.py` | CLI |
| `config.py` | Default constants |
| `hand_tracking.py` | Pure helpers for live landmarks: `HandObservation`, `ray_from_landmarks`, mirroring |
| `live_point.py` | Live pipeline: landmarks → ray → `select_target` → one per-session `TemporalSmoother` |
| `camera_debug.py` | Optional live webcam viewer (OpenCV + MediaPipe, imported lazily) |

### Coordinates and targets

All coordinates are **normalized image coordinates**: `x` runs from 0 on the
left to 1 on the right, and `y` from 0 at the top to 1 at the bottom (the
MediaPipe convention). Every coordinate must be finite and within
`[0.0, 1.0]`; anything else raises `ValueError`.

The pointing ray starts at the **index MCP (landmark 5)** and passes through
the **index fingertip (landmark 8)**.

| Target | `TargetId` | Default center |
|---|---|---|
| Blue block | `BLUE_BLOCK` | x=0.20, y=0.62 |
| Yellow block | `YELLOW_BLOCK` | x=0.50, y=0.62 |
| Green block | `GREEN_BLOCK` | x=0.80, y=0.62 |

### Rules

Per frame (`select_target`):

1. The ray is **degenerate** if landmark 5 → landmark 8 is shorter than
   `min_ray_length` (0.01). That gives `NONE`.
2. A target is valid only if it is **ahead of the ray origin** and its distance
   to the ray is **≤ 0.12**. Distance is perpendicular to the ray, never
   measured behind the hand. If no target is valid, the result is `NONE`.
3. The **best** candidate has the lowest distance. Exact ties resolve in the
   order blue, yellow, green.
4. Confidence is a number in [0, 1], computed deterministically:
   `confidence = 0.5 · closeness + 0.5 · separation`
   - `closeness = 1 − d_best / 0.12`
   - `separation = (d_second − d_best) / 0.12`, or 1 when no other target is valid
5. The result is **`AMBIGUOUS`** if `d_second − d_best < 0.035` **or**
   `confidence < 0.65`. Both comparisons are strict. Otherwise it is
   **`CANDIDATE`**.

Over time (`TemporalSmoother`):

6. The same `CANDIDATE` target for **12 consecutive frames** becomes
   **`STABLE`** on the 12th frame, with `stable_target` set.
7. `STABLE` is emitted **exactly once per sustained gesture**. While the user
   keeps pointing, later frames report `CANDIDATE`, shown as `(held)` in the
   simulation output. Consumers can simply act on `state == STABLE`.
8. A `NONE` or `AMBIGUOUS` frame, a **different** candidate, or `reset()` ends
   the gesture. The streak starts over and the same target can become
   `STABLE` again after a fresh 12-frame streak.

Every threshold can be changed in the target config.

### Run it

```bash
python -m morph_pi.app --show-targets                         # targets + thresholds
python -m morph_pi.app --simulate                             # all scenarios, PASS/FAIL each
python -m morph_pi.app --simulate --scenario blue             # one scenario
python -m morph_pi.app --simulate --scenario ambiguous -v     # with per-frame reasons
python -m morph_pi.app --save-default-targets targets.json    # editable config (add --force to overwrite)
python -m morph_pi.app --config targets.json --show-targets
python -m morph_pi.app --config targets.json --simulate
```

Scenarios: `blue`, `yellow` and `green` each point clearly at one block for
16 frames and must give STABLE exactly once, at frame 12. The others are:

| Scenario | What it does | Expected result |
|---|---|---|
| `ambiguous` | Points between blue and yellow | Never STABLE |
| `none` | Degenerate, too-short and pointing-away rays | Always NONE |
| `reacquire` | Blue for 8 frames, 3 frames of no target, then blue again | Needs a fresh 12-frame streak |
| `release` | Stable, hold, release, point again | STABLE twice |

The scenario rays are built from the loaded config's target positions, so
`--config` layouts get the same checks. Exit codes: 0 for success, 1 if a
scenario fails its expectations, 2 for a usage or config error.

```
frame=01 state=CANDIDATE target=BLUE_BLOCK confidence=0.96 streak=1
...
frame=12 state=STABLE target=BLUE_BLOCK confidence=0.96 streak=12
frame=13 state=CANDIDATE target=BLUE_BLOCK confidence=0.96 streak=13 (held)
```

### Target config JSON

Targets are keyed by id. `label`, `color` and every threshold are optional
and fall back to the defaults.

```json
{
  "targets": {
    "blue_block":   {"x": 0.2, "y": 0.62, "label": "Blue block", "color": "blue"},
    "yellow_block": {"x": 0.5, "y": 0.62, "label": "Yellow block", "color": "yellow"},
    "green_block":  {"x": 0.8, "y": 0.62, "label": "Green block", "color": "green"}
  },
  "thresholds": {
    "max_ray_distance": 0.12,
    "ambiguity_margin": 0.035,
    "min_confidence": 0.65,
    "stable_frames": 12,
    "min_ray_length": 0.01,
    "closeness_weight": 0.5
  }
}
```

The loader rejects any of the following with an error that names the field:
invalid JSON, unknown keys (so typos can't go unnoticed), duplicate keys or
target ids, unknown or missing targets, out-of-range or non-finite
coordinates, two targets at the same center, and invalid thresholds.

### Using it from code

```python
from morph_pi.geometry import ray_from_hand_landmarks
from morph_pi.selection import select_target
from morph_pi.smoothing import TemporalSmoother
from morph_pi.targets import default_config

config = default_config()
smoother = TemporalSmoother(config.stable_frames)
for landmarks in hand_landmark_frames:            # 21 Point2D per frame (future MediaPipe adapter)
    result = smoother.update(select_target(ray_from_hand_landmarks(landmarks), config))
    if result.state.name == "STABLE":
        ...                                         # future: tell the robot to point back
```

When no hand is visible, call `smoother.reset()`. MediaPipe can report
landmarks slightly outside [0, 1] when a hand is partly off-frame. The future
adapter must treat those frames as "no hand" (or clamp them), because
`Point2D` rejects them.

## Live Camera Debug

A live viewer that proves hand tracking works on your laptop webcam. It
shows:
- the mirrored camera image
- one detected hand, with its 21 landmarks and connections
- a bright cyan pointing ray from the index MCP (landmark 5) through the index
  fingertip (landmark 8), extended to the image edge
- **HAND DETECTED** / **NO HAND**, and the frame rate (FPS)

**This viewer only shows the pointing ray.** It does not select blocks
yet, and it controls no hardware and sends nothing over the network.

### Setup (macOS)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r morph_pi/requirements-vision.txt
```

The vision packages (MediaPipe, plus the OpenCV build it depends on) are
**optional**. Simulations and `pytest` work without them.
`requirements-vision.txt` deliberately does **not** list `opencv-python`:
installing it next to MediaPipe's `opencv-contrib-python` gives two
conflicting `cv2` modules.

**One-time model download (required).** MediaPipe's Hand Landmarker needs a
model file, `hand_landmarker.task` (a few MB, published by Google). It is not
downloaded automatically. Fetch it once from the repo root:

```bash
mkdir -p models
curl -fL -o models/hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
```

`models/` and `*.task` are git-ignored, so the model is never committed. To
use a copy stored elsewhere, pass `--model PATH`.

### Run

```bash
python -m morph_pi.camera_debug              # camera 0
python -m morph_pi.camera_debug --camera 1   # another camera
```

- **Camera permission:** the first time, macOS may ask to allow **Camera**
  access for the app running the command (Terminal, iTerm, Cursor, VS Code).
  Allow it, then run the command again. If you denied access earlier, turn it
  on in *System Settings → Privacy & Security → Camera* and restart that app.
- **Quit:** press **Q** or **Esc** with the window focused, or close the
  window, or press **Ctrl+C** in the terminal. Each of these releases the
  camera.
- **If the ray disappears** while a hand is still detected, a yellow line
  explains why. The usual cause is part of the hand being outside the image:
  landmarks outside [0, 1] are rejected, never clamped.
- **Exit codes:** 0 normal quit, 1 camera unavailable or lost, 2 usage error,
  3 missing setup (vision packages or model file).

Detection runs on the **unmirrored** camera frame, which is the coordinate
space target selection will use. The image, landmarks and ray are mirrored
together, only for display.

### Live target selection in the viewer

The viewer feeds each frame's pointing ray through the real selection logic,
the same code and 12-frame rule as `--simulate`:

```
hand landmarks ─▶ ray_from_landmarks() ─▶ select_target(ray, config) ─▶ TemporalSmoother.update()
                  no hand / no ray ─▶ smoother.reset()
```

`live_point.PointPipeline` owns **one** `TemporalSmoother` for the whole
session. It is created once when the camera opens and reused for every
frame, which is what lets a 12-frame streak build up.

```bash
python -m morph_pi.camera_debug                           # default target layout
python -m morph_pi.camera_debug --config targets.json     # custom layout (see "Target config JSON")
```

On screen:
- **Target zones:** an ellipse per block, drawn in its color and labeled
  BLUE, YELLOW or GREEN. Each ellipse is the region within `max_ray_distance`
  (0.12) of the target, so the ray must cross it for the target to count. It
  is an ellipse because normalized x and y are scaled by the image width and
  height.
- **Status line and highlight:**

  | State | Status line | Highlight |
  |---|---|---|
  | Candidate | `CANDIDATE: BLUE 96%` | Pulsing white outline on the candidate |
  | Locked | `LOCKED: BLUE` | Solid, filled highlight |
  | Ambiguous | `AMBIGUOUS` (orange) | Orange outline on both rivals |
  | No target | `NO TARGET` | None |
  | No ray | `NO RAY` | None |
  | No hand | `NO HAND` | None |

- **Summary line** at the bottom, for example
  `state=CANDIDATE target=BLUE confidence=0.78 frames=5/12`.

**LOCKED stays on while you keep pointing.** `STABLE` is emitted on exactly
one frame, the 12th, so the viewer shows `LOCKED` from that frame until you
release. Release means no hand, no ray, an ambiguous or no-target frame, or
switching targets. On the held frames the summary line shows the smoother's
actual state: `state=CANDIDATE ... (locked)`. The terminal also prints one
`LOCKED: BLUE (confidence 0.96)` line per gesture.

**Mirroring:** target positions are in camera coordinates, like the hand
landmarks. With the default layout, BLUE (camera x = 0.20) therefore appears
on the **right** of the mirrored window. That matches the real world when the
camera faces you. To put BLUE on the left in the mirror view, swap the blue
and green `x` values in a `--config` file.

This is still visualization only: nothing is sent over serial or WebSocket,
and no hardware moves.

## Scope

In scope today:
- the local desktop agent, its protocol, context state, the mock/real
  executors, optional token auth, and the demo client
- `morph_pi` POINT selection: geometry, target config, per-frame selection,
  temporal smoothing and simulations
- an optional live webcam viewer for hand landmarks, the pointing ray, and
  live target selection (debug visualization only)
- tests for all of it

**Intentionally deferred:**
- in `morph_pi`: serial/ESP32 communication, robot actuation (motors, servos, LEDs, LCD,
  gripper), and sending results to the desktop agent over the network
- elsewhere: speech recognition, active-app detection, dashboards, and any
  cloud service
