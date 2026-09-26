"""Live POINT pipeline and its viewer integration. No camera, cv2 or mediapipe."""

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from morph_pi import camera_debug, live_point
from morph_pi.live_point import PointPipeline, short_name
from morph_pi.models import Ray2D, SelectionState, TargetConfig, TargetId
from morph_pi.simulate import ambiguous_ray, clear_ray
from morph_pi.smoothing import TemporalSmoother
from morph_pi.targets import config_to_dict

CONFIG = TargetConfig()
S = SelectionState
N = CONFIG.stable_frames


def hand_for(ray: Ray2D) -> list[tuple[float, float]]:
    """21 valid landmarks whose index MCP (5) and fingertip (8) form `ray`."""
    points = [(ray.origin.x, ray.origin.y)] * 21
    points[8] = (ray.through.x, ray.through.y)
    return points


BLUE_HAND = hand_for(clear_ray(CONFIG.target(TargetId.BLUE_BLOCK)))
YELLOW_HAND = hand_for(clear_ray(CONFIG.target(TargetId.YELLOW_BLOCK)))
AMBIGUOUS_HAND = hand_for(ambiguous_ray(CONFIG.target(TargetId.BLUE_BLOCK), CONFIG.target(TargetId.YELLOW_BLOCK)))
_BLUE_RAY = clear_ray(CONFIG.target(TargetId.BLUE_BLOCK))
AWAY_HAND = hand_for(Ray2D(_BLUE_RAY.through, _BLUE_RAY.origin))  # reversed: points back at the user
DEGENERATE_HAND = [(0.5, 0.9)] * 21


# --- pipeline: wiring ---------------------------------------------------------------------------------


def test_smoother_is_created_once_and_reused() -> None:
    pipeline = PointPipeline(CONFIG)
    smoother = pipeline.smoother
    frames = [pipeline.process(BLUE_HAND) for _ in range(N + 5)]
    assert pipeline.smoother is smoother  # never replaced
    # A 12-frame streak can only build up if the same smoother saw every frame.
    assert [f.streak for f in frames[:N]] == list(range(1, N + 1))
    assert [f.just_locked for f in frames].count(True) == 1 and frames[N - 1].just_locked


