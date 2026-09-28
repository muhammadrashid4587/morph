# MORPH Integration Contract (v1)

Handoff contract between the Pi, the robot-side board, the laptop, and UX/demo owners for the
OwlHacks MVP. If code and this document disagree, fix one of them before the demo.
Protocol version: **1**.

> **Current hardware.** The robot-side board is an **Arduino Nano**, connected to the Pi by USB, with no Wi-Fi.
> It has a **Grove Rotary Angle Sensor v1.2** (the volume knob, a potentiometer), **one push button**, and a
> **joystick** (X, Y and press). It only *reports* its controls to the Pi, as text lines (§3N).
> Everything marked *(not on current hardware)*: the LED, LCD, arm, e-stop, touch pad and every
> Pi → board command. These parts are kept for later: the arm is printed and may be wired later.
> The pin wiring is being confirmed with `firmware/nano/pin_finder/`.

## 1. Project names and roles

- **Product:** MORPH, a mobile embodied interface for a computer.
- **HCI loop:** **POINT → CONFIRM → ADAPT → ACT**

| Area | Owner | Owns |
|---|---|---|
| Pi vision/HCI | _name_ | Camera, MediaPipe hand landmarks, pointing ray, target selection + confidence, state machine, serial + WebSocket clients |
| Board firmware/hardware (Arduino Nano) | _name_ | Volume knob, button, joystick readings over USB serial (§3N). LED, LCD, arm poses, e-stop, touch, heartbeat watchdog *(not on current hardware)* |
| Laptop desktop agent | _name_ | `morph_desktop` WebSocket service, validation, mock/real laptop actions |
| UX/demo/integration | _name_ | Block layout, LCD wording, calibration session, acceptance test, demo script, safety checklist |

## 2. Required MVP behavior

**Known targets (exactly three).** The blocks are **target-selection objects only**.
Selecting or confirming a block never changes the laptop context and never sends a laptop action.

LED, LCD and arm columns: *(not on current hardware)*.

| Target | LED color | LCD while confirming | Arm pose |
|---|---|---|---|
| `blue` block | blue | `TARGET: BLUE?` | `point_blue` |
| `yellow` block | yellow | `TARGET: YELLOW?` | `point_yellow` |
| `green` block | green | `TARGET: GREEN?` | `point_green` |

**Controls on current hardware.** The names used in the rest of this document map to the Nano's controls:

| Name in this contract | Physical control (Nano, §3N) |
|---|---|
| **Button A** | the push button (`btn`) |
| **Button B** | the joystick press (`jbtn`) |
| **Dial** | the joystick pushed left/right (`jx`): right = +1, left = −1 |
| **Volume knob** | the Grove Rotary Angle Sensor (`knob`), used in `music` mode |
| Touch pad | none. Touch is *(not on current hardware)* |

**Adaptive panel.** The physical controls change meaning with the current mode. The mode changes **only** through an explicit mode switch:

- Physical: **Button A long press**, which cycles `robot_targeting → presentation → music → robot_targeting`.
- Demo control: the operator presses `m` on the Pi, which does the same cycle.

| Mode | Dial | Volume knob | A short press | B short press | B long press |
|---|---|---|---|---|---|
| `robot_targeting` (default) | Cycle targets (while confirming or in manual select) | none | Confirm selected target | Cancel / back to IDLE | Manual target select |
| `presentation` | +1 → `next_slide`, −1 → `previous_slide` | none | `next_slide` | Back to IDLE (`robot_targeting`) | none |
| `music` | none (the knob sets the volume) | `volume_delta` (see §3C) | `play_pause` | Back to IDLE (`robot_targeting`) | none |
| `teach` | Deferred (stretch). Not in the mode cycle | none | none | none | none |

**Minimum demo flow:**

1. The user points at a colored block.
2. The Pi identifies the target with a confidence score. It locks only after 12 consecutive clear frames on the same target (clear = not ambiguous and confidence ≥ 0.65); any no-target, ambiguous, or different-target frame restarts the count.
3. MORPH physically confirms the target: LED in the target color, LCD shows the target, and the arm points if available. *(not on current hardware)*: see the open issue below.
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
The demo counts as a full pass with this fallback. Arm motion is a bonus, never a blocker. *(not on current hardware)*: this fallback
needs the LED/LCD.

**Open issue (current hardware).** With no LED, LCD or arm, nothing on the robot shows *which* target was
understood before Button A confirms. The Pi owner must choose a stand-in for CONFIRM feedback (for example the Pi's
own screen or debug viewer) until the LED/LCD or arm is wired.

