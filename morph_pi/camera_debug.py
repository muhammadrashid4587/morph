"""Live webcam POINT debug viewer (optional; needs OpenCV + MediaPipe).

    python -m morph_pi.camera_debug [--camera 0] [--model PATH] [--config PATH]

Shows the mirrored camera image with the detected hand's 21 landmarks and
connections, a cyan pointing ray from index MCP (5) through the index
fingertip (8), and the three target zones. Each frame's ray goes through
select_target() and one per-session TemporalSmoother (see live_point.py);
the viewer highlights the candidate / locked target. Debug-only: it controls
no hardware and sends nothing over the network.

Detection and selection run in the unmirrored camera frame (the space the
target config uses). Image, landmarks, ray and target zones are all mirrored
together for display only.

Exit codes: 0 normal quit, 1 camera failure, 2 usage error, 3 missing setup
(vision packages or model file).
"""

from __future__ import annotations

import argparse
import importlib
import math
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DEFAULT_MIN_RAY_LENGTH
from .hand_tracking import (
    HAND_CONNECTIONS,
    HAND_LANDMARK_COUNT,
    mirror_ray,
    ray_exit_point,
    ray_from_landmarks,
    ray_rejection_reason,
)
from .live_point import PointFrame, PointPipeline, short_name
from .objects import Box, EveryNth, ObjectFrame, ObjectPointPipeline, boxes_from_detections
from .models import SelectionState, TargetConfig, TargetId
from .targets import ConfigError, default_config, load_config

MISSING_DEPS_MESSAGE = "Install optional vision dependencies with: pip install -r morph_pi/requirements-vision.txt"
WINDOW_TITLE = "MORPH POINT Debug"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_PATH = REPO_ROOT / "models" / "hand_landmarker.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)

EXIT_OK, EXIT_CAMERA, EXIT_USAGE, EXIT_SETUP = 0, 1, 2, 3
DEFAULT_OBJECT_MODEL_PATH = REPO_ROOT / "models" / "efficientdet_lite0.tflite"
OBJECT_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/int8/1/efficientdet_lite0.tflite"
)
DETECT_EVERY = 3     # objects mode: run the detector on every 3rd frame, reuse its boxes in between
DETECT_WIDTH = 320   # objects mode: detector input width in pixels (Pi speed)
MAX_FAILED_READS = 30  # consecutive failed frame reads before giving up
QUIT_KEYS = frozenset({ord("q"), ord("Q"), 27})  # 27 = Escape

# BGR colors
CYAN = (255, 255, 0)
GREEN = (90, 220, 90)
RED = (70, 70, 240)
YELLOW = (0, 215, 255)
WHITE = (245, 245, 245)
BONE = (200, 200, 200)
BLACK = (0, 0, 0)
ORANGE = (0, 165, 255)
TARGET_COLORS: dict[TargetId, tuple[int, int, int]] = {
    TargetId.BLUE_BLOCK: (255, 140, 30),
    TargetId.YELLOW_BLOCK: (0, 225, 255),
    TargetId.GREEN_BLOCK: (60, 210, 60),
}

Pixel = tuple[int, int]


# --- pure display model (testable without OpenCV) ------------------------------------------


@dataclass(frozen=True, slots=True)
class OverlayModel:
    hand_detected: bool
    points: tuple[Pixel | None, ...]  # mirrored pixel positions; None if not drawable
    finger: tuple[Pixel, Pixel] | None  # landmark 5 -> 8, mirrored
    ray: tuple[Pixel, Pixel] | None  # landmark 5 -> image border, mirrored
    note: str | None  # why there is no ray, when a hand is detected


def to_pixel(x: float, y: float, width: int, height: int) -> Pixel | None:
    """Normalized (x, y) -> pixel; None for non-finite values. Off-image values stay drawable."""
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    px = round(min(2.0, max(-1.0, x)) * (width - 1))
    py = round(min(2.0, max(-1.0, y)) * (height - 1))
    return int(px), int(py)


