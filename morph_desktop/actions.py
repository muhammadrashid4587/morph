"""Desktop action executors.

MockActionExecutor (default) never touches the machine: it logs what would
happen and records every action in memory. RealActionExecutor drives the
mouse/keyboard through pyautogui and is only created when
MORPH_REAL_ACTIONS=1 is set; any failure falls back to mock behavior.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from typing import Any, ClassVar

from .config import REAL_ACTION_PAUSE_S, VOLUME_STEP_PER_KEYPRESS
from .protocol import (
    VOLUME_MAX,
    VOLUME_MIN,
    Click,
    DesktopAction,
    MouseButton,
    MouseMove,
    NextSlide,
    PlayPause,
    PreviousSlide,
    SetVolume,
    VolumeDelta,
)

logger = logging.getLogger("morph.actions")

# Real mouse targets stay this many pixels away from the screen edges, so a
# normalized 0 or 1 never lands on a corner and trips pyautogui's failsafe.
EDGE_INSET_PX = 2


class ActionUnavailable(RuntimeError):
    """The action cannot be performed by this executor on this platform."""


class ActionExecutor(ABC):
    mode: ClassVar[str]

    def execute(self, action: DesktopAction) -> None:
        match action:
            case NextSlide():
                self.next_slide()
            case PreviousSlide():
                self.previous_slide()
            case SetVolume(value=value):
                self.set_volume(value)
            case VolumeDelta(delta=delta):
                self.volume_delta(delta)
            case PlayPause():
                self.play_pause()
            case MouseMove(x=x, y=y):
                self.mouse_move(x, y)
            case Click(button=button):
                self.click(button)
            case _:
                raise TypeError(f"not a desktop action: {action!r}")

    @abstractmethod
    def next_slide(self) -> None: ...

    @abstractmethod
    def previous_slide(self) -> None: ...

    @abstractmethod
    def set_volume(self, value: int) -> None: ...

    @abstractmethod
    def volume_delta(self, delta: int) -> None: ...

    @abstractmethod
    def play_pause(self) -> None: ...

    @abstractmethod
    def mouse_move(self, x: float, y: float) -> None: ...

    @abstractmethod
    def click(self, button: MouseButton) -> None: ...


class MockActionExecutor(ActionExecutor):
    """Safe default: logs intended actions and records them in memory."""

    mode = "mock"

    def __init__(self, history_limit: int = 1000, initial_volume: int = 50) -> None:
        self.history: deque[DesktopAction] = deque(maxlen=history_limit)
        self.volume = initial_volume  # simulated, so volume_delta logs are meaningful

    def execute(self, action: DesktopAction) -> None:
        self.history.append(action)
        super().execute(action)

    def next_slide(self) -> None:
        logger.info("[MOCK] next slide")

    def previous_slide(self) -> None:
        logger.info("[MOCK] previous slide")

    def set_volume(self, value: int) -> None:
        self.volume = value
        logger.info("[MOCK] set volume to %d", value)

    def volume_delta(self, delta: int) -> None:
        before = self.volume
        self.volume = max(VOLUME_MIN, min(VOLUME_MAX, before + delta))
        logger.info("[MOCK] change volume by %+d (simulated %d -> %d)", delta, before, self.volume)

    def play_pause(self) -> None:
        logger.info("[MOCK] toggle play/pause")

    def mouse_move(self, x: float, y: float) -> None:
        logger.info("[MOCK] move mouse to normalized coordinates %.2f, %.2f", x, y)

    def click(self, button: MouseButton) -> None:
        logger.info("[MOCK] %s click", button)


def to_pixel(normalized: float, size: int, inset: int = EDGE_INSET_PX) -> int:
    """Map a 0..1 coordinate onto [inset, size - 1 - inset]."""
    lo, hi = inset, size - 1 - inset
    if hi < lo:
        return max(0, size // 2)
    return round(lo + normalized * (hi - lo))


class RealActionExecutor(ActionExecutor):
    """Drives the real mouse/keyboard via pyautogui. Opt-in only.

    On any unexpected error (including pyautogui's corner failsafe) real
    control is disabled for the rest of the session and actions continue
    in mock mode.
    """

    mode = "real"

    def __init__(
        self,
        gui: Any,
        pause_s: float = REAL_ACTION_PAUSE_S,
        fallback: MockActionExecutor | None = None,
    ) -> None:
        self._gui = gui
        self._gui.PAUSE = pause_s
        self._gui.FAILSAFE = True
        self._fallback = fallback or MockActionExecutor()
        self.disabled = False

    def execute(self, action: DesktopAction) -> None:
        if self.disabled:
            self._fallback.execute(action)
            return
        try:
            super().execute(action)
        except ActionUnavailable:
            raise
        except Exception as exc:
            self.disabled = True
            logger.error(
                "[REAL] %s failed (%s: %s); real control DISABLED, falling back to mock",
                action.name, type(exc).__name__, exc,
            )
            self._fallback.execute(action)

    def _supports_key(self, key: str) -> bool:
        is_valid = getattr(self._gui, "isValidKey", None)
        return bool(is_valid and is_valid(key))

    def next_slide(self) -> None:
        logger.info("[REAL] next slide (Right arrow)")
        self._gui.press("right")

    def previous_slide(self) -> None:
        logger.info("[REAL] previous slide (Left arrow)")
        self._gui.press("left")

    def set_volume(self, value: int) -> None:
        logger.warning("[REAL] set_volume %d unavailable: media keys cannot set an absolute level", value)
        raise ActionUnavailable(
            "set_volume is unavailable in real mode (no absolute volume via media keys); use volume_delta"
        )

    def volume_delta(self, delta: int) -> None:
        if delta == 0:
            logger.info("[REAL] volume_delta 0: nothing to do")
            return
        key = "volumeup" if delta > 0 else "volumedown"
        if not self._supports_key(key):
            logger.warning("[REAL] volume media keys unavailable on this platform")
            raise ActionUnavailable("volume media keys are not supported on this platform")
        presses = max(1, round(abs(delta) / VOLUME_STEP_PER_KEYPRESS))
        logger.info("[REAL] volume %+d (%d x %s)", delta, presses, key)
        self._gui.press(key, presses=presses)

    def play_pause(self) -> None:
        if not self._supports_key("playpause"):
            logger.warning("[REAL] play/pause media key unavailable on this platform")
            raise ActionUnavailable("play/pause media key is not supported on this platform")
        logger.info("[REAL] toggle play/pause")
        self._gui.press("playpause")

    def mouse_move(self, x: float, y: float) -> None:
        width, height = self._gui.size()
        px, py = to_pixel(x, width), to_pixel(y, height)
        logger.info("[REAL] move mouse to %.2f, %.2f -> pixel (%d, %d) on %dx%d", x, y, px, py, width, height)
        self._gui.moveTo(px, py)

    def click(self, button: MouseButton) -> None:
        logger.info("[REAL] %s click", button)
        self._gui.click(button=button)


def load_pyautogui() -> Any:
    import pyautogui  # optional dependency; imported only when real mode is requested

    return pyautogui


def create_executor(
    real_actions: bool,
    loader: Callable[[], Any] = load_pyautogui,
) -> ActionExecutor:
    """Build the executor for this run. Mock unless real actions were requested AND work."""
    if not real_actions:
        logger.info("MOCK mode: no real mouse/keyboard control (set MORPH_REAL_ACTIONS=1 to opt in)")
        return MockActionExecutor()
    try:
        executor = RealActionExecutor(loader())
    except Exception as exc:  # ImportError, or display/backend errors from pyautogui
        logger.error(
            "MORPH_REAL_ACTIONS=1 but pyautogui is unavailable (%s: %s); falling back to MOCK mode",
            type(exc).__name__, exc,
        )
        return MockActionExecutor()
    _warn_real_mode()
    return executor


def _warn_real_mode() -> None:
    banner = "!" * 68
    for line in (
        banner,
        "REAL ACTIONS ENABLED: MORPH will control this machine's mouse and keyboard.",
        "Use only under direct supervision. Emergency stop: Ctrl+C, or slam the",
        "mouse into a screen corner (pyautogui failsafe) to disable real control.",
        banner,
    ):
        logger.warning(line)