## 3. Interfaces

### N. Nano → Pi (current hardware: text lines over USB serial)

> **Note:** this section is the planned format. The Nano stream actually built and merged sends **JSON**
> lines (`b1`, `b2`, `sw`, `eb`, `enc`, `x`, `y`, plus press counts); see `morph_nano/README.md`.
> Update this section to match before building on it.

- USB serial, **115200 baud, 8N1**, ASCII. Each line ends with `\n`; the Pi also accepts `\r\n`.
- **One-way.** The Pi sends nothing to the Nano.
- **Boot line:** `MORPH NANO 1`, where `1` is the protocol version. The Nano resets when the Pi opens the port, so
  the Pi waits up to 3 s for this line.
- **Reading line,** sent every **50 ms**:

  ```
  knob=512 jx=510 jy=498 btn=0 jbtn=0
  ```

  | Key | Meaning | Values |
  |---|---|---|
  | `knob` | Volume knob (Grove Rotary Angle Sensor) | 0–1023 |
  | `jx`, `jy` | Joystick X / Y (rest ≈ middle) | 0–1023 |
  | `btn` | Button A (push button) | `1` pressed, `0` released |
  | `jbtn` | Button B (joystick press) | `1` pressed, `0` released |

  The pairs are `key=value`, separated by single spaces. The Pi ignores unknown keys, so fields can be added later
  (for example `pot=` if a second potentiometer is fitted), and it ignores any line it can't parse.
- **Buttons** are debounced on the Nano. A press that starts and ends between two lines is still reported as `1`
  in the next line, so taps are never lost.
- **Link:** no valid reading line for **1 s** → the Pi treats the board as disconnected (see §4 global rules).
- **What the Pi derives from the lines:**
  - **Short and long presses** come from `btn`/`jbtn` changes. The button semantics below apply unchanged.
  - **Dial steps** come from `jx`. Pushing past about 60% of the travel from rest gives one step (right = +1), and
    holding auto-repeats. The threshold and repeat rate are Pi-side settings; confirm the left/right direction at
    bring-up.
  - **The volume** comes from `knob` (§3C).
  - `jy` is reported but not used yet.
- **Pins:** to be confirmed with `firmware/nano/pin_finder/` (§7).

### JSON board protocol *(not on current hardware)*

The rest of §3 up to §3C describes the JSON protocol for a board with the LED, LCD, arm and e-stop. It is kept for
when those parts are wired, and it is not used by the Nano.

**Serial rules (Pi ↔ board, JSON):**

- USB serial, 8N1, UTF-8.
- One compact JSON object per line, terminated by `\n`, at most 128 bytes per line.
- Unknown fields are ignored. An unknown `cmd` gets `ack ok:false`.
- The Pi ignores lines that do not start with `{` (board boot noise).
- The board sends exactly one `ack` for every `cmd` except `heartbeat`.
- For `arm_pose`, the `ack` is sent when the motion finishes or fails.

**Button semantics (Pi side; for the Nano, from `btn`/`jbtn` going 1 → 0):**

- **Short press** = `pressed` followed by `released`, with no `long_press` in between. The Pi acts on the `released` event.
- **Long press** = the `long_press` event (held ≥ 1 s). The Pi acts on it right away and ignores the `released` that follows.

### A. Pi → board (newline-delimited JSON) *(not on current hardware)*

