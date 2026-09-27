"""--speak for the camera viewer: spoken lock announcements and V push-to-talk, off the video thread.

- announce_lock(label): says "That's a cup." / "Locked on that." in a background thread.
  Skipped when MORPH is already speaking, listening or thinking (never queued).
- toggle_talk(locked_target): first V starts recording from MORPH_AUDIO_INPUT, second V
  stops it; then transcribe -> agent (with locked_target in its state) -> dry-run/execute ->
  speak the reply, all in a background thread. V while busy is ignored.

At most one background job runs at a time, so the video loop never waits on audio or network.
Everything is injected, so tests need no microphone, speaker, network or keys.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

REGION_NAME = "OBJECT"


def lock_phrase(label: str) -> str:
    """"That's a cup." / "That's an apple." / "Locked on that." for the unrecognized region."""
    name = " ".join(label.split()).lower()
    if not name or name.upper() == REGION_NAME:
        return "Locked on that."
    article = "an" if name[0] in "aeiou" else "a"
    return f"That's {article} {name}."


class VoiceLink:
    def __init__(
        self,
        say: Callable[[str], None],
        transcribe: Callable[[bytes], str],
        recorder: Any,        # .start(), .stop() -> WAV bytes, .recording
        agent: Any,           # .decide(transcript, MorphState) -> AgentAction
        executor: Any,        # .execute(AgentAction) (dry run by default)
        out: Callable[[str], None] = print,
    ) -> None:
        self._say = say
        self._transcribe = transcribe
        self._recorder = recorder
        self._agent = agent
        self._executor = executor
        self._out = out
        self._lock = threading.Lock()
        self._job: threading.Thread | None = None
        self._job_status = ""
        self._pending_target: str | None = None

    # --- status (for the HUD) ----------------------------------------------------------------

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._recorder.recording or (self._job is not None and self._job.is_alive())

    @property
    def status(self) -> str:
        if self._recorder.recording:
            return "LISTENING - press V to stop"
        with self._lock:
            return self._job_status if self._job is not None and self._job.is_alive() else ""

    def wait(self, timeout: float = 30.0) -> None:
        """Wait for the current background job (tests, shutdown)."""
        job = self._job
        if job is not None:
            job.join(timeout)

    # --- jobs -----------------------------------------------------------------------------------

    def _start_job(self, status: str, work: Callable[[], None]) -> bool:
        with self._lock:
            if self._recorder.recording or (self._job is not None and self._job.is_alive()):
                return False
            self._job_status = status
            self._job = threading.Thread(target=self._run, args=(work,), daemon=True)
            self._job.start()
            return True

    def _run(self, work: Callable[[], None]) -> None:
        try:
            work()
        except Exception as exc:  # audio/network problems must never kill the viewer
            self._out(f"voice: {type(exc).__name__}: {exc}")

    def announce_lock(self, label: str) -> bool:
        """Speak a new lock in the background; False (skipped) if MORPH is busy."""
        return self._start_job("SPEAKING", lambda: self._say(lock_phrase(label)))

    def toggle_talk(self, locked_target: str | None) -> str:
        """V key: start listening, or stop and answer. Returns what happened."""
        if self._recorder.recording:
            try:
                wav = self._recorder.stop()
            except Exception as exc:  # e.g. too short: say nothing, just report it
                self._out(f"voice: {exc}")
                return "too short"
            target = self._pending_target
            self._start_job("THINKING", lambda: self._answer(wav, target))
            return "answering"
        if self.busy:
            return "busy"
        self._pending_target = locked_target
        try:
            self._recorder.start()
        except Exception as exc:  # no microphone / PortAudio missing: the video keeps running
            self._out(f"voice: cannot record: {exc}")
            return "no microphone"
        return "listening"

    def close(self) -> None:
        """Release the microphone if the viewer quits while listening."""
        if self._recorder.recording:
            try:
                self._recorder.stop()
            except Exception:
                pass

    def _answer(self, wav: bytes, locked_target: str | None) -> None:
        from morph_agent.actions import MorphState, apply

        transcript = self._transcribe(wav)
        self._out(f"You said: {transcript!r}")
        try:
            state = MorphState(locked_target=locked_target)
        except ValueError:
            state = MorphState()
        action = self._agent.decide(transcript, state)
        self._out(f"MORPH action: {action.action} | reply: {action.reply!r}")
        self._executor.execute(action)
        with self._lock:
            self._job_status = "SPEAKING"
        self._say(action.reply)
        apply(action, state)


def from_env(out: Callable[[str], None] = print) -> VoiceLink:
    """The real VoiceLink: ElevenLabs voice and devices from .env, Gemini agent, dry-run executor."""
    from morph_agent.agent import Agent
    from morph_agent.executor import DryRunExecutor
    from morph_voice.audio import StreamRecorder
    from morph_voice.speech import default_voice

    voice = default_voice()  # reads ELEVENLABS_API_KEY, MORPH_AUDIO_INPUT, MORPH_AUDIO_OUTPUT
    return VoiceLink(
        say=voice.say,
        transcribe=voice.transcribe,
        recorder=StreamRecorder(device=voice.input_device),
        agent=Agent.from_env(),
        executor=DryRunExecutor(out),
        out=out,
    )
