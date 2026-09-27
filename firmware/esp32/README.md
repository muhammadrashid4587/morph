# MORPH ESP32 firmware

Firmware for the MORPH robot's ESP32. It handles the buttons, touch pad, dial
(rotary encoder or joystick), LED, LCD, e-stop, and the arm motors (servos
and/or steppers), plus holding a brushless ESC safely at stop. It talks to the
Raspberry Pi over USB serial using **exactly** the protocol in
[`docs/MORPH_INTEGRATION_CONTRACT.md`](../../docs/MORPH_INTEGRATION_CONTRACT.md) §3.

**Status:**
- **No hardware is confirmed yet, so every device is disabled.** Each device
  enables itself once Sahil's values are filled into
  [`include/hardware_config.h`](include/hardware_config.h).
- Not flashed and not tested on a real board.
- The code compiles for the ESP32, and the logic is tested on a PC.

## Safety rules (enforced in code and covered by tests)

- **Motors are OFF:**
  - at boot, until the Pi has spoken
  - on heartbeat loss (no valid line for 1000 ms): LCD shows `PI LOST`
  - on **any** invalid command or malformed line
  - on `stop`
  - while the e-stop is engaged
- **The e-stop latches.** It engages on the first active sample. It clears
  only after a *physical* reset: the switch has been released continuously for
  500 ms, and, for a momentary button, a separate reset input has been pressed.
  Nothing sent over serial can clear it.
- **No motor can ever be enabled unless an e-stop input is configured.**
- After an e-stop or a lost Pi, motors stay off until the Pi sends a new
  `arm_pose`.
- **Brushless motor:** the contract has no command to spin it, so the ESC only
  ever receives its stop pulse, and only once its pin and pulse values are known.
- **Every motion is limited:** servos by angle limits and a degrees-per-second
  rate limit, steppers by travel limits and acceleration/speed limits.
- A pose that doesn't finish within 8 s fails and powers the arm off.

**Electrical:**
- **Never power a servo, brushless motor or stepper from an ESP32 pin.** ESP32
  pins carry 3.3 V control signals only.
- Brushless motors must go through a suitable ESC, and steppers through their
  drivers. Both need their own power supply, with its ground shared with the
  ESP32.
- Keep every input at 3.3 V or below. For example, power the joystick module
  from 3.3 V, not 5 V.

## Layout

```
firmware/esp32/
  include/hardware_config.h   pin map + device parameters (all UNKNOWN today)
  lib/morph_core/src/morph/   device-independent core (plain C++17, tested on a PC)
    protocol   contract §3 commands/events      json      strict JSON parser/writer
    controller safety rules + contract behavior  safety    latched e-stop, heartbeat
    inputs     debounce, buttons, touch, joystick, encoder, dial rate limit
    outputs    LED patterns, 16-char LCD lines   motion    servo / stepper / ESC math
    arm        poses over servo/stepper joints   config    what is enabled
    line_buffer serial framing
  src/                        ESP32 (Arduino) layer: board_config, board (pins), main
  test/host/                  unit tests for the core and board config
  tools/morph_sim.cpp         serial simulator (type contract lines, see replies)
```

## Commands

Run all of these from `firmware/esp32/`.

```bash
# 1. Unit tests on your Mac (no board needed). Uses address/UB sanitizers.
make test

# 2. Simulator: talk to the firmware like the Pi does.
make sim
./build/morph_sim                  # the real hardware_config.h (nothing enabled)
./build/morph_sim --demo-config    # TEST VALUES: e-stop, buttons, dial and a demo arm enabled
#    Type contract lines, e.g. {"cmd":"arm_pose","pose":"point_blue"}, plus !press a,
#    !release a, !touch, !dial 3, !estop on, !estop off, !wait 500, !quiet (Pi goes silent).

# 3. Compile for the ESP32 (no upload). Needs PlatformIO once: pip install platformio
pio run -e esp32dev
```

From the repo root, `pytest` also builds the simulator. It then checks every
line the firmware sends, with Python's JSON parser, against the contract's
event schemas (`tests/test_firmware_contract.py`).

**Do not run `pio run -t upload` yet.** Wait until the hardware checklist
below is answered and someone is at the bench with the motor power switched
off for the first boot.