| Message | Dir | When sent | Req/Opt | Expected response / effect | Owner (send / handle) |
|---|---|---|---|---|---|
| `{"cmd":"hello","version":1}` | Pi→board | Serial open, and every 1 s until board `hello` arrives | Required | board replies `hello` with capabilities | Pi / board |
| `{"cmd":"set_mode","mode":"robot_targeting"}` | Pi→board | Boot, explicit mode switch, and entering ESTOP (`idle`). Mode is one of `idle`, `robot_targeting`, `music`, `presentation`, `teach` (`teach` reserved) | Required | `ack`. board may change button/dial LEDs; input meaning stays on the Pi | Pi / board |
| `{"cmd":"set_led","color":"blue","pattern":"solid"}` | Pi→board | Every state change. Color is one of `blue`, `yellow`, `green`, `white`, `red`. Pattern is one of `solid`, `pulse`, `blink`, `off` | Required | `ack`; LED updates within 100 ms | Pi / board |
| `{"cmd":"set_lcd","line1":"TARGET: BLUE?","line2":"A:yes B:no"}` | Pi→board | Every state change | Required | `ack`; lines longer than 16 chars are truncated by the board | Pi / board |
| `{"cmd":"arm_pose","pose":"point_blue"}` | Pi→board | Enter CONFIRMING/MANUAL_FALLBACK (`point_*`), target confirmed in ACTING (`nod`), IDLE (`home`). Pose is one of `home`, `point_blue`, `point_yellow`, `point_green`, `nod` | Optional (only if `arm` in capabilities) | `ack` after motion ends; `ok:false` on limit/fault → LED/LCD fallback | Pi / board |
| `{"cmd":"stop","reason":"estop"}` | Pi→board | Pi enters ESTOP. Reason is one of `estop`, `timeout`, `manual` | Required | Motors stop immediately; `ack`. Does **not** clear an e-stop | Pi / board |
| `{"cmd":"heartbeat"}` | Pi→board | Every 250 ms, always | Required | No ack. If no line arrives for 1000 ms, the board stops motors and shows `PI LOST` | Pi / board |

### B. Board → Pi (newline-delimited JSON) *(not on current hardware)*

| Message | Dir | When sent | Req/Opt | Expected response / effect | Owner (send / handle) |
|---|---|---|---|---|---|
| `{"event":"hello","version":1,"capabilities":["led","lcd","button_a","button_b","dial","arm"]}` | board→Pi | Boot, and in reply to `cmd:hello` | Required | Pi records capabilities; `arm` missing → Pi never sends `arm_pose` | board / Pi |
| `{"event":"button","id":"a","state":"pressed"}` | board→Pi | Debounced edge. `id` is `a` or `b`. `state` is `pressed`, `released`, or `long_press` (≥ 1 s) | Required | A short = confirm target or panel action. A long = mode switch. B short = cancel / back to IDLE. B long = MANUAL_FALLBACK | board / Pi |
| `{"event":"dial","delta":1}` | board→Pi | Detents since the last event, at most every 50 ms. Clockwise is positive. `delta` is in -10..10 | Required | Pi maps it by mode (see §2 panel table) | board / Pi |
| `{"event":"touch","state":"tapped"}` | board→Pi | Touch pad tapped | Optional | Treated the same as Button A short press | board / Pi |
| `{"event":"estop","active":true}` | board→Pi | E-stop engaged (`true`) or physically reset (`false`) | Required | `true` → Pi enters ESTOP. `false` → Pi returns to IDLE | board / Pi |
| `{"event":"status","arm":"idle","safety":"ok"}` | board→Pi | Every 500 ms. `arm` is `idle`, `moving`, or `error`. `safety` is `ok` or `estop` | Required | Link-alive signal. No status for 1.5 s → Pi treats serial as down | board / Pi |
| `{"event":"ack","cmd":"set_led","ok":true,"message":""}` | board→Pi | Once per received `cmd` (not `heartbeat`) | Required | Pi logs it. 3 missing acks in a row → serial treated as down | board / Pi |

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
| `{"action":"volume_delta","delta":10}` | Pi→Laptop | `music` mode, volume knob. The Pi turns `knob` into a percentage `p = round(knob × 100 / 1023)` and remembers the last percentage it sent (reset to the current `p` on entering `music`, so there is no jump). When `p` differs from it by ≥ 2: `delta = clamp(p − last, -10, 10)`, then `last += delta`. Relative steps are used because the laptop's real mode cannot set an absolute volume | Required | `{"status":"ok","action":"volume_delta"}` | Pi / Laptop |
| `{"action":"play_pause"}` | Pi→Laptop | `music` mode: A short press | Required | `{"status":"ok","action":"play_pause"}` | Pi / Laptop |

The laptop has no network e-stop message. The Pi stops sending, and the laptop owner uses Ctrl+C or the pyautogui corner failsafe.

## 4. State machine (Pi-owned)

IDLE, TRACKING, CONFIRMING and MANUAL_FALLBACK all run in `robot_targeting` mode.
PRESENTING_CONTROL runs in `presentation` or `music` mode.

**On current hardware:** the board commands in this table (`set_mode`, `set_led`, `set_lcd`, `arm_pose`,
`stop`) are *(not on current hardware)* and are not sent. There is no e-stop input, so ESTOP cannot be reached until one is wired,
and it **must** be wired before the arm moves. There is no touch pad. A, B and the dial are the controls in §2.

