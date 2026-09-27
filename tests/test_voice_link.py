"""--speak: spoken lock announcements and V push-to-talk, without microphone, speaker, network or keys."""

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from morph_agent.actions import AgentAction, MorphState
from morph_agent.executor import DryRunExecutor
from morph_pi import camera_debug
from morph_pi.voice_link import VoiceLink, lock_phrase
from morph_voice import speech


class FakeRecorder:
    def __init__(self, wav: bytes = b"RIFF-fake-wav", fail: Exception | None = None) -> None:
        self.recording = False
        self.wav, self.fail = wav, fail
        self.starts = 0

    def start(self) -> None:
        self.starts += 1
        self.recording = True

    def stop(self) -> bytes:
        self.recording = False
        if self.fail:
            raise self.fail
        return self.wav


class FakeAgent:
    def __init__(self, action: AgentAction) -> None:
        self.action = action
        self.seen: list[tuple[str, MorphState]] = []

    def decide(self, transcript: str, state: MorphState) -> AgentAction:
        self.seen.append((transcript, state))
        return self.action


def link(say: Any, agent: Any = None, recorder: Any = None, transcribe: Any = None, out: list | None = None) -> VoiceLink:
    lines = out if out is not None else []
    return VoiceLink(
        say=say,
        transcribe=transcribe or (lambda wav: "what am I pointing at?"),
        recorder=recorder or FakeRecorder(),
        agent=agent or FakeAgent(AgentAction("say", None, "That's a cup.")),
        executor=DryRunExecutor(lines.append),
        out=lines.append,
    )


def test_lock_phrases() -> None:
    assert lock_phrase("cup") == "That's a cup."
    assert lock_phrase("apple") == "That's an apple."
    assert lock_phrase("cell  phone") == "That's a cell phone."
    assert lock_phrase("OBJECT") == "Locked on that."
    assert lock_phrase("") == "Locked on that."


def test_announce_runs_in_the_background_and_skips_while_speaking() -> None:
    release = threading.Event()
    spoken: list[str] = []

    def slow_say(text: str) -> None:
        spoken.append(text)
        release.wait(5)

    voice = link(slow_say)
    started = time.monotonic()
    assert voice.announce_lock("cup") is True
    assert time.monotonic() - started < 0.5  # the video loop is not blocked by speech
    assert voice.status == "SPEAKING" and voice.busy
    assert voice.announce_lock("laptop") is False  # already speaking: skipped, not queued
    release.set()
    voice.wait()
    assert spoken == ["That's a cup."]
    assert not voice.busy and voice.status == ""
    assert voice.announce_lock("OBJECT") is True
    voice.wait()
    assert spoken == ["That's a cup.", "Locked on that."]


def test_push_to_talk_sends_the_locked_target_to_the_agent_and_speaks_the_reply() -> None:
    spoken: list[str] = []
    lines: list[str] = []
    agent = FakeAgent(AgentAction("say", None, "That's a coffee cup."))
    recorder = FakeRecorder()
    voice = link(spoken.append, agent, recorder, out=lines)
    assert voice.toggle_talk("cup") == "listening"
    assert recorder.recording and voice.status.startswith("LISTENING")
    assert voice.announce_lock("cup") is False  # no announcements while listening
    assert voice.toggle_talk("laptop") == "answering"  # target captured when V was first pressed
    voice.wait()
    transcript, state = agent.seen[0]
    assert transcript == "what am I pointing at?"
    assert state == MorphState("robot_targeting", "cup")
    assert spoken == ["That's a coffee cup."]
    assert "[DRY RUN] speech only, nothing to send" in lines


def test_v_while_busy_is_ignored_and_errors_never_escape() -> None:
    release = threading.Event()
    voice = link(lambda text: release.wait(5))
    voice.announce_lock("cup")
    assert voice.toggle_talk("cup") == "busy"
    release.set()
    voice.wait()

    lines: list[str] = []
    short = link(lambda text: None, recorder=FakeRecorder(fail=ValueError("recording too short")), out=lines)
    short.toggle_talk(None)
    assert short.toggle_talk(None) == "too short" and "voice: recording too short" in lines

    class NoMic(FakeRecorder):
        def start(self) -> None:
            raise RuntimeError("PortAudio missing")

    assert link(lambda text: None, recorder=NoMic(), out=lines).toggle_talk(None) == "no microphone"

    def broken_stt(wav: bytes) -> str:
        raise RuntimeError("network down")

    failing = link(lambda text: None, transcribe=broken_stt, out=lines)
    failing.toggle_talk(None)
    failing.toggle_talk(None)
    failing.wait()
    assert any("network down" in line for line in lines)


def test_unrecognized_region_and_odd_labels_reach_the_agent_safely() -> None:
    agent = FakeAgent(AgentAction("say", None, "I can't tell what that is."))
    voice = link(lambda text: None, agent)
    voice.toggle_talk("unrecognized object")
    voice.toggle_talk(None)
    voice.wait()
    assert agent.seen[0][1].locked_target == "unrecognized object"
    agent2 = FakeAgent(AgentAction("none", None, "x"))
    weird = link(lambda text: None, agent2)
    weird.toggle_talk("<script>")  # not a valid label: the agent just gets no target
    weird.toggle_talk(None)
    weird.wait()
    assert agent2.seen[0][1].locked_target is None