def test_each_frame_calls_select_target_then_the_smoother(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []
    real_select = live_point.select_target

    def spy_select(ray: Ray2D, config: TargetConfig) -> Any:
        result = real_select(ray, config)
        calls.append(("select_target", (ray, config)))
        return result

    monkeypatch.setattr(live_point, "select_target", spy_select)
    pipeline = PointPipeline(CONFIG)
    real_update, real_reset = pipeline.smoother.update, pipeline.smoother.reset
    monkeypatch.setattr(pipeline.smoother, "update", lambda r: (calls.append(("update", r)), real_update(r))[1])
    monkeypatch.setattr(pipeline.smoother, "reset", lambda: (calls.append(("reset", None)), real_reset())[1])

    frame = pipeline.process(BLUE_HAND)
    # (update() may call reset() internally when a new streak starts; order of the first two is what matters)
    (name1, (ray, config)), (name2, fed) = calls[:2]
    assert (name1, name2) == ("select_target", "update")
    assert ray == frame.ray and config is CONFIG
    assert fed.state is S.CANDIDATE and fed.stable_target is None  # per-frame result, not yet smoothed
    assert frame.result is not None and frame.result.target_id is TargetId.BLUE_BLOCK

    calls.clear()
    pipeline.process(None)
    pipeline.process([])
    pipeline.process(DEGENERATE_HAND)
    assert calls == [("reset", None)] * 3  # no selection without a usable ray


# --- pipeline: states ------------------------------------------------------------------------------------


def test_no_hand_resets_and_reports() -> None:
    pipeline = PointPipeline(CONFIG)
    for _ in range(N - 1):
        pipeline.process(BLUE_HAND)
    frame = pipeline.process(None)
    assert (frame.status, frame.streak, frame.hand_detected) == ("NO HAND", 0, False)
    assert frame.summary() == "state=NONE target=- confidence=0.00 frames=0/12 (no hand)"
    assert not pipeline.process(BLUE_HAND).just_locked  # 11 + 1 does not add up across the gap


def test_no_ray_resets_and_reports() -> None:
    pipeline = PointPipeline(CONFIG)
    for _ in range(5):
        pipeline.process(BLUE_HAND)
    frame = pipeline.process(DEGENERATE_HAND)
    assert (frame.status, frame.streak, frame.ray) == ("NO RAY", 0, None)
    assert frame.summary().endswith("frames=0/12 (no ray)")


def test_config_min_ray_length_is_used() -> None:
    strict = dataclasses.replace(CONFIG, min_ray_length=0.2)  # synthetic finger is 0.08 long
    assert PointPipeline(strict).process(BLUE_HAND).status == "NO RAY"


def test_candidate_then_locked_while_held() -> None:
    pipeline = PointPipeline(CONFIG)
    frames = [pipeline.process(BLUE_HAND) for _ in range(N + 3)]
    assert frames[4].status == "CANDIDATE"
    assert frames[4].summary() == "state=CANDIDATE target=BLUE confidence=0.96 frames=5/12"
    assert frames[N - 1].state is S.STABLE and frames[N - 1].status == "LOCKED"
    assert frames[N - 1].summary() == "state=STABLE target=BLUE confidence=0.96 frames=12/12"
    held = frames[N]
    assert held.state is S.CANDIDATE and held.locked and not held.just_locked  # STABLE emitted once
    assert held.status == "LOCKED"
    assert held.summary() == "state=CANDIDATE target=BLUE confidence=0.96 frames=12/12 (locked)"


def test_switching_target_unlocks_and_restarts() -> None:
    pipeline = PointPipeline(CONFIG)
    for _ in range(N):
        pipeline.process(BLUE_HAND)
    frame = pipeline.process(YELLOW_HAND)
    assert frame.status == "CANDIDATE" and frame.streak == 1 and not frame.locked
    assert frame.result is not None and frame.result.target_id is TargetId.YELLOW_BLOCK


def test_ambiguous_and_no_target() -> None:
    pipeline = PointPipeline(CONFIG)
    ambiguous = pipeline.process(AMBIGUOUS_HAND)
    assert ambiguous.status == "AMBIGUOUS" and ambiguous.streak == 0
    away = pipeline.process(AWAY_HAND)
    assert away.status == "NO TARGET" and away.hand_detected and away.ray is not None


def test_short_name() -> None:
    assert [short_name(t) for t in TargetId] == ["BLUE", "YELLOW", "GREEN"]


# --- display helpers (pure) ---------------------------------------------------------------------------------


def test_target_zones_are_mirrored_and_scaled() -> None:
    zones = {z.id: z for z in camera_debug.target_zones(CONFIG, 641, 481)}
    # camera-space x 0.20 is shown at 1 - 0.20 = 0.80 of the width (mirror)
    assert zones[TargetId.BLUE_BLOCK].center == (512, 298)
    assert zones[TargetId.YELLOW_BLOCK].center == (320, 298)
    assert zones[TargetId.GREEN_BLOCK].center == (128, 298)
    assert zones[TargetId.BLUE_BLOCK].axes == (77, 58)  # 0.12 of width and height
    assert zones[TargetId.GREEN_BLOCK].name == "GREEN"


def test_zone_matches_selection_geometry() -> None:
    """A ray that just grazes the zone edge is (barely) a valid candidate; just outside is not."""
    from morph_pi.models import Point2D
    from morph_pi.selection import score_targets

    inside = Ray2D(Point2D(0.2 + 0.119, 0.95), Point2D(0.2 + 0.119, 0.85))
    outside = Ray2D(Point2D(0.2 + 0.121, 0.95), Point2D(0.2 + 0.121, 0.85))
    assert [c.target.id for c in score_targets(inside, CONFIG)] == [TargetId.BLUE_BLOCK]
    assert score_targets(outside, CONFIG) == []


def test_selection_status_texts() -> None:
    pipeline = PointPipeline(CONFIG)
    assert camera_debug.selection_status(pipeline.process(None)) is None  # NO HAND shown elsewhere
    assert camera_debug.selection_status(pipeline.process(DEGENERATE_HAND)) == ("NO RAY", camera_debug.YELLOW)
    assert camera_debug.selection_status(pipeline.process(AWAY_HAND)) == ("NO TARGET", camera_debug.WHITE)
    assert camera_debug.selection_status(pipeline.process(AMBIGUOUS_HAND)) == ("AMBIGUOUS", camera_debug.ORANGE)
    assert camera_debug.selection_status(pipeline.process(BLUE_HAND)) == ("CANDIDATE: BLUE 96%", camera_debug.WHITE)
    for _ in range(N):
        frame = pipeline.process(BLUE_HAND)
    assert camera_debug.selection_status(frame) == ("LOCKED: BLUE", camera_debug.TARGET_COLORS[TargetId.BLUE_BLOCK])


def test_pulse_is_bounded() -> None:
    values = [camera_debug.pulse(t / 100) for t in range(200)]
    assert min(values) >= 0.0 and max(values) <= 1.0 and max(values) - min(values) > 0.9


# --- viewer loop with fakes ------------------------------------------------------------------------------------


class FakeCapture:
    released = False

    def isOpened(self) -> bool:  # noqa: N802
        return True

    def read(self) -> tuple[bool, Any]:
        return True, SimpleNamespace(shape=(480, 640, 3))

    def release(self) -> None:
        self.released = True


class FakeCv2:
    COLOR_BGR2RGB = FONT_HERSHEY_SIMPLEX = LINE_AA = WND_PROP_VISIBLE = 0

    def __init__(self, frames_before_quit: int) -> None:
        self.capture, self.calls = FakeCapture(), []
        self.keys = [-1] * (frames_before_quit - 1) + [ord("q")]

    def VideoCapture(self, index: int) -> FakeCapture:  # noqa: N802
        return self.capture

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

    def __getattr__(self, name: str) -> Any:  # line, circle, ellipse, rectangle, putText, imshow
        return lambda *args, **kwargs: self.calls.append((name, args))


class FakeLandmarker:
    def __init__(self, hands: list[Any]) -> None:
        self.hands = hands

    def detect_for_video(self, image: Any, timestamp_ms: int) -> Any:
        hand = self.hands.pop(0) if self.hands else None
        return SimpleNamespace(hand_landmarks=[] if hand is None else [[SimpleNamespace(x=x, y=y) for x, y in hand]])

    def close(self) -> None:
        pass


def run_viewer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, hands: list[Any]) -> tuple[FakeCv2, list[PointPipeline]]:
    created: list[PointPipeline] = []

    class CountingPipeline(PointPipeline):
        def __init__(self, config: TargetConfig | None = None) -> None:
            super().__init__(config)
            created.append(self)

    monkeypatch.setattr(camera_debug, "PointPipeline", CountingPipeline)
    monkeypatch.setattr(camera_debug, "create_landmarker", lambda v, p: FakeLandmarker(list(hands)))
    model = tmp_path / "hand_landmarker.task"
    model.write_bytes(b"fake")
    cv2 = FakeCv2(frames_before_quit=len(hands))
    vision = camera_debug.Vision(
        cv2=cv2, mp=SimpleNamespace(Image=lambda **kw: kw, ImageFormat=SimpleNamespace(SRGB=1)), base_options=None, vision=None
    )
    assert camera_debug.run(0, model, vision) == camera_debug.EXIT_OK
    return cv2, created


