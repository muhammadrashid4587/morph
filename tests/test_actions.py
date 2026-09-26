"""Executor tests. RealActionExecutor is exercised only against FakeGui,
so nothing here ever moves the real mouse or presses real keys."""

from typing import Any

import pytest

from morph_desktop.actions import (
    ActionUnavailable,
    MockActionExecutor,
    RealActionExecutor,
    create_executor,
    to_pixel,
)
from morph_desktop.protocol import (
    Click,
    MouseMove,
    NextSlide,
    PlayPause,
    PreviousSlide,
    SetVolume,
    VolumeDelta,
)
from morph_desktop.server import MorphAgent

# --- mock executor ------------------------------------------------------------


def test_mock_records_actions_in_order() -> None:
    mock = MockActionExecutor()
    actions = [
        NextSlide(), PreviousSlide(), SetVolume(60), VolumeDelta(-10),
        PlayPause(), MouseMove(0.5, 0.5), Click("left"),
    ]
    for action in actions:
        mock.execute(action)
    assert list(mock.history) == actions


def test_mock_logs_what_would_happen(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="morph.actions")
    mock = MockActionExecutor()
    mock.execute(SetVolume(60))
    mock.execute(NextSlide())
    mock.execute(MouseMove(0.75, 0.5))
    assert "[MOCK] set volume to 60" in caplog.text
    assert "[MOCK] next slide" in caplog.text
    assert "[MOCK] move mouse to normalized coordinates 0.75, 0.50" in caplog.text


def test_mock_simulated_volume_is_clamped() -> None:
    mock = MockActionExecutor(initial_volume=5)
    mock.execute(VolumeDelta(-10))
    assert mock.volume == 0
    mock.execute(SetVolume(95))
    mock.execute(VolumeDelta(10))
    assert mock.volume == 100


def test_mock_history_is_bounded() -> None:
    mock = MockActionExecutor(history_limit=3)
    for _ in range(10):
        mock.execute(NextSlide())
    assert len(mock.history) == 3


def test_agent_records_only_accepted_desktop_actions() -> None:
    mock = MockActionExecutor()
    agent = MorphAgent(mock)
    assert agent.handle_line('{"action":"next_slide"}').response == {"status": "ok", "action": "next_slide"}
    assert agent.handle_line('{"action":"set_volume","value":101}').response["status"] == "error"
    assert agent.handle_line("garbage").response["status"] == "error"
    assert agent.handle_line('{"action":"ping"}').response["status"] == "pong"
    assert agent.handle_line('{"action":"set_context","context":"music"}').response["status"] == "context"
    assert list(mock.history) == [NextSlide()]


def test_default_executor_is_mock() -> None:
    def must_not_load() -> Any:
        raise AssertionError("pyautogui must not be loaded in mock mode")

    assert isinstance(create_executor(False, loader=must_not_load), MockActionExecutor)


def test_real_mode_falls_back_to_mock_without_pyautogui() -> None:
    def missing() -> Any:
        raise ImportError("No module named 'pyautogui'")

    assert isinstance(create_executor(True, loader=missing), MockActionExecutor)


# --- real executor against a fake GUI backend ----------------------------------------


class FakeGui:
    """Stands in for the pyautogui module; records calls instead of acting."""

    def __init__(self, keys: set[str] | None = None, fail: bool = False) -> None:
        self.PAUSE = 0.0
        self.FAILSAFE = False
        self.calls: list[tuple[Any, ...]] = []
        self._keys = keys if keys is not None else {"right", "left", "volumeup", "volumedown", "playpause"}
        self._fail = fail

    def _record(self, *call: Any) -> None:
        if self._fail:
            raise RuntimeError("backend exploded")
        self.calls.append(call)

    def isValidKey(self, key: str) -> bool:  # noqa: N802 (pyautogui's name)
        return key in self._keys

    def press(self, key: str, presses: int = 1) -> None:
        self._record("press", key, presses)

    def size(self) -> tuple[int, int]:
        return (1920, 1080)

    def moveTo(self, x: int, y: int) -> None:  # noqa: N802
        self._record("moveTo", x, y)

    def click(self, button: str = "left") -> None:
        self._record("click", button)


def test_real_executor_configures_pause_and_failsafe() -> None:
    gui = FakeGui()
    RealActionExecutor(gui)
    assert gui.PAUSE == 0.2
    assert gui.FAILSAFE is True


def test_real_executor_maps_actions_to_gui_calls() -> None:
    gui = FakeGui()
    real = RealActionExecutor(gui)
    real.execute(NextSlide())
    real.execute(PreviousSlide())
    real.execute(VolumeDelta(-10))
    real.execute(PlayPause())
    real.execute(MouseMove(0.5, 0.5))
    real.execute(Click("right"))
    assert gui.calls == [
        ("press", "right", 1),
        ("press", "left", 1),
        ("press", "volumedown", 2),
        ("press", "playpause", 1),
        ("moveTo", 960, 540),
        ("click", "right"),
    ]


def test_real_mouse_never_hits_screen_corners() -> None:
    gui = FakeGui()
    real = RealActionExecutor(gui)
    real.execute(MouseMove(0.0, 0.0))
    real.execute(MouseMove(1.0, 1.0))
    assert gui.calls == [("moveTo", 2, 2), ("moveTo", 1917, 1077)]


def test_to_pixel_handles_tiny_screens() -> None:
    assert to_pixel(0.5, 3) == 1
    assert to_pixel(1.0, 1) == 0


def test_real_set_volume_is_unavailable() -> None:
    real = RealActionExecutor(FakeGui())
    with pytest.raises(ActionUnavailable):
        real.execute(SetVolume(60))


def test_real_media_keys_unavailable_are_reported() -> None:
    gui = FakeGui(keys={"right", "left"})
    real = RealActionExecutor(gui)
    with pytest.raises(ActionUnavailable):
        real.execute(VolumeDelta(5))
    with pytest.raises(ActionUnavailable):
        real.execute(PlayPause())
    assert gui.calls == []


def test_agent_turns_unavailable_into_error_response() -> None:
    agent = MorphAgent(RealActionExecutor(FakeGui()))
    reply = agent.handle_line('{"action":"set_volume","value":60}')
    assert reply.response["status"] == "error"
    assert "unavailable" in reply.response["message"]


def test_real_executor_failure_falls_back_to_mock_permanently() -> None:
    fallback = MockActionExecutor()
    real = RealActionExecutor(FakeGui(fail=True), fallback=fallback)
    real.execute(NextSlide())  # backend raises -> disabled, mock takes over
    assert real.disabled is True
    real.execute(Click("left"))
    assert list(fallback.history) == [NextSlide(), Click("left")]
