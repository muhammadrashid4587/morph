"""Any-object pointing: ray-to-box selection, fallbacks, detection conversion, schedule, lock,
and the objects-mode viewer loop with fakes. No camera, cv2 or mediapipe."""

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from morph_pi import camera_debug
from morph_pi.models import Point2D, Ray2D
from morph_pi.objects import (
    NEAREST_MAX,
    REGION_NAME,
    Box,
    EveryNth,
    ObjectLock,
    ObjectPointPipeline,
    boxes_from_detections,
    ray_entry,
    region_box,
    select_box,
)

UP = Ray2D(Point2D(0.5, 0.9), Point2D(0.5, 0.8))  # hand low in the image, pointing straight up; tip at (0.5, 0.8)


def box(x0: float, y0: float, x1: float, y1: float, label: str = "cup", score: float = 0.8) -> Box:
    return Box(x0, y0, x1, y1, label, score)


# --- ray -> box -----------------------------------------------------------------------------------


def test_ray_entry_distance() -> None:
    assert ray_entry(UP, box(0.4, 0.5, 0.6, 0.6)) == pytest.approx(0.3)
    assert ray_entry(UP, box(0.4, 0.2, 0.6, 0.3)) == pytest.approx(0.6)
    assert ray_entry(UP, box(0.4, 0.93, 0.6, 0.99)) is None   # behind the hand
    assert ray_entry(UP, box(0.7, 0.2, 0.9, 0.6)) is None     # off to the side (parallel slab)
    assert ray_entry(UP, box(0.0, 0.3, 1.0, 1.0)) is None     # contains the ray origin


def test_diagonal_ray_hits_box() -> None:
    diag = Ray2D(Point2D(0.1, 0.9), Point2D(0.2, 0.8))  # up and to the right at 45 degrees
    assert ray_entry(diag, box(0.45, 0.45, 0.6, 0.6)) == pytest.approx(math.hypot(0.35, 0.35))  # enters at x=0.45, y=0.55
    assert ray_entry(diag, box(0.1, 0.1, 0.2, 0.2)) is None


def test_selects_the_first_box_the_ray_hits() -> None:
    near, far = box(0.4, 0.5, 0.6, 0.6, "cup"), box(0.4, 0.2, 0.6, 0.3, "bottle")
    choice = select_box(UP, [far, near])
    assert (choice.box, choice.how, choice.name) == (near, "ray", "CUP")


def test_skips_the_pointing_person_and_boxes_behind() -> None:
    person = box(0.0, 0.3, 1.0, 1.0, "person", 0.99)  # contains the hand (ray origin)
    behind = box(0.45, 0.93, 0.55, 0.99, "phone")
    cup = box(0.45, 0.1, 0.55, 0.2)
    assert select_box(UP, [person, behind, cup]).box == cup


def test_pointing_person_alone_is_not_a_target() -> None:
    person = box(0.0, 0.3, 1.0, 1.0, "person", 0.99)  # the fingertip is inside it, distance 0
    choice = select_box(UP, [person])
    assert choice.how == "region" and choice.name == REGION_NAME


def test_equal_entry_prefers_higher_score() -> None:
    low, high = box(0.4, 0.5, 0.6, 0.6, "bowl", 0.4), box(0.3, 0.5, 0.7, 0.6, "cup", 0.9)
    assert select_box(UP, [low, high]).box == high


def test_box_under_the_fingertip_is_hit_by_the_ray() -> None:
    under = box(0.45, 0.75, 0.55, 0.85, "remote")  # fingertip (0.5, 0.8) is inside
    assert select_box(UP, [under]).how == "ray"


def test_falls_back_to_the_nearest_box_near_the_fingertip() -> None:
    side = box(0.55, 0.6, 0.7, 0.7, "mouse")  # ray (x = 0.5) misses; ~0.11 from the fingertip
    choice = select_box(UP, [side])
    assert (choice.box, choice.how) == (side, "nearest")


