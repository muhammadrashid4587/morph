"""Hand-tracking helpers and the camera viewer's non-camera paths.

Nothing here imports cv2 or mediapipe or opens a camera: the viewer is
exercised with small fakes.
"""

import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from morph_pi import camera_debug
from morph_pi.hand_tracking import (
    HAND_CONNECTIONS,
    HandObservation,
    mirror_point,
    mirror_ray,
    ray_exit_point,
    ray_from_landmarks,
    ray_rejection_reason,
)
from morph_pi.models import Point2D, Ray2D


def synthetic_hand(mcp: tuple[float, float] = (0.40, 0.70), tip: tuple[float, float] = (0.45, 0.55)) -> list[tuple[float, float]]:
    """21 in-range landmarks with index MCP (5) and fingertip (8) placed explicitly."""
    points = [(0.30 + 0.01 * i, 0.80 - 0.01 * i) for i in range(21)]
    points[5], points[8] = mcp, tip
    return points


# --- required behavior ----------------------------------------------------------------------


def test_ray_uses_landmarks_5_and_8() -> None:
    ray = ray_from_landmarks(synthetic_hand())
    assert ray == Ray2D(Point2D(0.40, 0.70), Point2D(0.45, 0.55))


def test_too_few_landmarks_gives_none() -> None:
    assert ray_from_landmarks(synthetic_hand()[:20]) is None
    assert ray_from_landmarks([]) is None
    assert "20 of 21" in (ray_rejection_reason(synthetic_hand()[:20]) or "")


@pytest.mark.parametrize("index", [0, 5, 8, 20])
@pytest.mark.parametrize("bad", [(-0.01, 0.5), (0.5, 1.01), (1.2, 0.5)])
def test_out_of_range_point_gives_none(index: int, bad: tuple[float, float]) -> None:
    hand = synthetic_hand()
    hand[index] = bad
    assert ray_from_landmarks(hand) is None  # rejected, never clamped
    assert f"landmark {index}" in (ray_rejection_reason(hand) or "")


@pytest.mark.parametrize("index", [0, 5, 8])
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_point_gives_none(index: int, bad: float) -> None:
    hand = synthetic_hand()
    hand[index] = (bad, 0.5)
    assert ray_from_landmarks(hand) is None


@pytest.mark.parametrize("tip", [(0.40, 0.70), (0.403, 0.704)])  # identical / 0.005 apart
def test_degenerate_index_ray_gives_none(tip: tuple[float, float]) -> None:
    hand = synthetic_hand(mcp=(0.40, 0.70), tip=tip)
    assert ray_from_landmarks(hand) is None
    assert "too short" in (ray_rejection_reason(hand) or "")


# --- input forms and observation --------------------------------------------------------------


def test_accepts_landmark_objects_and_points() -> None:
    objects = [SimpleNamespace(x=x, y=y, z=0.0) for x, y in synthetic_hand()]  # like NormalizedLandmark
    points = [Point2D(x, y) for x, y in synthetic_hand()]
    assert ray_from_landmarks(objects) == ray_from_landmarks(points) == ray_from_landmarks(synthetic_hand())


@pytest.mark.parametrize("bad", [("0.5", 0.5), (True, 0.5), (None, 0.5), "xy", 0.5])
def test_rejects_non_numeric_landmarks(bad: object) -> None:
    hand: list[Any] = synthetic_hand()
    hand[3] = bad
    assert ray_from_landmarks(hand) is None


def test_hand_observation() -> None:
    hand = HandObservation(tuple(Point2D(x, y) for x, y in synthetic_hand()), score=0.9)
    assert len(hand.landmarks) == 21
    assert hand.ray == ray_from_landmarks(synthetic_hand())
    mirrored = hand.mirrored()
    assert mirrored.landmarks[5] == Point2D(0.60, 0.70)
    assert mirrored.ray == mirror_ray(hand.ray)  # mirroring commutes with building the ray
    with pytest.raises(ValueError, match="expected 21"):
        HandObservation(hand.landmarks[:20])
    with pytest.raises(ValueError, match="landmark 2"):
        HandObservation((*hand.landmarks[:2], (1.5, 0.5), *hand.landmarks[3:]))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="score"):
        HandObservation(hand.landmarks, score=1.5)


def test_connections_cover_all_21_landmarks() -> None:
    assert len(HAND_CONNECTIONS) == 21
    assert {i for pair in HAND_CONNECTIONS for i in pair} == set(range(21))
    assert (5, 6) in HAND_CONNECTIONS and (7, 8) in HAND_CONNECTIONS