def build_overlay(
    raw: Sequence[tuple[float, float]] | None,
    width: int,
    height: int,
    min_ray_length: float = DEFAULT_MIN_RAY_LENGTH,
) -> OverlayModel:
    """Everything to draw for one frame, in mirrored display pixels."""
    if not raw:
        return OverlayModel(False, (), None, None, None)
    points = tuple(to_pixel(1.0 - x, y, width, height) for x, y in raw)
    ray = ray_from_landmarks(raw, min_ray_length)
    if ray is None:
        return OverlayModel(True, points, None, None, ray_rejection_reason(raw, min_ray_length))
    shown = mirror_ray(ray)
    end = ray_exit_point(shown)

    def px(p: Any) -> Pixel:
        pixel = to_pixel(p.x, p.y, width, height)
        assert pixel is not None  # Point2D is always finite
        return pixel

    return OverlayModel(True, points, (px(shown.origin), px(shown.through)), (px(shown.origin), px(end)), None)


@dataclass(frozen=True, slots=True)
class TargetZone:
    id: TargetId
    name: str  # BLUE / YELLOW / GREEN
    center: Pixel  # mirrored display pixels
    axes: Pixel  # ellipse half-axes in pixels
    color: tuple[int, int, int]


def target_zones(config: TargetConfig, width: int, height: int) -> tuple[TargetZone, ...]:
    """Each target's zone in mirrored display pixels.

    The zone is everything within max_ray_distance of the target center, in
    normalized units: a target is a valid candidate only if the ray passes
    through it. Normalized x and y scale by width and height, so the zone is
    an ellipse in pixels.
    """
    axes = (
        max(1, round(config.max_ray_distance * (width - 1))),
        max(1, round(config.max_ray_distance * (height - 1))),
    )
    zones = []
    for target in config.targets:
        center = to_pixel(1.0 - target.center.x, target.center.y, width, height)
        assert center is not None  # Point2D is always finite
        zones.append(TargetZone(target.id, short_name(target.id), center, axes, TARGET_COLORS[target.id]))
    return tuple(zones)


def pulse(now: float, hz: float = 2.0) -> float:
    """0..1, oscillating hz times per second (for the candidate highlight)."""
    return 0.5 + 0.5 * math.sin(2.0 * math.pi * hz * now)


def selection_status(frame: PointFrame) -> tuple[str, tuple[int, int, int]] | None:
    """Big status line under HAND DETECTED; None when there is no hand (NO HAND is shown already)."""
    result = frame.result
    name = short_name(result.target_id) if result is not None and result.target_id is not None else "-"
    status = frame.status
    if status == "NO HAND":
        return None
    if status == "LOCKED":
        assert result is not None and result.target_id is not None
        return f"LOCKED: {name}", TARGET_COLORS[result.target_id]
    if status == "CANDIDATE":
        assert result is not None
        return f"CANDIDATE: {name} {result.confidence:.0%}", WHITE
    return status, {"NO RAY": YELLOW, "AMBIGUOUS": ORANGE}.get(status, WHITE)


class FpsMeter:
    """Frames per second, smoothed with an exponential moving average."""

    def __init__(self, smoothing: float = 0.9) -> None:
        self.smoothing = smoothing
        self.fps = 0.0
        self._last: float | None = None

    def tick(self, now: float) -> float:
        if self._last is not None and now > self._last:
            instant = 1.0 / (now - self._last)
            self.fps = instant if self.fps == 0.0 else self.smoothing * self.fps + (1 - self.smoothing) * instant
        self._last = now
        return self.fps


# --- optional dependencies and setup messages --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Vision:
    cv2: Any
    mp: Any
    base_options: Any  # BaseOptions class of the resolved MediaPipe API
    vision: Any  # namespace providing HandLandmarker, HandLandmarkerOptions, RunningMode
    image: Any = None  # mp.Image class (None: use mp.Image)
    image_format: Any = None  # mp.ImageFormat (None: use mp.ImageFormat)
    api: str = ""  # which import path was used, shown at startup


class VisionApiError(RuntimeError):
    """MediaPipe is installed, but no complete Hand Landmarker API was found in it."""


