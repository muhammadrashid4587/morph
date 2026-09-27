"""MediaPipe Hand Landmarker crash diagnostic for the Raspberry Pi (standalone).

    python tools/mediapipe_pi_check.py [path/to/hand_landmarker.task]

Runs every step in its own child process, so a crash ("Illegal instruction",
SIGILL) in one step is reported instead of killing the whole check. It first
loads the model directly with MediaPipe (no MORPH code), then with MORPH's
settings, then through MORPH itself, and prints a verdict: code fix vs package.
No camera is opened. Nothing is installed or downloaded.
"""

import importlib.metadata as md
import os
import platform
import signal
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODEL = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "models" / "hand_landmarker.task"

# Shared by every child step: find the Hand Landmarker API (Pi layout first here).
PRELUDE = """
import os, sys
os.environ.setdefault("GLOG_minloglevel", "2")
import mediapipe as mp
def api():
    t = getattr(mp, "tasks", None)
    v = getattr(t, "vision", None)
    if v is not None and hasattr(v, "HandLandmarker"):
        return (getattr(t, "BaseOptions", None) or v.BaseOptions), v, "mp.tasks.vision"
    from mediapipe.tasks.python import BaseOptions, vision
    return BaseOptions, vision, "mediapipe.tasks.python.vision"
MODEL = sys.argv[1]
def landmarker(mode, delegate="default", buffer=False):
    B, v, where = api()
    kw = {"model_asset_buffer": open(MODEL, "rb").read()} if buffer else {"model_asset_path": MODEL}
    if delegate == "cpu":
        if not hasattr(B, "Delegate"):
            print("SKIP BaseOptions has no Delegate option in this build"); sys.exit(0)
        kw["delegate"] = B.Delegate.CPU
    opts = v.HandLandmarkerOptions(base_options=B(**kw), running_mode=getattr(v.RunningMode, mode), num_hands=1)
    return v.HandLandmarker.create_from_options(opts), where
"""

STEPS = [
    ("import", "import mediapipe", "print('OK mediapipe', mp.__version__)"),
    ("image", "native mp.Image from numpy",
     "import numpy as np; i = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.zeros((480,640,3), np.uint8));"
     " print('OK', i.width, 'x', i.height)"),
    ("img_default", "direct load: IMAGE mode, default delegate",
     "lm, w = landmarker('IMAGE'); print('OK via', w); lm.close()"),
    ("img_cpu", "direct load: IMAGE mode, CPU delegate",
     "lm, w = landmarker('IMAGE', 'cpu'); print('OK via', w); lm.close()"),
    ("img_cpu_buf", "direct load: IMAGE mode, CPU delegate, model as bytes",
     "lm, w = landmarker('IMAGE', 'cpu', buffer=True); print('OK via', w); lm.close()"),
    ("detect", "direct inference: detect() on a blank frame (IMAGE, CPU)",
     "import numpy as np; lm, w = landmarker('IMAGE', 'cpu');"
     " r = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.zeros((480,640,3), np.uint8)));"
     " print('OK hands found:', len(r.hand_landmarks)); lm.close()"),
    ("video_default", "direct load with MORPH's settings: VIDEO mode, default delegate",
     "lm, w = landmarker('VIDEO'); print('OK via', w); lm.close()"),
    ("morph", "MORPH: create_landmarker(load_vision(), model)",
     "sys.path.insert(0, %r); from pathlib import Path; from morph_pi import camera_debug as cd;"
     " v = cd.load_vision(); lm = cd.create_landmarker(v, Path(MODEL)); print('OK via', v.api); lm.close()" % str(REPO)),
]


def run_step(code: str) -> tuple[str, str]:
    """(status, detail) for one child process: OK / SKIP / ERROR / CRASH <signal>."""
    cmd = [sys.executable, "-X", "faulthandler", "-c", PRELUDE + code, str(MODEL)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=REPO)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "no result after 120 s"
    out = p.stdout.strip().splitlines()
    if p.returncode < 0:
        name = signal.Signals(-p.returncode).name
        where = [ln.strip() for ln in p.stderr.splitlines() if ln.strip().startswith("File ")]
        return f"CRASH {name}", (where[0] if where else "(no Python frame)")
    if p.returncode != 0:
        err = [ln for ln in p.stderr.strip().splitlines() if ln.strip()]
        return "ERROR", err[-1] if err else f"exit code {p.returncode}"
    last = out[-1] if out else ""
    return ("SKIP", last[5:]) if last.startswith("SKIP") else ("OK", last[3:] if last.startswith("OK") else last)