# --- mirroring and ray extension ------------------------------------------------------------------


def test_mirror_is_an_involution() -> None:
    p = Point2D(0.2, 0.7)
    assert mirror_point(p) == Point2D(0.8, 0.7)
    twice = mirror_point(mirror_point(p))
    assert (twice.x, twice.y) == pytest.approx((p.x, p.y))  # exact up to float rounding


@pytest.mark.parametrize(
    ("ray", "exit_point"),
    [
        (Ray2D(Point2D(0.5, 0.5), Point2D(0.6, 0.5)), (1.0, 0.5)),  # right edge
        (Ray2D(Point2D(0.5, 0.9), Point2D(0.5, 0.8)), (0.5, 0.0)),  # top edge
        (Ray2D(Point2D(0.5, 0.5), Point2D(0.4, 0.4)), (0.0, 0.0)),  # exactly through the corner
        (Ray2D(Point2D(0.2, 0.9), Point2D(0.3, 0.8)), (1.0, 0.1)),  # diagonal: exits the right edge
    ],
)
def test_ray_exit_point(ray: Ray2D, exit_point: tuple[float, float]) -> None:
    end = ray_exit_point(ray)
    assert (end.x, end.y) == pytest.approx(exit_point)


# --- viewer display model (pure) --------------------------------------------------------------------


def test_overlay_is_mirrored_consistently() -> None:
    model = camera_debug.build_overlay(synthetic_hand(), width=101, height=51)
    assert model.hand_detected and model.note is None
    assert model.points[5] == (60, 35)  # x 0.40 -> mirrored 0.60 -> 60 px
    assert model.finger == (model.points[5], model.points[8])
    assert model.ray is not None and model.ray[0] == model.points[5]
    # The camera-space ray points up and right; mirrored, it must point up and left.
    (x0, y0), (x1, y1) = model.ray
    assert x1 < x0 and y1 < y0
    assert x1 == 0 or y1 == 0  # extended to the image border


def test_overlay_states() -> None:
    assert camera_debug.build_overlay(None, 640, 480).hand_detected is False
    off_frame = synthetic_hand()
    off_frame[0] = (1.1, 0.5)
    model = camera_debug.build_overlay(off_frame, 640, 480)
    assert model.hand_detected and model.ray is None and "landmark 0" in (model.note or "")
    nan_hand = synthetic_hand()
    nan_hand[3] = (math.nan, 0.5)
    assert camera_debug.build_overlay(nan_hand, 640, 480).points[3] is None  # not drawable


def test_fps_meter() -> None:
    meter = camera_debug.FpsMeter(smoothing=0.5)
    assert meter.tick(10.0) == 0.0
    assert meter.tick(10.1) == pytest.approx(10.0)
    assert meter.tick(10.15) == pytest.approx(0.5 * 10 + 0.5 * 20)
    assert meter.tick(10.15) == pytest.approx(15.0)  # zero interval ignored


# --- viewer setup and failure paths (fakes only) ------------------------------------------------------