_LANDMARKER_ATTRS = ("HandLandmarker", "HandLandmarkerOptions", "RunningMode")


def _complete_api(tasks: Any, vision: Any) -> tuple[Any, Any] | None:
    """(BaseOptions, vision) when this namespace pair is a complete Hand Landmarker API.

    BaseOptions is taken from the same namespace as `vision`, never mixed across builds.
    """
    if vision is None or not all(hasattr(vision, name) for name in _LANDMARKER_ATTRS):
        return None
    base_options = getattr(tasks, "BaseOptions", None) or getattr(vision, "BaseOptions", None)
    return (base_options, vision) if base_options is not None else None


def resolve_mediapipe_api(mp: Any, import_module: Any = importlib.import_module) -> tuple[Any, Any, str]:
    """Find (BaseOptions, vision namespace, description) across MediaPipe builds.

    1. `mediapipe.tasks.python` (+ `.vision`): the layout the Mac builds use (tried first,
       so the known-working Mac path is unchanged).
    2. `mp.tasks.vision` / `mp.tasks.BaseOptions`: MediaPipe 1.0.1 on the Pi, where
       `mediapipe.tasks.python.vision` imports but has no HandLandmarker.
    """
    try:
        python_api = import_module("mediapipe.tasks.python")
        vision = getattr(python_api, "vision", None)
        if vision is None:
            vision = import_module("mediapipe.tasks.python.vision")
        api = _complete_api(python_api, vision)
        if api is not None:
            return (*api, "mediapipe.tasks.python.vision")
    except (ImportError, AttributeError):
        pass
    tasks = getattr(mp, "tasks", None)
    api = _complete_api(tasks, getattr(tasks, "vision", None))
    if api is not None:
        return (*api, "mp.tasks.vision")
    version = getattr(mp, "__version__", "unknown version")
    raise VisionApiError(
        f"MediaPipe {version} has no complete Hand Landmarker API: tried mediapipe.tasks.python.vision "
        "and mp.tasks.vision (each needs HandLandmarker, HandLandmarkerOptions, RunningMode, BaseOptions)"
    )


def resolve_image_api(mp: Any, vision: Any) -> tuple[Any, Any]:
    """(Image class, ImageFormat) with an SRGB format: mp.Image first, then the vision namespace."""
    for namespace in (mp, vision):
        image = getattr(namespace, "Image", None)
        image_format = getattr(namespace, "ImageFormat", None)
        if image is not None and hasattr(image_format, "SRGB"):
            return image, image_format
    raise VisionApiError("MediaPipe provides no Image / ImageFormat.SRGB (tried mp.Image and the vision namespace)")


def load_vision() -> Vision | None:
    """Import OpenCV and MediaPipe lazily. None when either is not installed.

    Raises VisionApiError when MediaPipe is installed but its Hand Landmarker API is not found.
    """
    os.environ.setdefault("GLOG_minloglevel", "2")  # quieter MediaPipe native logs
    try:
        import cv2
        import mediapipe as mp
    except ImportError:
        return None
    base_options, vision, api = resolve_mediapipe_api(mp)
    image, image_format = resolve_image_api(mp, vision)
    return Vision(cv2, mp, base_options, vision, image, image_format, api)


def model_help(path: Path) -> str:
    return (
        f"MediaPipe hand model not found: {path}\n"
        "Download it once (a few MB, from Google's MediaPipe model storage) from the repo root:\n"
        "  mkdir -p models\n"
        f"  curl -fL -o models/hand_landmarker.task {MODEL_URL}\n"
        "Or point to an existing copy with --model PATH. (models/ is git-ignored.)"
    )


def object_model_help(path: Path) -> str:
    return (
        f"Object detection model not found: {path}\n"
        "Download it once (about 4.6 MB, EfficientDet-Lite0 int8) from the repo root:\n"
        "  mkdir -p models\n"
        f"  curl -fL -o models/efficientdet_lite0.tflite {OBJECT_MODEL_URL}\n"
        "Or point to it with --object-model PATH. (models/ is git-ignored.)"
    )