def environment() -> None:
    print("== environment")
    print(f"python   {sys.version.split()[0]} ({platform.architecture()[0]}) machine={platform.machine()}")
    print(f"system   {platform.platform()}")
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
        feats = next((ln.split(":", 1)[1].split() for ln in cpuinfo.splitlines() if ln.startswith("Features")), [])
        model = next((ln.split(":", 1)[1].strip() for ln in cpuinfo.splitlines() if ln.startswith("Model")), "?")
        newer = [f for f in ("asimddp", "fphp", "asimdhp", "atomics", "sve", "i8mm", "bf16") if f in feats]
        print(f"board    {model}")
        print(f"cpu      features: {' '.join(feats) or '?'}")
        print(f"         ARMv8.2+ features present: {' '.join(newer) or 'none (ARMv8.0 core, e.g. Pi 4 Cortex-A72)'}")
    except OSError:
        print("cpu      /proc/cpuinfo not available (not Linux)")
    for dist in sorted({d.metadata["Name"] for d in md.distributions() if d.metadata["Name"]}):
        if "mediapipe" in dist.lower() or dist.lower() in ("numpy", "opencv-python", "opencv-contrib-python"):
            d = md.distribution(dist)
            tags = [ln.split(":", 1)[1].strip() for ln in (d.read_text("WHEEL") or "").splitlines() if ln.startswith("Tag")]
            print(f"package  {dist}=={d.version} wheel={','.join(tags) or '?'}")
    print(f"model    {MODEL} ({MODEL.stat().st_size} bytes)" if MODEL.is_file() else f"model    MISSING: {MODEL}")


def verdict(r: dict[str, str]) -> str:
    crash = {k for k, s in r.items() if s.startswith("CRASH")}
    ok = {k for k, s in r.items() if s == "OK"}
    direct = {"img_default", "img_cpu", "img_cpu_buf"}
    if crash & {"import", "image"}:
        return ("PACKAGE: MediaPipe crashes before any model is loaded. This build is not compatible with this CPU;\n"
                "         no MORPH setting can avoid it. Use a MediaPipe build made for this board/CPU.")
    if direct <= crash | {k for k, s in r.items() if s == "SKIP"} and not (direct & ok):
        return ("PACKAGE: MediaPipe crashes loading the model with the most basic settings (IMAGE, CPU, even from bytes)\n"
                "         and no MORPH code. MORPH's settings are not the cause. Use a different MediaPipe build.")
    if "detect" in crash:
        return "PACKAGE: the model loads but inference crashes; MediaPipe's CPU kernels are not compatible with this CPU."
    if "video_default" in crash and direct & ok:
        works = ", ".join(sorted(direct & ok))
        return (f"CODE FIX: basic loading works ({works}) but MORPH's VIDEO/default settings crash.\n"
                "         MORPH can switch the Pi to the working mode/delegate.")
    if "morph" in crash and "video_default" in ok:
        return "CODE FIX: direct loading with MORPH's settings works, but MORPH's own code path crashes. Send this output."
    if not crash:
        return "No crash reproduced here. If the viewer still dies, the crash is in camera frames or inference over time."
    return "Mixed result. Send this whole output."


def main() -> int:
    environment()
    print("\n== steps (each in its own process; CRASH SIGILL = Illegal instruction)")
    results: dict[str, str] = {}
    for key, label, code in STEPS:
        if key != "import" and results.get("import", "").startswith(("CRASH", "ERROR")):
            results[key] = "SKIP"
            print(f"[SKIP ] {label}: mediapipe does not import")
            continue
        status, detail = run_step(code)
        results[key] = status
        print(f"[{status:5}] {label}: {detail}")
    print("\n== verdict\n" + verdict(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
