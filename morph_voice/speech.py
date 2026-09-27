"""say() and listen(): MORPH's voice, with a disk cache for fixed phrases.

Every spoken sentence is cached as a WAV file in voice_cache/ (git-ignored), keyed
by the text, voice, model and format, so repeated phrases play instantly with no
network. `python -m morph_voice --cache-phrases` pre-generates PHRASES for the demo.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from . import audio
from .elevenlabs import TTS_FORMAT, TTS_MODEL, ElevenLabs
from .env import REPO_ROOT, get_secret, get_setting

CACHE_DIR = REPO_ROOT / "voice_cache"

# Fixed phrases worth pre-generating so the demo never waits on the network for them.
PHRASES: tuple[str, ...] = (
    "Locked on blue.",
    "Locked on yellow.",
    "Locked on green.",
    "Targeting mode.",
    "Presentation mode.",
    "Music mode.",
    "Next slide.",
    "Previous slide.",
    "Volume up.",
    "Volume down.",
    "Cancelled.",
    "Sorry, I didn't catch that.",
    "Sorry, I can't do that.",
    "Give me a second.",
    "Locked on that.",
)


class PhraseCache:
    def __init__(self, directory: Path = CACHE_DIR) -> None:
        self.directory = directory

    def path_for(self, text: str, voice_id: str, model_id: str, output_format: str) -> Path:
        key = json.dumps([text, voice_id, model_id, output_format]).encode()
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "phrase"
        return self.directory / f"{slug}-{hashlib.sha256(key).hexdigest()[:12]}.wav"

    def get(self, path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def put(self, path: Path, data: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: a half-written file is never played


class PushToTalk(Protocol):
    """Starts and stops a recording. EnterPushToTalk today; the Nano button can implement this later."""

    def wait_start(self) -> None: ...
    def wait_stop(self, timeout: float) -> None: ...


class EnterPushToTalk:
    def wait_start(self) -> None:
        input("Press Enter, then speak... ")

    def wait_stop(self, timeout: float) -> None:
        done = threading.Event()
        reader = threading.Thread(target=lambda: (input("  recording - press Enter when done "), done.set()), daemon=True)
        reader.start()
        if not done.wait(timeout):
            print(f"\n  (stopped after {timeout:.0f} s - press Enter to continue)", flush=True)
            reader.join()  # consume that Enter here so it cannot leak into the next prompt


class Voice:
    def __init__(
        self,
        client: ElevenLabs,
        voice_id: str,
        cache: PhraseCache | None = None,
        model_id: str = TTS_MODEL,
        output_format: str = TTS_FORMAT,
        player: Callable[[bytes], None] = audio.play_wav,
        recorder: Callable[[Callable[[float], None], float], bytes] = audio.record,
        language_code: str = "en",
        max_seconds: float = 15.0,
        input_device: int | str | None = None,
        output_device: int | str | None = None,
    ) -> None:
        self.client = client
        self.voice_id = voice_id
        self.cache = cache or PhraseCache()
        self.model_id = model_id
        self.output_format = output_format
        self.player = player
        self.recorder = recorder
        self.language_code = language_code
        self.max_seconds = max_seconds
        self.input_device = input_device    # MORPH_AUDIO_INPUT
        self.output_device = output_device  # MORPH_AUDIO_OUTPUT

    def speech_audio(self, text: str) -> bytes:
        """The WAV for `text`, from the cache or freshly generated (and then cached)."""
        text = " ".join(text.split())
        path = self.cache.path_for(text, self.voice_id, self.model_id, self.output_format)
        cached = self.cache.get(path)
        if cached is not None:
            return cached
        data = self.client.tts(text, self.voice_id, self.model_id, self.output_format)
        self.cache.put(path, data)
        return data

    def say(self, text: str) -> None:
        self.player(self.speech_audio(text))

    def transcribe(self, wav: bytes) -> str:
        return self.client.stt(wav, language_code=self.language_code)

    def listen(self, trigger: PushToTalk | None = None) -> str:
        trigger = trigger or EnterPushToTalk()
        trigger.wait_start()
        wav = self.recorder(trigger.wait_stop, self.max_seconds)
        return self.client.stt(wav, language_code=self.language_code)


_default: Voice | None = None


def default_voice() -> Voice:
    """The Voice configured from .env (created on first use)."""
    global _default
    if _default is None:
        client = ElevenLabs(get_secret("ELEVENLABS_API_KEY"))
        voice_id = get_setting("ELEVENLABS_VOICE_ID")
        if not voice_id:
            voice_id, name = client.default_voice()
            print(f"Using ElevenLabs voice '{name}' ({voice_id}). Set ELEVENLABS_VOICE_ID in .env to pin it.", file=sys.stderr)
        speaker = _device(get_setting("MORPH_AUDIO_OUTPUT") or None)
        mic = _device(get_setting("MORPH_AUDIO_INPUT") or None)
        _default = Voice(
            client,
            voice_id,
            player=lambda data: audio.play_wav(data, device=speaker),
            recorder=lambda wait, seconds: audio.record(wait, seconds, device=mic),
            input_device=mic,
            output_device=speaker,
        )
    return _default


def _device(value: str | None) -> int | str | None:
    return int(value) if value and value.isdigit() else value


def say(text: str) -> None:
    """Speak `text` out loud (cached after the first time)."""
    default_voice().say(text)


def listen(trigger: PushToTalk | None = None) -> str:
    """Push-to-talk: record from the mic (Enter to start and stop by default) and return the transcript."""
    return default_voice().listen(trigger)