| State | Entry behavior | Allowed inputs | Output commands | Exit condition | Safety behavior |
|---|---|---|---|---|---|
| **IDLE** | `set_mode robot_targeting`, `set_led white pulse`, `set_lcd "POINT AT BLOCK" "MODE:TARGETING"`, `arm_pose home` | Vision frames, A long, B long, estop | board display commands. Laptop `set_context` only on a mode switch | Pointing hand seen → TRACKING. A long → PRESENTING_CONTROL (`presentation`). B long or camera down → MANUAL_FALLBACK | No laptop actions |
| **TRACKING** | `set_led white blink` | Vision frames, estop | None | Lock (12 consecutive clear frames on the same target; any NONE, AMBIGUOUS, or target-change frame resets the count) → CONFIRMING. Hand gone ≥ 4 frames or 3 s without lock → IDLE | No laptop actions. Ambiguous frames never lock |
| **CONFIRMING** | `set_led <target> solid`, `set_lcd "TARGET: <T>?" "A:yes B:no"`, `arm_pose point_<target>` (if arm) | A short, touch tapped, B short, dial, estop, 8 s timeout | board display/arm only | A or touch → ACTING (target confirm). Dial cycles blue → yellow → green (re-point, update LED/LCD). B or 8 s timeout → IDLE | Vision is ignored (frozen). **No laptop messages.** Arm failure → LED/LCD fallback, stay in this state |
| **ACTING** | Runs exactly one action. **Target confirm:** `arm_pose nod`, `set_led <target> blink`, `set_lcd "<T> SELECTED"`, store the selected target; no laptop message. **Panel action:** send one laptop action | board `ack`, laptop reply, estop, 2 s timeout | Target confirm: board only. Panel action: exactly one laptop action | Target confirm → IDLE (still `robot_targeting`). Panel action `ok` → PRESENTING_CONTROL. Error or timeout → PRESENTING_CONTROL with `set_led red blink` + `set_lcd "LAPTOP OFFLINE"` | Buttons and dial are ignored while acting, so one input gives exactly one action. Failures are **not** queued or retried |
| **PRESENTING_CONTROL** | Entered only by an explicit mode switch. Laptop `set_context <mode>`, `set_mode <mode>`, `set_led white solid`, `set_lcd "PRESENTATION" "DIAL=SLIDE B=END"` or `"MUSIC" "KNOB=VOL A=PLAY"` | Dial, volume knob, A short, A long, B short, estop, laptop `context` broadcast | presentation: dial > 0 → ACTING(`next_slide`), dial < 0 → ACTING(`previous_slide`), A → ACTING(`next_slide`). music: volume knob change → ACTING(`volume_delta`, §3C), A → ACTING(`play_pause`). ≤ 1 action per 250 ms | A long → next mode (presentation → music → IDLE). B short → IDLE, with laptop `set_context robot_targeting` | Vision is ignored. One laptop action per input event. No timeout, so the presenter keeps control |
| **ESTOP** | `stop reason:estop`, `set_mode idle`, `set_led red solid`, `set_lcd "E-STOP" "RESET ON ROBOT"` | Only `estop active:false` | None to laptop | board `estop active:false` (physical reset only) → IDLE | **Everything else is ignored.** The Pi cannot clear an e-stop |
| **MANUAL_FALLBACK** | `set_lcd "SELECT: BLUE" "DIAL=NEXT A=OK"`, `set_led <selected> solid`, `arm_pose point_<selected>` (if arm) | Dial, A short, B short, estop | board display/arm only | Dial cycles blue → yellow → green. A → ACTING (target confirm). B → IDLE | Same confirmation rule: nothing runs until A. No laptop messages |

**Global rules:**

- `estop active:true` → ESTOP from every state.
- Mode switches happen only on A long press or the demo control `m`. `teach` is not in the mode cycle.
- Serial down (for the Nano: no valid reading line for 1 s) → stay in IDLE and show the error in the Pi log/overlay; no confirmations or panel actions are possible.
- Laptop down → panel actions fail safely, as described in the ACTING row. Target selection keeps working.

## 5. Exact first acceptance test (60 s, no driving/grabbing)

**On current hardware:** the e-stop steps (t = 36–50), the LED/LCD/arm pass conditions and the arm fallback
variant are *(not on current hardware)* and cannot be run yet. Button A/B and the dial are the Nano controls in §2.

**Setup:**