def camera_help(index: int) -> str:
    return (
        f"Could not open camera {index}.\n"
        "- macOS: System Settings > Privacy & Security > Camera: allow the app running this\n"
        "  command (Terminal, iTerm, Cursor, VS Code), then quit and reopen that app.\n"
        "- If macOS just asked for camera access, allow it and run the command again.\n"
        "- Close other apps using the camera (FaceTime, Zoom, Photo Booth), or try --camera 1."
    )


def create_landmarker(v: Vision, model_path: Path) -> Any:
    options = v.vision.HandLandmarkerOptions(
        base_options=v.base_options(model_asset_path=str(model_path)),
        running_mode=v.vision.RunningMode.VIDEO,
        num_hands=1,
    )
    return v.vision.HandLandmarker.create_from_options(options)


def create_object_detector(v: Vision, model_path: Path) -> Any:
    if not all(hasattr(v.vision, name) for name in ("ObjectDetector", "ObjectDetectorOptions")):
        raise VisionApiError("this MediaPipe build has no ObjectDetector")
    options = v.vision.ObjectDetectorOptions(
        base_options=v.base_options(model_asset_path=str(model_path)),
        running_mode=v.vision.RunningMode.VIDEO,
        max_results=5,
        score_threshold=0.3,
    )
    return v.vision.ObjectDetector.create_from_options(options)


# --- live loop ---------------------------------------------------------------------------------------


def detect_hand(v: Vision, landmarker: Any, frame: Any, timestamp_ms: int) -> list[tuple[float, float]] | None:
    """Raw normalized (x, y) of the first detected hand in the (unmirrored) frame."""
    rgb = v.cv2.cvtColor(frame, v.cv2.COLOR_BGR2RGB)
    image_cls = v.image if v.image is not None else v.mp.Image
    image_format = v.image_format if v.image_format is not None else v.mp.ImageFormat
    image = image_cls(image_format=image_format.SRGB, data=rgb)
    result = landmarker.detect_for_video(image, timestamp_ms)
    if not result.hand_landmarks:
        return None
    return [(float(lm.x), float(lm.y)) for lm in result.hand_landmarks[0][:HAND_LANDMARK_COUNT]]


def detect_objects(v: Vision, detector: Any, frame: Any, timestamp_ms: int) -> list[Box]:
    """Normalized boxes from the (unmirrored) frame, detected on a DETECT_WIDTH-wide copy."""
    height, width = frame.shape[:2]
    small_h = max(1, round(height * DETECT_WIDTH / width))
    small = v.cv2.resize(frame, (DETECT_WIDTH, small_h), interpolation=v.cv2.INTER_AREA)
    rgb = v.cv2.cvtColor(small, v.cv2.COLOR_BGR2RGB)
    image_cls = v.image if v.image is not None else v.mp.Image
    image_format = v.image_format if v.image_format is not None else v.mp.ImageFormat
    result = detector.detect_for_video(image_cls(image_format=image_format.SRGB, data=rgb), timestamp_ms)
    return boxes_from_detections(result, DETECT_WIDTH, small_h)


def _label(cv2: Any, img: Any, text: str, org: Pixel, color: tuple[int, int, int], scale: float = 0.6) -> None:
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    x, y = org
    cv2.rectangle(img, (x - 4, y - th - 6), (x + tw + 4, y + baseline + 2), BLACK, -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)


def draw_overlay(cv2: Any, img: Any, model: OverlayModel, fps: float) -> None:
    height, width = img.shape[:2]
    for a, b in HAND_CONNECTIONS:
        pa, pb = (model.points[a], model.points[b]) if model.points else (None, None)
        if pa is not None and pb is not None:
            cv2.line(img, pa, pb, BONE, 2, cv2.LINE_AA)
    for index, point in enumerate(model.points):
        if point is not None:
            cv2.circle(img, point, 6 if index in (5, 8) else 4, CYAN if index in (5, 8) else GREEN, -1, cv2.LINE_AA)
    if model.ray is not None and model.finger is not None:
        cv2.line(img, *model.ray, CYAN, 3, cv2.LINE_AA)
        cv2.line(img, *model.finger, CYAN, 6, cv2.LINE_AA)

    _label(cv2, img, "MORPH POINT DEBUG", (12, 30), WHITE, 0.7)
    if model.hand_detected:
        _label(cv2, img, "HAND DETECTED", (12, 62), GREEN)
        if model.note:
            _label(cv2, img, f"no ray: {model.note}", (12, 126), YELLOW, 0.5)
    else:
        _label(cv2, img, "NO HAND", (12, 62), RED)
    _label(cv2, img, f"FPS {fps:4.1f}", (max(12, width - 130), 30), WHITE)
    _label(cv2, img, "Press Q or Esc to quit", (12, height - 16), WHITE, 0.55)


