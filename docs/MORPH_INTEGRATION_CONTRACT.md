# MORPH Integration Contract (v1)

Handoff contract between the Pi, ESP32, laptop, and UX/demo owners for the
OwlHacks MVP. If code and this document disagree, fix one of them before the demo.
Protocol version: **1**.

## 1. Project names and roles

- **Product:** MORPH, a mobile embodied interface for a computer.
- **HCI loop:** **POINT → CONFIRM → ADAPT → ACT**

| Area | Owner | Owns |
|---|---|---|
| Pi vision/HCI | _name_ | Camera, MediaPipe hand landmarks, pointing ray, target selection + confidence, state machine, serial + WebSocket clients |
| ESP32 firmware/hardware | _name_ | LED, LCD, Button A/B, dial, touch, arm poses, e-stop, heartbeat watchdog |
| Laptop desktop agent | _name_ | `morph_desktop` WebSocket service, validation, mock/real laptop actions |
| UX/demo/integration | _name_ | Block layout, LCD wording, calibration session, acceptance test, demo script, safety checklist |

## 2. Required MVP behavior

**Known targets (exactly three).** The blocks are **target-selection objects only**.
Selecting or confirming a block never changes the laptop context and never sends a laptop action.

| Target | LED color | LCD while confirming | Arm pose |
|---|---|---|---|
| `blue` block | blue | `TARGET: BLUE?` | `point_blue` |
| `yellow` block | yellow | `TARGET: YELLOW?` | `point_yellow` |
| `green` block | green | `TARGET: GREEN?` | `point_green` |

**Adaptive panel.** The physical controls change meaning with the current mode. The mode changes **only** through an explicit mode switch:

- Physical: **Button A long press**, which cycles `robot_targeting → presentation → music → robot_targeting`.
- Demo control: the operator presses `m` on the Pi, which does the same cycle.

| Mode | Dial | A short press | B short press | B long press |
|---|---|---|---|---|
| `robot_targeting` (default) | Cycle targets (while confirming or in manual select) | Confirm selected target | Cancel / back to IDLE | Manual target select |
| `presentation` | +1 → `next_slide`, −1 → `previous_slide` | `next_slide` | Back to IDLE (`robot_targeting`) | none |
| `music` | `volume_delta` | `play_pause` | Back to IDLE (`robot_targeting`) | none |
| `teach` | Deferred (stretch). Not in the mode cycle | none | none | none |

**Minimum demo flow:**

1. The user points at a colored block.
2. The Pi identifies the target with a confidence score. It locks only when at least 10 of the last 12 frames agree and mean confidence is at least 0.5.
3. MORPH physically confirms the target: LED in the target color, LCD shows the target, and the arm points if available.
4. The user presses **Button A** to confirm, or **Button B** to cancel.
5. MORPH stays in `robot_targeting` and records the selected target. The only robot-side action in the MVP is a nod plus the LCD `BLUE SELECTED`; other robot-side actions come later.
6. A separate, explicit mode switch changes the adaptive panel (e.g. to `presentation`).
7. In that mode, the dial or Button A triggers one context-aware laptop action per input.

**Rules:**

- A target is never acted on without a Button A confirmation.
- Laptop actions come only from panel input (dial or A short press) in `presentation` or `music`, after an explicit mode switch.
- An ambiguous point (two targets too close to call) is never acted on automatically.

**Successful fallback when the arm is unavailable** (no `arm` capability, `ack ok:false`, or no ack within 2 s):
the LED shows the target color, and LCD line 1 names the target (e.g. `TARGET: BLUE?`). Button A still confirms.
The demo counts as a full pass with this fallback. Arm motion is a bonus, never a blocker.

## 3. Interfaces

**Serial rules (Pi ↔ ESP32):**

- USB serial, 8N1, UTF-8.
- One compact JSON object per line, terminated by `\n`, at most 128 bytes per line.
- Unknown fields are ignored. An unknown `cmd` gets `ack ok:false`.
- The Pi ignores lines that do not start with `{` (ESP32 boot noise).
- The ESP32 sends exactly one `ack` for every `cmd` except `heartbeat`.
- For `arm_pose`, the `ack` is sent when the motion finishes or fails.

**Button semantics (Pi side):**