- Laptop: `python -m morph_desktop.server` (mock mode by default; real mode only for the final rehearsal, with slides open).
- Pi: connected to the board (Arduino Nano) over USB and to the laptop over WebSocket.
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
| 50 | Physical e-stop reset | board sends `estop active:false`; MORPH returns to IDLE |
| 60 | End | The laptop log holds only these, in order (plus `ping`): `set_context robot_targeting`, `set_context presentation`, `next_slide`, `set_context robot_targeting` |

**Manual fallback variants (must also pass):**

- **Camera fails:** hold B (long_press) → MANUAL_FALLBACK. Dial to `BLUE`, short-press A → `BLUE SELECTED`, with no laptop action. The rest of the run (mode switch, dial, B, e-stop) is unchanged.
- **Arm fails or is absent:** the same run passes with LED blue + LCD `TARGET: BLUE?` as the physical confirmation.

## 6. Milestones

| # | Milestone | Owner | Depends on | Exact done test |
|---|---|---|---|---|
| M1 | Desktop mock actions | Laptop | none | `python -m morph_desktop.server` + `python -m morph_desktop.demo_client` logs every action in §3C in mock mode; `pytest` green |
| M2 | Pi simulated point selection | Pi | none | Replaying a recorded or synthetic landmark clip prints lock `blue` / `yellow` / `green` correctly, and prints no lock for an ambiguous clip |
| M3 | Nano control readings | Board | none | The pin finder confirms every control's pin (§7). Then, in a serial monitor, the Nano prints `MORPH NANO 1` at boot and §3N lines every 50 ms: `knob` sweeps 0–1023, `jx`/`jy` follow the stick, and `btn`/`jbtn` read `1` while held. (LED/LCD/arm/heartbeat checks: *(not on current hardware)*) |
| M4 | Pi ↔ Nano serial integration | Pi + Board | M2, M3 | The Pi reads `MORPH NANO 1` and §3N lines. Button short → confirm, long → mode `presentation`. Joystick right in `presentation` → one dial step. Knob in `music` → `volume_delta`. Unplugging the Nano → the Pi reports "disconnected" within 1 s. (LED/LCD/arm/e-stop checks: *(not on current hardware)*) |
| M5 | Pi ↔ laptop WebSocket integration | Pi + Laptop | M1, M2 | A simulated blue confirm sends **no** laptop message. A simulated mode switch + dial +1 → laptop logs `set_context presentation` then exactly one `next_slide`. Killing the server gives a safe ACTING failure; restarting reconnects (re-authenticating if a token is set) |
| M6 | Full Point → Confirm → Adapt → Act demo | UX/demo (all) | M4, M5 | The §5 acceptance test passes 3 runs in a row, including both fallback variants |

## 7. Hardware checklist (hardware owner fills in)

**Arduino Nano (current hardware).** Find each pin with `firmware/nano/pin_finder/` (see `firmware/nano/README.md`).

| Item | Value |
|---|---|
| Nano: genuine (FTDI `0403:6001`) or clone (CH340 `1a86:7523`)? `lsusb` | |
| Upload setting that works: `ATmega328P` or `ATmega328P (Old Bootloader)` | |
| Nano USB port on the Pi (e.g. `/dev/ttyUSB0`, `/dev/ttyACM0`) | |
| Baud rate | 115200 (fixed by the firmware) |
| Volume knob (Grove Rotary Angle Sensor v1.2) analog pin | |
| Joystick X pin / Y pin; which way is +1 (right)? | |
| Joystick press pin; reads 0 or 1 when pressed? | |
| Push button pin; wired to GND (reads 0 when pressed)? | |
| Second potentiometer present? (not assumed) Pin? | |

Board items *(not on current hardware)*:

| Item | Value |
|---|---|
| LCD type / I2C address / size (e.g. 16×2 @ 0x27) | |
| LED type / pin / count | |
| Touch pad pin (optional) | |
| Arm available for demo? (yes/no) | |
| Arm poses tuned: `home` / `point_blue` / `point_yellow` / `point_green` / `nod` | |
| E-stop mechanism (hardware power cut vs GPIO), reset method | |
| Motor supply voltage; separate from Pi supply? | |
| Common ground between the board, motors, and Pi confirmed? | |

## 8. Demo safety checklist

- [ ] Robot starts in the `home` pose, and returns home on IDLE.
- [ ] E-stop stops all motors locally on the board, without waiting for the Pi. *(not on current hardware)*: required before the arm is wired.
- [ ] The heartbeat watchdog (1000 ms) stops motors if the Pi goes silent. *(not on current hardware)*: required before the arm is wired.
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