## Enabling a device

1. Fill in its fields in `include/hardware_config.h`, using values confirmed by
   the hardware team.
2. Run `pio run -e esp32dev`, then flash. At boot, the serial monitor lists
   every device that is still disabled and why (`# disabled: ...` lines; the
   Pi ignores lines that don't start with `{`).
3. **Joystick or capacitive touch calibration:** set the pin first. While the
   device is still disabled, the firmware prints raw readings every 500 ms
   (`# calibrate joystick_x_raw=...`, `# calibrate touch_raw=...`). Use them to
   fill in the min/center/max or the threshold.
4. **Arm joints:** add one `JointSpec` per joint to `ARM_JOINTS`. **At power-on
   each joint is assumed to be at its `home` pose.** Hobby servos and steppers
   without homing switches can't know where they are, so place the arm at home
   before powering the motors.

**Not implemented yet, on purpose:**
- **LED and LCD drivers:** their types are unknown. Until then, `set_led` and
  `set_lcd` are acked with `ok:false` ("led not configured" / "lcd not
  configured").
- **Stepper homing:** there's no information about limit switches.
- **A spin command for the brushless motor:** it isn't in the contract.

## Contract interpretations

These are places where the contract is open to reading, and what this
firmware does:

- **`hello`:** answered with the `hello` event *and* an `ack`, because §3A says
  every command except `heartbeat` gets exactly one ack. If the version isn't 1,
  the ack is `ok:false`.
- **`nod`:** moves to the nod pose, then back to the previous pose, and is
  acked once it's back.
- **A new `arm_pose` during a move** replaces the old one. The old one is acked
  `ok:false, "preempted"`, so each command still gets exactly one ack.
- **Heartbeat:** only well-formed JSON that has a `cmd` keeps the link alive.
  A garbage line is acked `ok:false` with `"cmd":""`.
- **Local screens:** the ESP32 shows `E-STOP` and `PI LOST` (and a red LED) on
  its own, regardless of what the Pi last asked for. Before the Pi first
  connects, it shows `WAITING FOR PI`.
- **Status `arm`:** reports `idle` when no arm is configured.

## Needed from the hardware team before wiring or live testing

Each item maps to fields in `include/hardware_config.h`.

1. **ESP32 board:** exact model (DevKit / ESP32-S3 / ...) and the USB serial
   port name on the Pi.
2. **E-stop:** latching mushroom switch or momentary button (plus a reset
   button)? Does it cut motor power directly, or is it only read by the ESP32?
   Which pin, is it wired normally-closed, and what level does the pin read
   when engaged?
3. **Buttons A and B:** pins, and whether each is wired to 3.3 V or to GND
   (i.e. the pull-up/pull-down setting and the pressed level).
4. **Touch sensor:** a digital touch module (pin, output level when touched) or
   a bare pad on an ESP32 touch pin?
5. **Dial:** is there a rotary encoder (A/B pins, pulses per detent), or should
   the joystick's X axis act as the dial? Joystick X/Y/switch pins, and is the
   module powered from 3.3 V?
6. **LED:** type (WS2812 strip, RGB LED, single-colour LEDs?), pin and count.
7. **LCD:** type and size (e.g. 16x2 with an I2C backpack), I2C address, SDA
   and SCL pins.
8. **Arm:** which joints exist, and whether each one is a servo or a stepper.
   - **Servos:** model, signal pin, safe min/max angle, pulse width at 0° and
     180°.
   - **Steppers:** driver model, STEP/DIR/EN pins, EN active level,
     microstepping, steps per revolution, safe travel range, and whether any
     joint would drop under gravity when its driver is switched off.
   - **Poses:** the measured target of every joint for `home`, `point_blue`,
     `point_yellow`, `point_green` and `nod`.
9. **Brushless motor:** what it is for, the ESC model and its signal pin,
   stop/full-throttle pulse widths and arming time from the manual, and whether
   it runs in one direction or both. It also needs a new contract message
   before it can ever spin.
10. **Power:** the supply for the servos, steppers and ESC (voltage and
    current), confirmation that it is separate from the ESP32/Pi supply, and
    that all grounds are shared.