def test_region_when_nothing_is_detected_there() -> None:
    far = box(0.9, 0.9, 0.99, 0.99, "chair")
    assert far.distance_to(UP.through) > NEAREST_MAX
    for boxes in ([], [far]):
        choice = select_box(UP, boxes)
        assert choice.how == "region" and choice.name == REGION_NAME
        assert choice.box.contains(UP.through)


def test_region_is_clamped_to_the_image() -> None:
    r = region_box(Point2D(0.01, 0.99))
    assert (r.x0, r.y1) == (0.0, 1.0) and r.x1 > r.x0 and r.y1 > r.y0


# --- detections, schedule, lock ------------------------------------------------------------------------


def detection(x: float, y: float, w: float, h: float, name: str = "cup", score: float = 0.7) -> Any:
    return SimpleNamespace(
        bounding_box=SimpleNamespace(origin_x=x, origin_y=y, width=w, height=h),
        categories=[SimpleNamespace(category_name=name, display_name="", score=score)],
    )


def test_boxes_from_detections_normalizes_pixels() -> None:
    result = SimpleNamespace(detections=[
        detection(32, 24, 64, 48, "cup", 0.9),
        detection(-10, 200, 400, 100, "table", 0.5),                       # clamped to the image
        SimpleNamespace(bounding_box=SimpleNamespace(origin_x=0, origin_y=0, width=0, height=0), categories=[]),
    ])
    boxes = boxes_from_detections(result, 320, 240)
    assert boxes[0] == Box(0.1, 0.1, 0.3, 0.3, "cup", 0.9)
    assert (boxes[1].x0, boxes[1].x1, boxes[1].y1) == (0.0, 1.0, 1.0)
    assert len(boxes) == 2
    assert boxes_from_detections(SimpleNamespace(detections=None), 320, 240) == []


def test_detector_runs_every_third_frame() -> None:
    schedule = EveryNth(3)
    assert [schedule.due() for _ in range(7)] == [True, False, False, True, False, False, True]
    with pytest.raises(ValueError):
        EveryNth(0)


def test_lock_uses_the_colors_rule() -> None:
    lock = ObjectLock(12)
    assert [lock.update("CUP") for _ in range(11)] == [False] * 11
    assert lock.update("CUP") is True        # 12th frame: STABLE once
    assert lock.update("CUP") is False and lock.latched  # held: no second STABLE
    assert lock.update("BOTTLE") is False and lock.streak == 1 and not lock.latched  # change resets
    lock.reset()
    assert (lock.key, lock.streak, lock.latched) == (None, 0, False)


# --- pipeline ---------------------------------------------------------------------------------------------


def hand(ray: Ray2D) -> list[tuple[float, float]]:
    points = [(ray.origin.x, ray.origin.y)] * 21
    points[8] = (ray.through.x, ray.through.y)
    return points


def test_pipeline_locks_on_the_pointed_object() -> None:
    pipeline = ObjectPointPipeline(stable_frames=12)
    cup = box(0.4, 0.5, 0.6, 0.6, "cup", 0.87)
    frames = [pipeline.process(hand(UP), [cup]) for _ in range(14)]
    assert frames[0].label == "CUP (0.87)" and frames[0].status == "CANDIDATE"
    assert frames[11].just_locked and frames[11].label == "LOCKED: CUP (0.87)"
    assert frames[11].summary() == "state=STABLE target=CUP confidence=0.87 frames=12/12 via ray"
    assert frames[13].locked and not frames[13].just_locked
    assert sum(f.just_locked for f in frames) == 1


def test_pipeline_region_lock_and_release() -> None:
    pipeline = ObjectPointPipeline(stable_frames=3)
    frames = [pipeline.process(hand(UP), []) for _ in range(3)]
    assert frames[-1].label == "LOCKED: OBJECT"
    gone = pipeline.process(None, [])
    assert gone.status == "NO HAND" and gone.streak == 0
    assert pipeline.process([(0.5, 0.9)] * 21, []).status == "NO RAY"  # landmarks 5 and 8 overlap


