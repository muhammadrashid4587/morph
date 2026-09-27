"""The ESP32 firmware core, checked from Python against the integration contract.

Builds the host simulator (firmware/esp32/tools/morph_sim.cpp: the same C++ core
that runs on the ESP32), drives it like the Pi would, and validates every line
it sends with Python's own JSON parser and the contract §3B event schemas.
Needs only a C++17 compiler and make; skipped when they are missing.
No ESP32, serial port or hardware is involved.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

FIRMWARE = Path(__file__).resolve().parent.parent / "firmware" / "esp32"

pytestmark = pytest.mark.skipif(
    shutil.which("make") is None or (shutil.which("c++") is None and shutil.which("g++") is None),
    reason="needs make and a C++17 compiler",
)

# Contract §3B: exact field set and allowed values per event.
EVENT_SCHEMAS = {
    "hello": {"event", "version", "capabilities"},
    "button": {"event", "id", "state"},
    "dial": {"event", "delta"},
    "touch": {"event", "state"},
    "estop": {"event", "active"},
    "status": {"event", "arm", "safety"},
    "ack": {"event", "cmd", "ok", "message"},
}
CAPABILITIES = {"led", "lcd", "button_a", "button_b", "dial", "arm"}


@pytest.fixture(scope="module")
def sim(tmp_path_factory: pytest.TempPathFactory) -> Path:
    build = tmp_path_factory.mktemp("morph_sim_build")
    subprocess.run(["make", "-s", "sim", f"BUILD={build}"], cwd=FIRMWARE, check=True, capture_output=True)
    return build / "morph_sim"


def run_sim(sim: Path, lines: list[str], demo: bool) -> list[dict]:
    args = [str(sim)] + (["--demo-config"] if demo else [])
    proc = subprocess.run(args, input="\n".join(lines) + "\n", capture_output=True, text=True, check=True, timeout=30)
    events = []
    for raw in proc.stdout.splitlines():
        assert len(raw.encode()) + 1 <= 128, f"line over 128 bytes: {raw}"
        assert raw.startswith("{"), raw
        events.append(validate(json.loads(raw)))
    return events


def validate(event: dict) -> dict:
    kind = event.get("event")
    assert kind in EVENT_SCHEMAS, event
    assert set(event) == EVENT_SCHEMAS[kind], event
    if kind == "hello":
        assert event["version"] == 1 and set(event["capabilities"]) <= CAPABILITIES
    elif kind == "button":
        assert event["id"] in {"a", "b"} and event["state"] in {"pressed", "released", "long_press"}
    elif kind == "dial":
        assert isinstance(event["delta"], int) and -10 <= event["delta"] <= 10 and event["delta"] != 0
    elif kind == "touch":
        assert event["state"] == "tapped"
    elif kind == "estop":
        assert isinstance(event["active"], bool)
    elif kind == "status":
        assert event["arm"] in {"idle", "moving", "error"} and event["safety"] in {"ok", "estop"}
    elif kind == "ack":
        assert isinstance(event["cmd"], str) and isinstance(event["ok"], bool) and isinstance(event["message"], str)
    return event


SESSION = [
    '{"cmd":"hello","version":1}',
    '{"cmd":"set_mode","mode":"robot_targeting"}',
    '{"cmd":"set_led","color":"white","pattern":"pulse"}',
    '{"cmd":"set_lcd","line1":"POINT AT BLOCK","line2":"MODE:TARGETING"}',
    '{"cmd":"arm_pose","pose":"point_blue"}',
    "!wait 600",
    '{"cmd":"set_lcd","line1":"TARGET: BLUE?","line2":"A:yes B:no"}',
    "!press a", "!wait 1100", "!release a", "!touch", "!dial 3", "!wait 60",
    '{"cmd":"arm_pose","pose":"nod"}',
    "!wait 1000",
    "!estop on", "!wait 20",
    '{"cmd":"stop","reason":"estop"}',
    '{"cmd":"arm_pose","pose":"home"}',
    "!estop off", "!wait 600",
    '{"cmd":"dance"}',
    "not json",
    '{"cmd":"set_led","color":"purple","pattern":"solid"}',
    '{"cmd":"heartbeat"}',
]
COMMANDS = [line for line in SESSION if not line.startswith("!")]


def test_every_line_matches_the_contract_and_every_cmd_gets_one_ack(sim: Path) -> None:
    events = run_sim(sim, SESSION, demo=True)
    kinds = {e["event"] for e in events}
    assert kinds == set(EVENT_SCHEMAS)  # the session exercises every event type
    acks = [e for e in events if e["event"] == "ack"]
    non_heartbeat = [c for c in COMMANDS if '"heartbeat"' not in c]
    assert len(acks) == len(non_heartbeat)  # contract: exactly one ack per cmd except heartbeat
    by_cmd = [a["cmd"] for a in acks]
    assert by_cmd.count("arm_pose") == 3
    assert next(a for a in acks if a["cmd"] == "")["ok"] is False  # "not json" still gets its ack


def test_safety_sequence_seen_by_the_pi(sim: Path) -> None:
    events = run_sim(sim, SESSION, demo=True)
    arm_acks = [(a["ok"], a["message"]) for a in events if a["event"] == "ack" and a["cmd"] == "arm_pose"]
    # point_blue finishes; the ~1.33 s nod is still moving when the e-stop hits at 1.0 s, so it is
    # aborted with ok:false; the arm_pose sent during the e-stop is refused.
    assert arm_acks == [(True, ""), (False, "estop"), (False, "estop active")]
    estops = [e["active"] for e in events if e["event"] == "estop"]
    assert estops == [True, False]  # latched, then physically reset
    stop_ack = next(a for a in events if a["event"] == "ack" and a["cmd"] == "stop")
    assert stop_ack["ok"] is True
    buttons = [(e["id"], e["state"]) for e in events if e["event"] == "button"]
    assert buttons == [("a", "pressed"), ("a", "long_press"), ("a", "released")]
    assert [e["delta"] for e in events if e["event"] == "dial"] == [3]


def test_committed_hardware_config_advertises_nothing_and_refuses_motion(sim: Path) -> None:
    events = run_sim(sim, ['{"cmd":"hello","version":1}', '{"cmd":"arm_pose","pose":"point_blue"}'], demo=False)
    hellos = [e for e in events if e["event"] == "hello"]
    assert hellos and all(e["capabilities"] == [] for e in hellos)
    arm_ack = next(e for e in events if e["event"] == "ack" and e["cmd"] == "arm_pose")
    assert arm_ack == {"event": "ack", "cmd": "arm_pose", "ok": False, "message": "arm not available"}
