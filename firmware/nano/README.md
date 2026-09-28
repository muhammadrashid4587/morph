# MORPH Arduino Nano

The robot-side board is an **Arduino Nano**, connected to the Pi by USB. It
reads a Grove Rotary Angle Sensor v1.2 (the volume knob), one push button and
a joystick (X, Y and press). It sends those readings to the Pi as text lines
over USB serial (contract §3N). There is no Wi-Fi, and no commands go to the
Nano.

**Status:** the wiring isn't confirmed yet, so only the pin finder exists. The
real firmware comes once the pins are known.

**Note:** the Nano → Pi stream that was actually built uses JSON lines (see
`morph_nano/README.md`), not the text format of contract §3N.

## Step 1: find the pins with `pin_finder/`

`pin_finder/pin_finder.ino` prints A0–A7 (0–1023) and D2–D12 (1/0) four times
per second at 115200 baud. Values that changed since the previous line are
marked with `*`. It only reads pins. It turns on the internal pull-ups on
D2–D12 and never drives an output.

### Option A: Arduino IDE (on the Mac, or on the Pi's desktop)
1. Plug the Nano in with a **data** USB cable. Some cables are charge-only.
2. Open `firmware/nano/pin_finder/pin_finder.ino`.
3. **Tools → Board → Arduino AVR Boards → Arduino Nano.**
4. **Tools → Processor → ATmega328P.**
5. **Tools → Port:** pick the Nano. On a Mac it's
   `/dev/cu.usbserial-…` or `/dev/cu.wchusbserial…`. On the Pi it's
   `/dev/ttyUSB0` or `/dev/ttyACM0`.
6. **Upload.** If it fails with `stk500_getsync(): not in sync` or
   `programmer is not responding`, go back to step 4, choose **ATmega328P (Old
   Bootloader)**, and upload again.
7. **Tools → Serial Monitor,** set to **115200 baud**.

### Option B: command line on the Pi (`arduino-cli`)
```bash
# one-time setup
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh   # installs ./bin/arduino-cli
export PATH="$PWD/bin:$PATH"
arduino-cli core update-index && arduino-cli core install arduino:avr
sudo usermod -aG dialout "$USER"      # then log out and back in, if you get "permission denied" on the port

# every time (from the repo root)
arduino-cli board list                # find the port, e.g. /dev/ttyUSB0
arduino-cli compile --fqbn arduino:avr:nano firmware/nano/pin_finder
arduino-cli upload  --fqbn arduino:avr:nano -p /dev/ttyUSB0 firmware/nano/pin_finder
#   "not in sync" / "not responding"? use the old bootloader instead:
#   arduino-cli upload --fqbn arduino:avr:nano:cpu=atmega328old -p /dev/ttyUSB0 firmware/nano/pin_finder
arduino-cli monitor -p /dev/ttyUSB0 -c baudrate=115200   # Ctrl+C to stop
```

Only one program can use the port at a time. Close the Serial Monitor, and
stop any MORPH Pi program, before uploading.

### Genuine or clone?
You'll know after uploading, plus one command.
- **USB chip:** plug the Nano into the Pi and run `lsusb`.
  - `0403:6001 Future Technology Devices … FT232` means an FTDI chip, as on a
    genuine Nano.
  - `1a86:7523 QinHeng Electronics … CH340` means a clone (the most common kind).
  - `dmesg | tail` shows the same thing: `FTDI USB Serial Device converter` vs
    `ch341-uart converter`.
- **Bootloader:** if the upload only works with **ATmega328P (Old
  Bootloader)**, the board has the older bootloader. That's common on clones
  and on Nanos made before 2018.
- Either kind works for MORPH. Just note which processor setting uploaded, so
  the real firmware uses the same one.

### Reading the output
```
A0=347  A1=512  A2=509  A3=498  A4=402  A5=388  A6=371  A7=366  | D2=1  D3=1  D4=1  D5=1  D6=1  ...
A0=347  A1=880* A2=509  A3=498  A4=415  A5=390  A6=362  A7=370  | D2=1  D3=1  D4=1  D5=1  D6=0* ...
```
- **Knob:** turn it fully one way, then the other. Its pin sweeps smoothly
  between roughly 0 and 1023.
- **Joystick:** push it left and right. One analog pin swings (that's the X
  axis). Push it up and down for the other pin (the Y axis). At rest, both sit
  near the middle (~500).
- **Joystick press** and **button:** press and hold each one. Its pin goes
  from `1` to `0` (or `0` to `1`) while held.
- **Unconnected analog pins drift** by themselves, sometimes with a `*`.
  Ignore any pin that doesn't follow your hand.

Send back the result as a list, e.g. `knob=A0, joystick X=A1, Y=A2, joystick
press=D2 (0 when pressed), button=D3 (0 when pressed)`, plus which processor
setting uploaded.