# --- objects-mode viewer loop (fakes) --------------------------------------------------------------------


class FakeCv2:
    COLOR_BGR2RGB = FONT_HERSHEY_SIMPLEX = LINE_AA = WND_PROP_VISIBLE = INTER_AREA = 0

    def __init__(self, frames: int) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.keys = [-1] * (frames - 1) + [ord("q")]

    def VideoCapture(self, index: int) -> Any:  # noqa: N802
        return SimpleNamespace(isOpened=lambda: True, read=lambda: (True, SimpleNamespace(shape=(480, 640, 3))), release=lambda: None)

    def resize(self, frame: Any, size: tuple[int, int], interpolation: int) -> Any:
        self.calls.append(("resize", size))
        return SimpleNamespace(shape=(size[1], size[0], 3))

    def cvtColor(self, frame: Any, code: int) -> Any:  # noqa: N802
        return frame

    def flip(self, frame: Any, code: int) -> Any:
        return frame

    def getTextSize(self, *args: Any) -> tuple[tuple[int, int], int]:  # noqa: N802
        return (100, 12), 4

    def waitKey(self, delay: int) -> int:  # noqa: N802
        return self.keys.pop(0) if self.keys else -1

    def getWindowProperty(self, *args: Any) -> float:  # noqa: N802
        return 1.0

    def destroyAllWindows(self) -> None:  # noqa: N802
        pass

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: self.calls.append((name, args))


def test_objects_mode_runs_detector_every_third_frame_and_locks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    detected: list[int] = []

    class Detector:
        def detect_for_video(self, image: Any, ts: int) -> Any:
            detected.append(ts)
            return SimpleNamespace(detections=[detection(128, 96, 64, 48, "cup", 0.87)])  # (0.4..0.6, 0.4..0.6) at 320x240

        def close(self) -> None:
            pass

    class Landmarker:
        def detect_for_video(self, image: Any, ts: int) -> Any:
            return SimpleNamespace(hand_landmarks=[[SimpleNamespace(x=x, y=y) for x, y in hand(UP)]])

        def close(self) -> None:
            pass

    monkeypatch.setattr(camera_debug, "create_landmarker", lambda v, p: Landmarker())
    monkeypatch.setattr(camera_debug, "create_object_detector", lambda v, p: Detector())
    for name in ("hand.task", "objects.tflite"):
        (tmp_path / name).write_bytes(b"fake")
    cv2 = FakeCv2(frames=13)
    vision = camera_debug.Vision(
        cv2=cv2, mp=SimpleNamespace(Image=lambda **kw: kw, ImageFormat=SimpleNamespace(SRGB=1)), base_options=None, vision=None
    )
    code = camera_debug.run(0, tmp_path / "hand.task", vision, mode="objects", object_model=tmp_path / "objects.tflite")
    assert code == camera_debug.EXIT_OK
    assert len(detected) == 5  # frames 1, 4, 7, 10, 13 of 13
    assert ("resize", (320, 240)) in cv2.calls
    texts = [args[1] for name, args in cv2.calls if name == "putText"]
    assert "CUP (0.87)" in texts and "LOCKED: CUP (0.87)" in texts
    assert capsys.readouterr().out.count("LOCKED: CUP (0.87)") == 1


def test_objects_mode_explains_a_missing_model(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "hand.task").write_bytes(b"fake")
    vision = camera_debug.Vision(cv2=FakeCv2(1), mp=None, base_options=None, vision=None)
    code = camera_debug.run(0, tmp_path / "hand.task", vision, mode="objects", object_model=tmp_path / "missing.tflite")
    assert code == camera_debug.EXIT_SETUP
    err = capsys.readouterr().err
    assert "Object detection model not found" in err and camera_debug.OBJECT_MODEL_URL in err