- **Short press** = `pressed` followed by `released`, with no `long_press` in between. The Pi acts on the `released` event.
- **Long press** = the `long_press` event (held ≥ 1 s). The Pi acts on it right away and ignores the `released` that follows.

### A. Pi → ESP32 (newline-delimited JSON)

| Message | Dir | When sent | Req/Opt | Expected response / effect | Owner (send / handle) |
|---|---|---|---|---|---|
| `{"cmd":"hello","version":1}` | Pi→ESP | Serial open, and every 1 s until ESP32 `hello` arrives | Required | ESP32 replies `hello` with capabilities | Pi / ESP32 |
| `{"cmd":"set_mode","mode":"robot_targeting"}` | Pi→ESP | Boot, explicit mode switch, and entering ESTOP (`idle`). Mode is one of `idle`, `robot_targeting`, `music`, `presentation`, `teach` (`teach` reserved) | Required | `ack`. ESP32 may change button/dial LEDs; input meaning stays on the Pi | Pi / ESP32 |
| `{"cmd":"set_led","color":"blue","pattern":"solid"}` | Pi→ESP | Every state change. Color is one of `blue`, `yellow`, `green`, `white`, `red`. Pattern is one of `solid`, `pulse`, `blink`, `off` | Required | `ack`; LED updates within 100 ms | Pi / ESP32 |
| `{"cmd":"set_lcd","line1":"TARGET: BLUE?","line2":"A:yes B:no"}` | Pi→ESP | Every state change | Required | `ack`; lines longer than 16 chars are truncated by the ESP32 | Pi / ESP32 |
| `{"cmd":"arm_pose","pose":"point_blue"}` | Pi→ESP | Enter CONFIRMING/MANUAL_FALLBACK (`point_*`), target confirmed in ACTING (`nod`), IDLE (`home`). Pose is one of `home`, `point_blue`, `point_yellow`, `point_green`, `nod` | Optional (only if `arm` in capabilities) | `ack` after motion ends; `ok:false` on limit/fault → LED/LCD fallback | Pi / ESP32 |
| `{"cmd":"stop","reason":"estop"}` | Pi→ESP | Pi enters ESTOP. Reason is one of `estop`, `timeout`, `manual` | Required | Motors stop immediately; `ack`. Does **not** clear an e-stop | Pi / ESP32 |
| `{"cmd":"heartbeat"}` | Pi→ESP | Every 250 ms, always | Required | No ack. If no line arrives for 1000 ms, the ESP32 stops motors and shows `PI LOST` | Pi / ESP32 |

### B. ESP32 → Pi (newline-delimited JSON)

| Message | Dir | When sent | Req/Opt | Expected response / effect | Owner (send / handle) |
|---|---|---|---|---|---|
| `{"event":"hello","version":1,"capabilities":["led","lcd","button_a","button_b","dial","arm"]}` | ESP→Pi | Boot, and in reply to `cmd:hello` | Required | Pi records capabilities; `arm` missing → Pi never sends `arm_pose` | ESP32 / Pi |
| `{"event":"button","id":"a","state":"pressed"}` | ESP→Pi | Debounced edge. `id` is `a` or `b`. `state` is `pressed`, `released`, or `long_press` (≥ 1 s) | Required | A short = confirm target or panel action. A long = mode switch. B short = cancel / back to IDLE. B long = MANUAL_FALLBACK | ESP32 / Pi |
| `{"event":"dial","delta":1}` | ESP→Pi | Detents since the last event, at most every 50 ms. Clockwise is positive. `delta` is in -10..10 | Required | Pi maps it by mode (see §2 panel table) | ESP32 / Pi |
| `{"event":"touch","state":"tapped"}` | ESP→Pi | Touch pad tapped | Optional | Treated the same as Button A short press | ESP32 / Pi |
| `{"event":"estop","active":true}` | ESP→Pi | E-stop engaged (`true`) or physically reset (`false`) | Required | `true` → Pi enters ESTOP. `false` → Pi returns to IDLE | ESP32 / Pi |
| `{"event":"status","arm":"idle","safety":"ok"}` | ESP→Pi | Every 500 ms. `arm` is `idle`, `moving`, or `error`. `safety` is `ok` or `estop` | Required | Link-alive signal. No status for 1.5 s → Pi treats serial as down | ESP32 / Pi |
| `{"event":"ack","cmd":"set_led","ok":true,"message":""}` | ESP→Pi | Once per received `cmd` (not `heartbeat`) | Required | Pi logs it. 3 missing acks in a row → serial treated as down | ESP32 / Pi |