def test_missing_vision_dependencies(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setitem(sys.modules, "cv2", None)  # makes `import cv2` fail even if installed
    assert camera_debug.main([]) == camera_debug.EXIT_SETUP != 0
    assert capsys.readouterr().err == (
        "Install optional vision dependencies with: pip install -r morph_pi/requirements-vision.txt\n"
    )


def test_missing_model_is_explained_not_downloaded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "models" / "hand_landmarker.task"
    assert camera_debug.run(0, model, vision=SimpleNamespace()) == camera_debug.EXIT_SETUP  # type: ignore[arg-type]
    err = capsys.readouterr().err
    assert "hand model not found" in err and camera_debug.MODEL_URL in err and "curl" in err
    assert not model.exists() and not model.parent.exists()


class FakeCapture:
    def __init__(self, opened: bool = True, frames: int = 100, interrupt: bool = False) -> None:
        self.opened, self.frames, self.interrupt, self.released = opened, frames, interrupt, False

    def isOpened(self) -> bool:  # noqa: N802 (OpenCV's name)
        return self.opened

    def read(self) -> tuple[bool, Any]:
        if self.interrupt:
            raise KeyboardInterrupt
        return True, SimpleNamespace(shape=(480, 640, 3))

    def release(self) -> None:
        self.released = True


class FakeCv2:
    COLOR_BGR2RGB = FONT_HERSHEY_SIMPLEX = LINE_AA = WND_PROP_VISIBLE = 0

    def __init__(self, capture: FakeCapture, keys: list[int]) -> None:
        self.capture, self.keys, self.calls, self.closed = capture, keys, [], False

    def VideoCapture(self, index: int) -> FakeCapture:  # noqa: N802
        return self.capture

    def cvtColor(self, frame: Any, code: int) -> Any:  # noqa: N802
        return frame

    def flip(self, frame: Any, code: int) -> Any:
        assert code == 1  # horizontal mirror
        return frame

    def getTextSize(self, *args: Any) -> tuple[tuple[int, int], int]:  # noqa: N802
        return (100, 12), 4

    def waitKey(self, delay: int) -> int:  # noqa: N802
        return self.keys.pop(0) if self.keys else -1

    def getWindowProperty(self, *args: Any) -> float:  # noqa: N802
        return 1.0

    def destroyAllWindows(self) -> None:  # noqa: N802
        self.closed = True

    def __getattr__(self, name: str) -> Any:  # line, circle, rectangle, putText, imshow
        return lambda *args, **kwargs: self.calls.append((name, args))


class FakeLandmarker:
    def __init__(self) -> None:
        self.timestamps: list[int] = []
        self.closed = False

    def detect_for_video(self, image: Any, timestamp_ms: int) -> Any:
        self.timestamps.append(timestamp_ms)
        return SimpleNamespace(hand_landmarks=[[SimpleNamespace(x=x, y=y) for x, y in synthetic_hand()]])

    def close(self) -> None:
        self.closed = True


def fake_vision(cv2: FakeCv2) -> Any:
    mp = SimpleNamespace(Image=lambda **kw: kw, ImageFormat=SimpleNamespace(SRGB="srgb"))
    return camera_debug.Vision(cv2=cv2, mp=mp, base_options=None, vision=None)


@pytest.fixture
def model_file(tmp_path: Path) -> Path:
    path = tmp_path / "hand_landmarker.task"
    path.write_bytes(b"fake")
    return path


def test_camera_unavailable_gives_actionable_message(model_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cv2 = FakeCv2(FakeCapture(opened=False), keys=[])
    assert camera_debug.run(0, model_file, fake_vision(cv2)) == camera_debug.EXIT_CAMERA
    err = capsys.readouterr().err
    assert "Could not open camera 0" in err and "Privacy & Security > Camera" in err
    assert cv2.capture.released and cv2.closed


@pytest.mark.parametrize("key", [ord("q"), ord("Q"), 27])
def test_live_loop_draws_and_quits(
    key: int, model_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    landmarker = FakeLandmarker()
    monkeypatch.setattr(camera_debug, "create_landmarker", lambda v, path: landmarker)
    cv2 = FakeCv2(FakeCapture(), keys=[-1, -1, key])
    assert camera_debug.run(0, model_file, fake_vision(cv2)) == camera_debug.EXIT_OK
    assert landmarker.timestamps == sorted(set(landmarker.timestamps)) and len(landmarker.timestamps) == 3
    texts = [args[1] for name, args in cv2.calls if name == "putText"]
    assert "HAND DETECTED" in texts and "Press Q or Esc to quit" in texts and "MORPH POINT DEBUG" in texts
    cyan_lines = [args for name, args in cv2.calls if name == "line" and camera_debug.CYAN in args]
    assert cyan_lines  # the pointing ray was drawn
    assert sum(name == "imshow" for name, _ in cv2.calls) == 3
    assert all(args[0] == camera_debug.WINDOW_TITLE for name, args in cv2.calls if name == "imshow")
    assert cv2.capture.released and landmarker.closed and cv2.closed


def test_ctrl_c_releases_camera_and_closes_windows(
    model_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    landmarker = FakeLandmarker()
    monkeypatch.setattr(camera_debug, "create_landmarker", lambda v, path: landmarker)
    cv2 = FakeCv2(FakeCapture(interrupt=True), keys=[])
    assert camera_debug.run(0, model_file, fake_vision(cv2)) == camera_debug.EXIT_OK
    assert "Stopped (Ctrl+C)" in capsys.readouterr().out
    assert cv2.capture.released and landmarker.closed and cv2.closed


def test_invalid_camera_index_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as info:
        camera_debug.main(["--camera", "-1"])
    assert info.value.code == 2


def test_modules_import_without_vision_packages() -> None:
    code = (
        "import sys, morph_pi.hand_tracking, morph_pi.camera_debug, morph_pi.app;"
        "bad = {'cv2', 'mediapipe'} & set(sys.modules); assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=Path(__file__).resolve().parent.parent)
