"""Receives Nano input events pushed over the LAN (or direct Ethernet link)
by ingest.py and logs them to a local SQLite database, so the data lives on
this machine for the MORPH agent (or Claude Code directly) to query.

    python3 receive.py                  # listens on 0.0.0.0:8765, db at ./events.db
    python3 receive.py --port 8765 --db events.db

No third-party packages needed - only the standard library. Find this
machine's LAN IP (`ipconfig` on Windows, System Settings > Network on Mac)
and pass http://<that IP>:8765 as --server to ingest.py.
"""

import argparse
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts  REAL NOT NULL,        -- unix time this machine received the event
    b1  INTEGER, b1n INTEGER, -- button 1: live state, press count since Nano boot
    b2  INTEGER, b2n INTEGER, -- button 2
    sw  INTEGER, swn INTEGER, -- joystick click
    eb  INTEGER, ebn INTEGER, -- encoder push
    enc INTEGER,              -- encoder position, clicks since Nano boot
    x   INTEGER, y INTEGER,   -- joystick axes
    raw TEXT NOT NULL         -- full JSON line, for fields not yet broken out above
);
"""

db_lock = threading.Lock()


def make_handler(conn):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f"{self.address_string()} - {fmt % args}", flush=True)

        def do_POST(self):
            if self.path != "/events":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                d = json.loads(self.rfile.read(length))
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            with db_lock:
                conn.execute(
                    "INSERT INTO events (ts, b1, b1n, b2, b2n, sw, swn, eb, ebn, enc, x, y, raw) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        time.time(),
                        d.get("b1"), d.get("b1n"), d.get("b2"), d.get("b2n"),
                        d.get("sw"), d.get("swn"), d.get("eb"), d.get("ebn"),
                        d.get("enc"), d.get("x"), d.get("y"),
                        json.dumps(d),
                    ),
                )
                conn.commit()
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default="events.db")
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, check_same_thread=False)
    conn.executescript(SCHEMA)

    server = ThreadingHTTPServer((args.bind, args.port), make_handler(conn))
    print(f"Listening on {args.bind}:{args.port}, logging to {args.db}. Ctrl+C to quit.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