### C. Pi → Laptop (WebSocket JSON, `ws://<laptop-ip>:8765`)

The laptop is strict:

- Do not add extra fields.
- Keep one request in flight and wait for its reply before sending the next.
- Treat any `status:error` as "not done".
- `{"status":"context","context":"music"}` may also arrive unprompted.
- **No target selection or confirmation ever produces a laptop message.**

| Message | Dir | When sent | Req/Opt | Expected response / effect | Owner (send / handle) |
|---|---|---|---|---|---|
| `{"action":"authenticate","token":"..."}` | Pi→Laptop | First message on every connection, **only when `MORPH_AUTH_TOKEN` is configured** | Conditional | `{"status":"ok","action":"authenticate"}`. On failure, stop retrying (3 failures closes the connection) | Pi / Laptop |
| `{"action":"ping"}` | Pi→Laptop | After connecting, then every 5 s while idle | Required | `{"status":"pong","context":"..."}` | Pi / Laptop |
| `{"action":"set_context","context":"presentation"}` | Pi→Laptop | Only on boot/reconnect (`robot_targeting`), explicit mode switch, and B returning to IDLE (`robot_targeting`). Context is one of `music`, `presentation`, `robot_targeting`, `teach` (`teach` reserved) | Required | `{"status":"context","context":"presentation"}` | Pi / Laptop |
| `{"action":"next_slide"}` | Pi→Laptop | `presentation` mode: dial clockwise or A short press | Required | `{"status":"ok","action":"next_slide"}` | Pi / Laptop |
| `{"action":"previous_slide"}` | Pi→Laptop | `presentation` mode: dial counter-clockwise | Required | `{"status":"ok","action":"previous_slide"}` | Pi / Laptop |
| `{"action":"volume_delta","delta":10}` | Pi→Laptop | `music` mode, dial: `delta = clamp(5 × dial.delta, -10, 10)`. Allowed range is -10..10 | Required | `{"status":"ok","action":"volume_delta"}` | Pi / Laptop |
| `{"action":"play_pause"}` | Pi→Laptop | `music` mode: A short press | Required | `{"status":"ok","action":"play_pause"}` | Pi / Laptop |

The laptop has no network e-stop message. The Pi stops sending, and the laptop owner uses Ctrl+C or the pyautogui corner failsafe.

## 4. State machine (Pi-owned)

IDLE, TRACKING, CONFIRMING and MANUAL_FALLBACK all run in `robot_targeting` mode.
PRESENTING_CONTROL runs in `presentation` or `music` mode.