def _label_centered(cv2: Any, img: Any, text: str, center_x: int, y: int, color: tuple[int, int, int]) -> None:
    (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
    _label(cv2, img, text, (center_x - tw // 2, y), color, 0.55)


def _ellipse(cv2: Any, img: Any, zone: TargetZone, grow: int, color: tuple[int, int, int], thickness: int) -> None:
    axes = (zone.axes[0] + grow, zone.axes[1] + grow)
    cv2.ellipse(img, zone.center, axes, 0, 0, 360, color, thickness, cv2.LINE_AA)


def draw_targets(cv2: Any, img: Any, zones: tuple[TargetZone, ...], frame: PointFrame, now: float) -> None:
    """Target zones, with the current candidate / locked / ambiguous targets highlighted."""
    result = frame.result
    best = result.target_id if result is not None else None
    second = (
        result.second_candidate.target.id
        if result is not None and result.second_candidate is not None
        else None
    )
    for zone in zones:
        label = zone.name
        _ellipse(cv2, img, zone, 0, zone.color, 2)
        cv2.circle(img, zone.center, 6, zone.color, -1, cv2.LINE_AA)
        if frame.locked and zone.id is best:
            _ellipse(cv2, img, zone, 0, zone.color, 8)  # solid, bright
            _ellipse(cv2, img, zone, 8, WHITE, 2)
            cv2.circle(img, zone.center, 16, zone.color, -1, cv2.LINE_AA)
            label = f"LOCKED: {zone.name}"
        elif frame.state is SelectionState.CANDIDATE and zone.id is best and result is not None:
            p = pulse(now)
            _ellipse(cv2, img, zone, round(6 * p), WHITE, 2 + round(4 * p))  # pulsing outline
            label = f"{zone.name} {result.confidence:.0%}"
        elif frame.state is SelectionState.AMBIGUOUS and zone.id in (best, second):
            _ellipse(cv2, img, zone, 4, ORANGE, 3)
            label = f"{zone.name}?"
        _label_centered(cv2, img, label, zone.center[0], zone.center[1] - zone.axes[1] - 12, zone.color)


def draw_selection(cv2: Any, img: Any, frame: PointFrame) -> None:
    height = img.shape[0]
    status = selection_status(frame)
    if status is not None:
        _label(cv2, img, status[0], (12, 96), status[1], 0.75)
    _label(cv2, img, frame.summary(), (12, height - 46), WHITE, 0.5)


def _mirrored_rect(box: Box, width: int, height: int) -> tuple[Pixel, Pixel]:
    p0 = to_pixel(1.0 - box.x1, box.y0, width, height)
    p1 = to_pixel(1.0 - box.x0, box.y1, width, height)
    assert p0 is not None and p1 is not None
    return p0, p1


def draw_objects(cv2: Any, img: Any, frame: ObjectFrame) -> None:
    """All detected boxes faint, the chosen one bold with its label (mirrored like the video)."""
    height, width = img.shape[:2]
    chosen = frame.choice.box if frame.choice is not None else None
    for box in frame.boxes:
        if box is chosen:
            continue
        p0, p1 = _mirrored_rect(box, width, height)
        cv2.rectangle(img, p0, p1, BONE, 1, cv2.LINE_AA)
        cv2.putText(img, f"{box.label} {box.score:.2f}", (p0[0] + 3, p0[1] + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, BONE, 1, cv2.LINE_AA)
    if chosen is not None and frame.ray is not None:
        color = GREEN if frame.locked else WHITE
        p0, p1 = _mirrored_rect(chosen, width, height)
        cv2.rectangle(img, p0, p1, color, 5 if frame.locked else 3, cv2.LINE_AA)
        _label(cv2, img, frame.label, (p0[0], max(20, p0[1] - 8)), color, 0.6)


def draw_object_selection(cv2: Any, img: Any, frame: ObjectFrame) -> None:
    height = img.shape[0]
    if frame.status != "NO HAND":
        color = GREEN if frame.locked else (YELLOW if frame.status == "NO RAY" else WHITE)
        _label(cv2, img, frame.label if frame.choice else frame.status, (12, 96), color, 0.75)
    _label(cv2, img, frame.summary(), (12, height - 46), WHITE, 0.5)


def _loop_objects(v: Vision, cap: Any, landmarker: Any, detector: Any, pipeline: ObjectPointPipeline) -> int:
    """objects mode: lock onto any detected object the finger points at."""
    cv2 = v.cv2
    meter = FpsMeter()
    schedule = EveryNth(DETECT_EVERY)
    boxes: list[Box] = []
    failed_reads = 0
    last_ts = -1
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            failed_reads += 1
            if failed_reads >= MAX_FAILED_READS:
                print("Camera stopped delivering frames (disconnected or access revoked).", file=sys.stderr)
                return EXIT_CAMERA
            cv2.waitKey(10)
            continue
        failed_reads = 0
        timestamp_ms = max(last_ts + 1, int(time.monotonic() * 1000))
        last_ts = timestamp_ms

        raw = detect_hand(v, landmarker, frame, timestamp_ms)
        if schedule.due():
            boxes = detect_objects(v, detector, frame, timestamp_ms)
        found = pipeline.process(raw, boxes)
        if found.just_locked:
            print(found.label, flush=True)

        display = cv2.flip(frame, 1)
        height, width = display.shape[:2]
        draw_objects(cv2, display, found)
        draw_overlay(cv2, display, build_overlay(raw, width, height, pipeline.min_ray_length), meter.tick(time.monotonic()))
        draw_object_selection(cv2, display, found)
        cv2.imshow(WINDOW_TITLE, display)

        if (cv2.waitKey(1) & 0xFF) in QUIT_KEYS:
            return EXIT_OK
        if cv2.getWindowProperty(WINDOW_TITLE, cv2.WND_PROP_VISIBLE) < 1:
            return EXIT_OK


def _loop(v: Vision, cap: Any, landmarker: Any, pipeline: PointPipeline) -> int:
    cv2 = v.cv2
    meter = FpsMeter()
    failed_reads = 0
    last_ts = -1
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            failed_reads += 1
            if failed_reads >= MAX_FAILED_READS:
                print("Camera stopped delivering frames (disconnected or access revoked).", file=sys.stderr)
                return EXIT_CAMERA
            cv2.waitKey(10)
            continue
        failed_reads = 0
        timestamp_ms = max(last_ts + 1, int(time.monotonic() * 1000))  # must strictly increase
        last_ts = timestamp_ms

        raw = detect_hand(v, landmarker, frame, timestamp_ms)
        point = pipeline.process(raw)  # same pipeline (and smoother) every frame
        if point.just_locked and point.result is not None and point.result.target_id is not None:
            print(f"LOCKED: {short_name(point.result.target_id)} (confidence {point.result.confidence:.2f})", flush=True)

        display = cv2.flip(frame, 1)  # mirror; landmarks, ray and zones are mirrored to match
        height, width = display.shape[:2]
        now = time.monotonic()
        draw_targets(cv2, display, target_zones(pipeline.config, width, height), point, now)
        overlay = build_overlay(raw, width, height, pipeline.config.min_ray_length)
        draw_overlay(cv2, display, overlay, meter.tick(now))
        draw_selection(cv2, display, point)
        cv2.imshow(WINDOW_TITLE, display)

        if (cv2.waitKey(1) & 0xFF) in QUIT_KEYS:
            return EXIT_OK
        if cv2.getWindowProperty(WINDOW_TITLE, cv2.WND_PROP_VISIBLE) < 1:
            return EXIT_OK  # window closed with its close button


def run(
    camera_index: int,
    model_path: Path,
    vision: Vision | None = None,
    config: TargetConfig | None = None,
    mode: str = "colors",
    object_model: Path = DEFAULT_OBJECT_MODEL_PATH,
) -> int:
    try:
        v = vision if vision is not None else load_vision()
    except VisionApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SETUP
    if v is None:
        print(MISSING_DEPS_MESSAGE, file=sys.stderr)
        return EXIT_SETUP
    if not model_path.is_file():
        print(model_help(model_path), file=sys.stderr)
        return EXIT_SETUP
    if mode == "objects" and not object_model.is_file():
        print(object_model_help(object_model), file=sys.stderr)
        return EXIT_SETUP

    cv2 = v.cv2
    cap = None
    landmarker = None
    detector = None
    try:
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            print(camera_help(camera_index), file=sys.stderr)
            return EXIT_CAMERA
        try:
            landmarker = create_landmarker(v, model_path)
        except Exception as exc:  # corrupt/incompatible model file
            print(f"Could not load the MediaPipe hand model {model_path}: {exc}", file=sys.stderr)
            return EXIT_SETUP
        config = config if config is not None else default_config()
        api = f" MediaPipe {getattr(v.mp, '__version__', '?')} via {v.api}." if v.api else ""
        if mode == "objects":
            try:
                detector = create_object_detector(v, object_model)
            except Exception as exc:  # missing API or corrupt model file
                print(f"Could not load the object detection model {object_model}: {exc}", file=sys.stderr)
                return EXIT_SETUP
            print(f"Camera {camera_index} open.{api} Mode: objects. Window: '{WINDOW_TITLE}'. Press Q or Esc to quit.")
            return _loop_objects(v, cap, landmarker, detector, ObjectPointPipeline(config.stable_frames, config.min_ray_length))
        # One pipeline (and one TemporalSmoother) for the whole session, reused every frame.
        pipeline = PointPipeline(config)
        print(f"Camera {camera_index} open.{api} Window: '{WINDOW_TITLE}'. Press Q or Esc to quit.")
        return _loop(v, cap, landmarker, pipeline)
    except KeyboardInterrupt:
        print("\nStopped (Ctrl+C).")
        return EXIT_OK
    finally:
        if detector is not None:
            detector.close()
        if landmarker is not None:
            landmarker.close()
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
        cv2.waitKey(1)  # lets macOS actually close the window


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m morph_pi.camera_debug",
        description="MORPH live POINT viewer: webcam + MediaPipe landmarks + pointing ray + live "
        "target selection. Debug-only: no hardware, serial, or network.",
    )
    parser.add_argument("--camera", type=int, default=0, help="camera index (default: 0)")
    parser.add_argument(
        "--model", type=Path, default=DEFAULT_MODEL_PATH,
        help=f"MediaPipe hand_landmarker.task path (default: {DEFAULT_MODEL_PATH.relative_to(REPO_ROOT)})",
    )
    parser.add_argument("--config", metavar="PATH", help="target layout JSON (default: built-in defaults)")
    parser.add_argument(
        "--mode", choices=("colors", "objects"), default="colors",
        help="colors: lock onto the three colored targets (default); objects: lock onto any detected object",
    )
    parser.add_argument(
        "--object-model", type=Path, default=DEFAULT_OBJECT_MODEL_PATH,
        help=f"EfficientDet-Lite0 .tflite for --mode objects (default: {DEFAULT_OBJECT_MODEL_PATH.relative_to(REPO_ROOT)})",
    )
    args = parser.parse_args(argv)
    if args.camera < 0:
        parser.error("--camera must be >= 0")
    try:
        config = load_config(args.config) if args.config else default_config()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return run(args.camera, args.model, config=config, mode=args.mode, object_model=args.object_model)


if __name__ == "__main__":
    sys.exit(main())