def texts(cv2: FakeCv2) -> list[str]:
    return [args[1] for name, args in cv2.calls if name == "putText"]


def test_viewer_uses_one_pipeline_and_locks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cv2, created = run_viewer(monkeypatch, tmp_path, [BLUE_HAND] * (N + 3))
    assert len(created) == 1  # one pipeline (one smoother) for the whole session
    drawn = texts(cv2)
    assert "CANDIDATE: BLUE 96%" in drawn and "LOCKED: BLUE" in drawn
    assert "state=CANDIDATE target=BLUE confidence=0.96 frames=5/12" in drawn
    assert "state=STABLE target=BLUE confidence=0.96 frames=12/12" in drawn
    assert {"YELLOW", "GREEN", "BLUE 96%"} <= set(drawn)  # zone labels (blue shows its confidence)
    assert sum(name == "ellipse" for name, _ in cv2.calls) >= 3 * (N + 3)  # three zones every frame
    out = capsys.readouterr().out
    assert out.count("LOCKED: BLUE (confidence 0.96)") == 1  # STABLE event printed once per gesture


def test_viewer_resets_on_no_hand(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hands = [BLUE_HAND] * (N - 1) + [None] + [BLUE_HAND] * (N - 1) + [AMBIGUOUS_HAND, DEGENERATE_HAND]
    cv2, created = run_viewer(monkeypatch, tmp_path, hands)
    drawn = texts(cv2)
    assert "NO HAND" in drawn and "AMBIGUOUS" in drawn and "NO RAY" in drawn
    assert not any(t.startswith("LOCKED") for t in drawn)  # 11 + 11 never locks
    assert "LOCKED" not in capsys.readouterr().out
    assert created[0].smoother.streak == 0


# --- CLI --config -------------------------------------------------------------------------------------------------


def test_cli_passes_custom_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = config_to_dict(CONFIG)
    data["thresholds"]["stable_frames"] = 6
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(data))
    seen: dict[str, Any] = {}
    monkeypatch.setattr(camera_debug, "run", lambda camera, model, config=None: seen.update(config=config) or 0)
    assert camera_debug.main(["--config", str(path)]) == 0
    assert seen["config"].stable_frames == 6


def test_cli_invalid_config_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"targets": {}}')
    assert camera_debug.main(["--config", str(path)]) == camera_debug.EXIT_USAGE
    assert "missing target" in capsys.readouterr().err


def test_default_smoother_rule_is_twelve_frames() -> None:
    assert PointPipeline().smoother.stable_frames == 12 == TemporalSmoother().stable_frames