| State | Entry behavior | Allowed inputs | Output commands | Exit condition | Safety behavior |
|---|---|---|---|---|---|
| **IDLE** | `set_mode robot_targeting`, `set_led white pulse`, `set_lcd "POINT AT BLOCK" "MODE:TARGETING"`, `arm_pose home` | Vision frames, A long, B long, estop | ESP32 display commands. Laptop `set_context` only on a mode switch | Pointing hand seen → TRACKING. A long → PRESENTING_CONTROL (`presentation`). B long or camera down → MANUAL_FALLBACK | No laptop actions |
| **TRACKING** | `set_led white blink` | Vision frames, estop | None | Lock (10/12 frames, conf ≥ 0.5) → CONFIRMING. Hand gone ≥ 4 frames or 3 s without lock → IDLE | No laptop actions. Ambiguous frames never lock |
| **CONFIRMING** | `set_led <target> solid`, `set_lcd "TARGET: <T>?" "A:yes B:no"`, `arm_pose point_<target>` (if arm) | A short, touch tapped, B short, dial, estop, 8 s timeout | ESP32 display/arm only | A or touch → ACTING (target confirm). Dial cycles blue → yellow → green (re-point, update LED/LCD). B or 8 s timeout → IDLE | Vision is ignored (frozen). **No laptop messages.** Arm failure → LED/LCD fallback, stay in this state |
| **ACTING** | Runs exactly one action. **Target confirm:** `arm_pose nod`, `set_led <target> blink`, `set_lcd "<T> SELECTED"`, store the selected target; no laptop message. **Panel action:** send one laptop action | ESP32 `ack`, laptop reply, estop, 2 s timeout | Target confirm: ESP32 only. Panel action: exactly one laptop action | Target confirm → IDLE (still `robot_targeting`). Panel action `ok` → PRESENTING_CONTROL. Error or timeout → PRESENTING_CONTROL with `set_led red blink` + `set_lcd "LAPTOP OFFLINE"` | Buttons and dial are ignored while acting, so one input gives exactly one action. Failures are **not** queued or retried |
| **PRESENTING_CONTROL** | Entered only by an explicit mode switch. Laptop `set_context <mode>`, `set_mode <mode>`, `set_led white solid`, `set_lcd "PRESENTATION" "DIAL=SLIDE B=END"` or `"MUSIC" "DIAL=VOL A=PLAY"` | Dial, A short, A long, B short, estop, laptop `context` broadcast | presentation: dial > 0 → ACTING(`next_slide`), dial < 0 → ACTING(`previous_slide`), A → ACTING(`next_slide`). music: dial → ACTING(`volume_delta`), A → ACTING(`play_pause`). ≤ 1 action per 250 ms | A long → next mode (presentation → music → IDLE). B short → IDLE, with laptop `set_context robot_targeting` | Vision is ignored. One laptop action per input event. No timeout, so the presenter keeps control |
| **ESTOP** | `stop reason:estop`, `set_mode idle`, `set_led red solid`, `set_lcd "E-STOP" "RESET ON ROBOT"` | Only `estop active:false` | None to laptop | ESP32 `estop active:false` (physical reset only) → IDLE | **Everything else is ignored.** The Pi cannot clear an e-stop |
| **MANUAL_FALLBACK** | `set_lcd "SELECT: BLUE" "DIAL=NEXT A=OK"`, `set_led <selected> solid`, `arm_pose point_<selected>` (if arm) | Dial, A short, B short, estop | ESP32 display/arm only | Dial cycles blue → yellow → green. A → ACTING (target confirm). B → IDLE | Same confirmation rule: nothing runs until A. No laptop messages |

**Global rules:**

- `estop active:true` → ESTOP from every state.
- Mode switches happen only on A long press or the demo control `m`. `teach` is not in the mode cycle.
- Serial down → stay in IDLE and show the error in the Pi log/overlay; no confirmations or panel actions are possible.
- Laptop down → panel actions fail safely, as described in the ACTING row. Target selection keeps working.

## 5. Exact first acceptance test (60 s, no driving/grabbing)

**Setup:**

- Laptop: `python -m morph_desktop.server` (mock mode by default; real mode only for the final rehearsal, with slides open).
- Pi: connected to the ESP32 over USB and to the laptop over WebSocket.
- Blocks placed as calibrated. Arm in `home`.

| t (s) | Action | Pass condition |
|---|---|---|
| 0 | Start. Hand down | LED white pulse; LCD `POINT AT BLOCK` / `MODE:TARGETING`; arm at home; laptop log `set_context robot_targeting` |
| 5 | Point at **blue** block, hold ~1 s | Within 1.5 s: LED blue solid, LCD `TARGET: BLUE?`, arm `point_blue` (or LED/LCD fallback) |
| 12 | Short-press **Button A** | Arm nods (if arm), LED blue blink, LCD `BLUE SELECTED`. **No** laptop action; the context stays `robot_targeting` |
| 18 | Explicit mode switch: hold **Button A** ≥ 1 s (or the operator presses `m`) | Laptop log `set_context presentation`; LCD `PRESENTATION` / `DIAL=SLIDE B=END` |
| 24 | Turn dial **one detent clockwise** | Exactly one `next_slide` in the laptop log; the slide advances (real mode) |
| 30 | Short-press **Button B** | Back to IDLE: laptop log `set_context robot_targeting`; LED white pulse; arm home; no slide action |
| 36 | Point at yellow, then press **e-stop** before A | Motors stop; LED red; LCD `E-STOP` |
| 42 | Press A, turn dial | **No** laptop messages; no motion |
| 50 | Physical e-stop reset | ESP32 sends `estop active:false`; MORPH returns to IDLE |
| 60 | End | The laptop log holds only these, in order (plus `ping`): `set_context robot_targeting`, `set_context presentation`, `next_slide`, `set_context robot_targeting` |

**Manual fallback variants (must also pass):**

