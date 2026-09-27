"""Reads the Nano's JSON status stream over USB serial and forwards changes
to the receiver server running on your machine, over the LAN (or a direct
Ethernet link if the LAN blocks device-to-device traffic - see README.md).

    python3 ingest.py --server http://192.168.50.1:8765

--server is required (no default): it's the address of the machine running
receive.py from this same directory.

Only forwards rows where something actually changed (not every 50ms tick),
and joystick jitter under JOY_THRESHOLD counts is ignored, so the log stays
a meaningful event history instead of 20 near-duplicate rows/second.

Find the Nano's port with: ls /dev/ttyACM* /dev/ttyUSB* (Pi/Linux).
Only one program can hold the port at a time.
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request

import serial

JOY_THRESHOLD = 8  # ignore joystick x/y drift smaller than this (ADC noise)


def send(server, d):
    body = json.dumps(d).encode()
    req = urllib.request.Request(
        server.rstrip("/") + "/events", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2).close()
        return True
    except (urllib.error.URLError, OSError) as e:
        print(f"Could not reach {server}: {e}", flush=True)
        return False


def changed(prev, d):
    if prev is None:
        return True
    for k in ("b1", "b1n", "b2", "b2n", "sw", "swn", "eb", "ebn", "enc"):
        if prev.get(k) != d.get(k):
            return True
    for k in ("x", "y"):
        if abs(d.get(k, 0) - prev.get(k, 0)) > JOY_THRESHOLD:
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=os.environ.get("NANO_PORT", "/dev/ttyACM0"))
    parser.add_argument("--server", required=True, help="e.g. http://192.168.50.1:8765")
    args = parser.parse_args()

    print(f"Forwarding {args.port} -> {args.server}. Ctrl+C to quit.", flush=True)
    while True:
        try:
            port = serial.Serial(args.port, 115200, timeout=1)
        except serial.SerialException:
            time.sleep(1)  # Nano unplugged or port busy: keep waiting
            continue
        print("Nano connected.", flush=True)
        try:
            run(port, args.server)
        except serial.SerialException:
            print("Nano disconnected - waiting for it to come back...", flush=True)
            port.close()


def run(port, server):
    prev = None
    counts = None  # last-seen counters, to notice a Nano restart (counters reset to 0)
    while True:
        line = port.readline().decode(errors="replace").strip()
        try:
            d = json.loads(line)
        except ValueError:
            continue  # partial line while the Nano restarts

        new_counts = {k: d.get(k, 0) for k in ("b1n", "b2n", "swn", "ebn")}
        if counts is not None and any(new_counts[k] < counts[k] for k in counts):
            prev = None  # Nano restarted: counters reset, don't log a spurious "diff"
        counts = new_counts

        if changed(prev, d):
            if send(server, d):
                prev = d
            # on failure, leave prev unchanged so the next real change still gets sent


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
