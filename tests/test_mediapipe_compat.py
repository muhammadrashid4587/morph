"""MediaPipe API resolution for the camera viewer, with fake module layouts.

Mac layout: `from mediapipe.tasks.python import BaseOptions, vision` has HandLandmarker.
Pi layout (MediaPipe 1.0.1): that `vision` has no HandLandmarker, but `mp.tasks.vision` does.
No real cv2 / mediapipe is imported here.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from morph_pi import camera_debug
from morph_pi.camera_debug import VisionApiError, resolve_image_api, resolve_mediapipe_api


def full_vision(tag: str) -> SimpleNamespace:
    return SimpleNamespace(
        HandLandmarker=f"{tag}.HandLandmarker", HandLandmarkerOptions=f"{tag}.Options", RunningMode=f"{tag}.RunningMode"
    )


FORMAT = SimpleNamespace(SRGB="srgb")


def pi_layout() -> tuple[Any, dict[str, Any]]:
    mp = SimpleNamespace(
        __version__="1.0.1",
        tasks=SimpleNamespace(vision=full_vision("pi"), BaseOptions="pi.BaseOptions"),
        Image="mp.Image",
        ImageFormat=FORMAT,
    )
    modules = {"mediapipe.tasks.python": SimpleNamespace(BaseOptions="py.BaseOptions", vision=SimpleNamespace())}
    return mp, modules


def mac_layout() -> tuple[Any, dict[str, Any]]:
    mp = SimpleNamespace(__version__="0.10.21", Image="mp.Image", ImageFormat=FORMAT)
    modules = {"mediapipe.tasks.python": SimpleNamespace(BaseOptions="py.BaseOptions", vision=full_vision("py"))}
    return mp, modules


def importer(modules: dict[str, Any]) -> Any:
    def import_module(name: str) -> Any:
        if name not in modules:
            raise ModuleNotFoundError(name)
        return modules[name]

    return import_module


def test_pi_layout_uses_mp_tasks_vision() -> None:
    mp, modules = pi_layout()
    base_options, vision, api = resolve_mediapipe_api(mp, importer(modules))
    assert api == "mp.tasks.vision"
    assert vision is mp.tasks.vision
    assert base_options == "pi.BaseOptions"  # from the same namespace, not mixed with tasks.python


def test_mac_layout_is_unchanged() -> None:
    mp, modules = mac_layout()
    base_options, vision, api = resolve_mediapipe_api(mp, importer(modules))
    assert api == "mediapipe.tasks.python.vision"
    assert base_options == "py.BaseOptions" and vision.HandLandmarker == "py.HandLandmarker"


def test_mac_path_is_preferred_when_both_are_complete() -> None:
    mp, _ = pi_layout()
    _, modules = mac_layout()
    assert resolve_mediapipe_api(mp, importer(modules))[2] == "mediapipe.tasks.python.vision"


def test_vision_submodule_imported_when_not_an_attribute() -> None:
    mp = SimpleNamespace()
    modules = {
        "mediapipe.tasks.python": SimpleNamespace(BaseOptions="py.BaseOptions"),
        "mediapipe.tasks.python.vision": full_vision("sub"),
    }
    assert resolve_mediapipe_api(mp, importer(modules))[1].HandLandmarker == "sub.HandLandmarker"


def test_broken_tasks_python_import_falls_back_to_mp_tasks() -> None:
    mp, _ = pi_layout()
    assert resolve_mediapipe_api(mp, importer({}))[2] == "mp.tasks.vision"


def test_base_options_may_live_on_the_vision_namespace() -> None:
    vision = full_vision("pi")
    vision.BaseOptions = "vision.BaseOptions"
    mp = SimpleNamespace(tasks=SimpleNamespace(vision=vision))
    assert resolve_mediapipe_api(mp, importer({}))[0] == "vision.BaseOptions"


def test_incomplete_apis_raise_a_clear_error() -> None:
    incomplete = SimpleNamespace(HandLandmarker="x", RunningMode="y")  # no HandLandmarkerOptions
    mp = SimpleNamespace(__version__="9.9", tasks=SimpleNamespace(vision=incomplete, BaseOptions="b"))
    modules = {"mediapipe.tasks.python": SimpleNamespace(BaseOptions="b", vision=SimpleNamespace())}
    with pytest.raises(VisionApiError, match=r"MediaPipe 9\.9 .*mediapipe\.tasks\.python\.vision.*mp\.tasks\.vision"):
        resolve_mediapipe_api(mp, importer(modules))
    no_base = SimpleNamespace(tasks=SimpleNamespace(vision=full_vision("pi")))  # no BaseOptions anywhere
    with pytest.raises(VisionApiError):
        resolve_mediapipe_api(no_base, importer({}))


def test_image_api_resolution() -> None:
    mp, _ = pi_layout()
    assert resolve_image_api(mp, None) == ("mp.Image", FORMAT)
    vision = SimpleNamespace(Image="vision.Image", ImageFormat=FORMAT)
    assert resolve_image_api(SimpleNamespace(), vision) == ("vision.Image", FORMAT)
    with pytest.raises(VisionApiError, match="Image"):
        resolve_image_api(SimpleNamespace(Image="x", ImageFormat=SimpleNamespace()), SimpleNamespace())


def test_load_vision_with_pi_layout_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    mp, modules = pi_layout()
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(name="fake-cv2"))
    monkeypatch.setitem(sys.modules, "mediapipe", mp)
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    v = camera_debug.load_vision()
    assert v is not None
    assert v.api == "mp.tasks.vision" and v.base_options == "pi.BaseOptions"
    assert (v.image, v.image_format) == ("mp.Image", FORMAT)


def test_detect_hand_uses_the_resolved_image_class() -> None:
    made: list[dict] = []

    def image_cls(**kwargs: Any) -> str:
        made.append(kwargs)
        return "image"

    class Landmarker:
        def detect_for_video(self, image: Any, timestamp_ms: int) -> Any:
            assert image == "image"
            return SimpleNamespace(hand_landmarks=[])

    cv2 = SimpleNamespace(COLOR_BGR2RGB=0, cvtColor=lambda frame, code: "rgb")
    v = camera_debug.Vision(cv2=cv2, mp=SimpleNamespace(), base_options=None, vision=None,
                            image=image_cls, image_format=FORMAT, api="mp.tasks.vision")
    assert camera_debug.detect_hand(v, Landmarker(), frame=object(), timestamp_ms=1) is None
    assert made == [{"image_format": "srgb", "data": "rgb"}]


def test_run_reports_an_incompatible_mediapipe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> Any:
        raise VisionApiError("MediaPipe 9.9 has no complete Hand Landmarker API")

    monkeypatch.setattr(camera_debug, "load_vision", broken)
    assert camera_debug.run(0, tmp_path / "hand_landmarker.task") == camera_debug.EXIT_SETUP
    assert "error: MediaPipe 9.9 has no complete Hand Landmarker API" in capsys.readouterr().err