- **Camera fails:** hold B (long_press) → MANUAL_FALLBACK. Dial to `BLUE`, short-press A → `BLUE SELECTED`, with no laptop action. The rest of the run (mode switch, dial, B, e-stop) is unchanged.
- **Arm fails or is absent:** the same run passes with LED blue + LCD `TARGET: BLUE?` as the physical confirmation.

## 6. Milestones

| # | Milestone | Owner | Depends on | Exact done test |
|---|---|---|---|---|
| M1 | Desktop mock actions | Laptop | none | `python -m morph_desktop.server` + `python -m morph_desktop.demo_client` logs every action in §3C in mock mode; `pytest` green |
| M2 | Pi simulated point selection | Pi | none | Replaying a recorded or synthetic landmark clip prints lock `blue` / `yellow` / `green` correctly, and prints no lock for an ambiguous clip |
| M3 | ESP32 LED/LCD/button mock | ESP32 | none | From a serial monitor, typing every §3A line gives the right LED/LCD/arm effect and an `ack`. Pressing A/B (short and long) and turning the dial prints the §3B events. Stopping heartbeats for 1 s stops motors |
| M4 | Pi ↔ ESP32 serial integration | Pi + ESP32 | M2, M3 | Pi receives `hello`. Simulated blue lock → blue LED + LCD `TARGET: BLUE?`. A short → `BLUE SELECTED`. A long → mode `presentation`. E-stop → ESTOP → physical reset → IDLE |
| M5 | Pi ↔ laptop WebSocket integration | Pi + Laptop | M1, M2 | A simulated blue confirm sends **no** laptop message. A simulated mode switch + dial +1 → laptop logs `set_context presentation` then exactly one `next_slide`. Killing the server gives a safe ACTING failure; restarting reconnects (re-authenticating if a token is set) |
| M6 | Full Point → Confirm → Adapt → Act demo | UX/demo (all) | M4, M5 | The §5 acceptance test passes 3 runs in a row, including both fallback variants |

## 7. Hardware checklist (hardware owner fills in)

| Item | Value |
|---|---|
| ESP32 USB port/device name (e.g. `/dev/ttyUSB0`, `/dev/ttyACM0`) | |
| Baud rate (proposed 115200) | |
| Does the ESP32 reset when the port opens (DTR/RTS)? | |
| LCD type / I2C address / size (e.g. 16×2 @ 0x27) | |
| LED type / pin / count (e.g. WS2812 on GPIO __) | |
| Button A pin, Button B pin, pull-up or pull-down, active level | |
| Dial/encoder pins, detents per turn, clockwise = positive confirmed? | |
| Dial push-switch present? Pin? | |
| Touch pad pin (optional) | |
| Arm available for demo? (yes/no) | |
| Arm poses tuned: `home` / `point_blue` / `point_yellow` / `point_green` / `nod` | |
| E-stop mechanism (hardware power cut vs GPIO), reset method | |
| Motor supply voltage; separate from Pi supply? | |
| Common ground between ESP32, motors, and Pi confirmed? | |

## 8. Demo safety checklist

- [ ] Robot starts in the `home` pose, and returns home on IDLE.
- [ ] E-stop stops all motors locally on the ESP32, without waiting for the Pi.
- [ ] The heartbeat watchdog (1000 ms) stops motors if the Pi goes silent.
- [ ] The laptop agent runs in **mock mode by default**. Real mode (`MORPH_REAL_ACTIONS=1`) is used only under supervision.
- [ ] Only colored foam or lightweight blocks are used as targets.
- [ ] The arm has clear space, and nobody puts hands near the arm while it moves.
- [ ] No target is acted on without Button A. No laptop action runs without an explicit mode switch plus a dial/Button A input.
- [ ] The manual fallback (B long_press → dial → A) has been rehearsed.
- [ ] If the agent is on the LAN, `MORPH_AUTH_TOKEN` is set, and the token is never in git.

## 9. Deferred features (explicitly out of MVP)

These are stretch goals only. None of them may block the demo:

- Drive base / navigation
- Gripper / object pickup
- Robot-side actions on a confirmed target (beyond nod + LCD)
- `teach` mode panel (record/save), wrist-IMU Teach mode and full gesture learning
- Voice input
- Generic vision / object recognition
- Screen pointing / mouse control (`mouse_move`, `click`, homography)
- Arbitrary smart-home device control
- Web dashboard
- LLM / agent behavior
- Multiple users / multiple hands
