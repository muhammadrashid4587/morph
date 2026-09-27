# morph_nano: physical controller input (Nano over serial -> SQLite)

Reads button/joystick/rotary-encoder input from an Arduino Nano over USB
serial and logs it to a local SQLite database, so it's another input source
the MORPH agent (or Claude Code directly) can query, alongside hand-tracking
(`morph_pi`) and voice (`morph_voice`).

Two scripts, two roles:

- **`ingest.py`** runs on the Raspberry Pi with the Nano plugged into it. It
  reads the Nano's JSON status stream and forwards *changed* events over the
  network to `receive.py`.
- **`receive.py`** runs on whichever machine should own the database (your
  laptop, a Mac, wherever the MORPH agent runs). It listens for events and
  writes them to `events.db`, right next to the script.

No changes to the Nano's firmware are needed - it just streams JSON over
generic USB serial and doesn't know or care what's downstream.

## Why a direct link instead of just WiFi

Campus/enterprise WiFi networks often silently block TCP connections between
client devices (client isolation) even while allowing outbound internet
access and ICMP (ping still works). If `ingest.py` times out reaching
`receive.py` over WiFi with `<urlopen error timed out>`, that's very likely
what's happening - confirmed by `ping` succeeding while a raw TCP connection
attempt to the port hangs. The fix that worked: run a plain Ethernet cable
directly between the Pi and the machine running `receive.py`, with static
IPs on both ends (`192.168.50.1` / `192.168.50.2`), bypassing the campus
network for that link entirely.

## Setup

**1. On the machine that will run `receive.py`** (Windows or Mac):

Plug in the Ethernet cable to the Pi, then set a static IP on that
interface:

- *Windows* (PowerShell, run as Administrator):
  ```powershell
  New-NetIPAddress -InterfaceAlias "<your Ethernet adapter name>" -IPAddress 192.168.50.1 -PrefixLength 24
  ```
  Find the adapter name with `Get-NetAdapter` - look for the one with
  `Status: Up` once the cable is plugged in.

- *Mac*: System Settings -> Network -> click the Ethernet adapter ->
  **Details** -> **TCP/IP** -> "Configure IPv4": **Manually** -> IP Address
  `192.168.50.1`, Subnet Mask `255.255.255.0`, Router blank.

Then start the server (stdlib only, no pip install needed):
```sh
python3 receive.py
```

**2. On the Raspberry Pi:**

```sh
python3 -m venv .venv && .venv/bin/pip install pyserial
ip link show                                 # find the Ethernet interface, usually eth0
sudo ip addr add 192.168.50.2/24 dev eth0
sudo ip link set eth0 up
ls /dev/ttyACM* /dev/ttyUSB*                 # find the Nano's port
ping -c 4 192.168.50.1                       # confirm the link works before running ingest.py
.venv/bin/python ingest.py --port /dev/ttyUSB0 --server http://192.168.50.1:8765
```

If `/dev/ttyUSB0` (or `ttyACM0`) isn't accessible (`Permission denied`), add
your user to the `dialout` group and re-log in:
```sh
sudo usermod -aG dialout $USER
```

To confirm the Nano itself is sending data (independent of the network
hop), watch the raw serial stream directly:
```sh
.venv/bin/python -m serial.tools.miniterm /dev/ttyUSB0 115200   # Ctrl+] to exit
```

## Schema

One `events` table in `events.db`, one row per *change* (not every 50ms
tick - joystick jitter under 8 counts is ignored so this stays a
meaningful log, not a 20-rows/second dump):

```
id, ts, b1, b1n, b2, b2n, sw, swn, eb, ebn, enc, x, y, raw
```

`ts` is when the receiving machine got the event (Unix timestamp, seconds).
`*n` columns are cumulative counts since the Nano last powered on, so a
consumer can detect "count went up" even across a dropped row. `raw` is the
full JSON line, for any field not yet broken out into its own column.

## Querying

```sh
sqlite3 events.db "select * from events order by id desc limit 20;"
sqlite3 events.db "select count(*) from events where b1n > 0;"
```

## Known limitation

If `receive.py` is unreachable when an event fires (network hiccup, not
started yet), `ingest.py` prints a warning and keeps retrying on the next
change - there's no offline buffering, so an event that occurs entirely
during an outage is lost rather than queued.