def test_morph_state_accepts_object_labels() -> None:
    assert MorphState(locked_target="Cell  Phone").locked_target == "cell phone"
    assert MorphState(locked_target="blue").locked_target == "blue"
    for bad in ("", "rm -rf /;", "x" * 41):
        with pytest.raises(ValueError):
            MorphState(locked_target=bad)


def test_default_voice_uses_audio_devices_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = {"MORPH_AUDIO_INPUT": "3", "MORPH_AUDIO_OUTPUT": "USB Speaker", "ELEVENLABS_VOICE_ID": "v1"}
    monkeypatch.setattr(speech, "get_secret", lambda name: "el-test-key")
    monkeypatch.setattr(speech, "get_setting", lambda name, default="": settings.get(name, default))
    monkeypatch.setattr(speech, "_default", None)
    played: list[Any] = []
    recorded: list[Any] = []
    monkeypatch.setattr(speech.audio, "play_wav", lambda data, device=None: played.append(device))
    monkeypatch.setattr(speech.audio, "record", lambda wait, seconds, device=None: recorded.append(device) or b"")
    voice = speech.default_voice()
    assert (voice.input_device, voice.output_device) == (3, "USB Speaker")
    voice.player(b"wav")
    voice.recorder(lambda s: None, 1.0)
    assert played == ["USB Speaker"] and recorded == [3]
    monkeypatch.setattr(speech, "_default", None)


# --- viewer: announce on lock, V key -----------------------------------------------------------------


class FakeCv2:
    COLOR_BGR2RGB = FONT_HERSHEY_SIMPLEX = LINE_AA = WND_PROP_VISIBLE = INTER_AREA = 0

    def __init__(self, keys: list[int]) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.keys = keys

    def VideoCapture(self, index: int) -> Any:  # noqa: N802
        return SimpleNamespace(isOpened=lambda: True, read=lambda: (True, SimpleNamespace(shape=(480, 640, 3))), release=lambda: None)

    def resize(self, frame: Any, size: tuple[int, int], interpolation: int) -> Any:
        return SimpleNamespace(shape=(size[1], size[0], 3))

    def cvtColor(self, frame: Any, code: int) -> Any:  # noqa: N802
        return frame

    def flip(self, frame: Any, code: int) -> Any:
        return frame

    def getTextSize(self, *args: Any) -> tuple[tuple[int, int], int]:  # noqa: N802
        return (100, 12), 4

    def waitKey(self, delay: int) -> int:  # noqa: N802
        return self.keys.pop(0) if self.keys else ord("q")

    def getWindowProperty(self, *args: Any) -> float:  # noqa: N802
        return 1.0

    def destroyAllWindows(self) -> None:  # noqa: N802
        pass

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: self.calls.append((name, args))


class SpyVoice:
    def __init__(self) -> None:
        self.announced: list[str] = []
        self.talks: list[Any] = []
        self.status = ""
        self.closed = False

    def announce_lock(self, label: str) -> bool:
        self.announced.append(label)
        return True

    def toggle_talk(self, target: Any) -> str:
        self.talks.append(target)
        return "listening"

    def close(self) -> None:
        self.closed = True


def test_viewer_announces_each_lock_once_and_v_talks_about_the_locked_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hand = [(0.5, 0.9)] * 21
    hand[8] = (0.5, 0.8)  # pointing straight up at the cup

    class Detector:
        def detect_for_video(self, image: Any, ts: int) -> Any:
            det = SimpleNamespace(bounding_box=SimpleNamespace(origin_x=128, origin_y=96, width=64, height=48),
                                  categories=[SimpleNamespace(category_name="cup", display_name="", score=0.9)])
            return SimpleNamespace(detections=[det])

        def close(self) -> None:
            pass

    class Landmarker:
        def detect_for_video(self, image: Any, ts: int) -> Any:
            return SimpleNamespace(hand_landmarks=[[SimpleNamespace(x=x, y=y) for x, y in hand]])

        def close(self) -> None:
            pass

    monkeypatch.setattr(camera_debug, "create_landmarker", lambda v, p: Landmarker())
    monkeypatch.setattr(camera_debug, "create_object_detector", lambda v, p: Detector())
    for name in ("hand.task", "objects.tflite"):
        (tmp_path / name).write_bytes(b"fake")
    keys = [-1] * 5 + [ord("v")] + [-1] * 9 + [ord("V")] + [-1] * 4 + [ord("q")]  # V before and after the lock
    cv2 = FakeCv2(keys)
    vision = camera_debug.Vision(cv2=cv2, mp=SimpleNamespace(Image=lambda **kw: kw, ImageFormat=SimpleNamespace(SRGB=1)),
                                 base_options=None, vision=None)
    spy = SpyVoice()
    code = camera_debug.run(0, tmp_path / "hand.task", vision, mode="objects",
                            object_model=tmp_path / "objects.tflite", voice=spy)
    assert code == camera_debug.EXIT_OK
    assert spy.announced == ["cup"]          # one lock -> one announcement, not one per frame
    assert spy.talks == [None, "cup"]        # V before the lock: no target; after the lock: the cup
    assert spy.closed
    texts = [args[1] for name, args in cv2.calls if name == "putText"]
    assert "V = talk" in texts


def test_speak_requires_objects_mode() -> None:
    with pytest.raises(SystemExit) as info:
        camera_debug.main(["--speak"])
    assert info.value.code == 2
