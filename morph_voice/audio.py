"""Speaker and microphone I/O through sounddevice (PortAudio), imported lazily.

WAV handling is standard library only. Playback falls back to resampling when
the output device rejects the file's sample rate (common with USB devices on
the Pi); recording uses the input device's own default rate.
"""

from __future__ import annotations

import array
import io
import threading
import wave
from collections.abc import Callable
from typing import Any

MISSING_AUDIO_MESSAGE = (
    "Audio is unavailable. Install the voice packages with: pip install -r requirements-voice.txt"
    " (on the Pi, first: sudo apt install libportaudio2)"
)
MIN_RECORDING_S = 0.3


class AudioUnavailable(RuntimeError):
    """sounddevice/PortAudio is missing or no suitable audio device exists."""


def load_sounddevice() -> Any:
    try:
        import sounddevice
    except (ImportError, OSError):  # OSError: the PortAudio library itself is missing
        raise AudioUnavailable(MISSING_AUDIO_MESSAGE) from None
    return sounddevice


# --- WAV helpers (pure) ---------------------------------------------------------------------


def pcm_to_wav(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buffer.getvalue()


def read_wav(data: bytes) -> tuple[bytes, int, int]:
    """(16-bit PCM frames, sample rate, channels)."""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit WAV audio is supported")
        return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()


def resample_pcm16(pcm: bytes, from_rate: int, to_rate: int, channels: int = 1) -> bytes:
    """Linear-interpolation resampling of interleaved 16-bit PCM (short clips only)."""
    if from_rate == to_rate or not pcm:
        return pcm
    src = array.array("h", pcm)
    frames = len(src) // channels
    out_frames = max(1, round(frames * to_rate / from_rate))
    out = array.array("h", bytes(out_frames * channels * 2))
    step = (frames - 1) / (out_frames - 1) if out_frames > 1 else 0.0
    for i in range(out_frames):
        pos = i * step
        j = int(pos)
        frac = pos - j
        k = min(j + 1, frames - 1)
        for c in range(channels):
            a, b = src[j * channels + c], src[k * channels + c]
            out[i * channels + c] = int(round(a + (b - a) * frac))
    return out.tobytes()


# --- playback / recording ---------------------------------------------------------------------


def play_wav(data: bytes, device: int | str | None = None, sd: Any = None) -> None:
    """Play a 16-bit WAV file and wait until it has finished."""
    sd = sd or load_sounddevice()
    pcm, rate, channels = read_wav(data)
    try:
        _play(sd, pcm, rate, channels, device)
    except sd.PortAudioError:
        # The device rejected this sample rate: resample to its default rate and retry.
        target = int(sd.query_devices(device, "output")["default_samplerate"])
        _play(sd, resample_pcm16(pcm, rate, target, channels), target, channels, device)


def _play(sd: Any, pcm: bytes, rate: int, channels: int, device: int | str | None) -> None:
    with sd.RawOutputStream(samplerate=rate, channels=channels, dtype="int16", device=device) as stream:
        stream.write(pcm)


def record(
    wait_for_stop: Callable[[float], None],
    max_seconds: float = 15.0,
    device: int | str | None = None,
    sd: Any = None,
) -> bytes:
    """Record mono 16-bit audio until wait_for_stop(max_seconds) returns; returns a WAV file."""
    sd = sd or load_sounddevice()
    rate = int(sd.query_devices(device, "input")["default_samplerate"])
    chunks: list[bytes] = []
    lock = threading.Lock()

    def callback(indata: Any, frames: int, time: Any, status: Any) -> None:
        with lock:
            chunks.append(bytes(indata))

    with sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", device=device, callback=callback):
        wait_for_stop(max_seconds)
    with lock:
        pcm = b"".join(chunks)
    if len(pcm) < MIN_RECORDING_S * rate * 2:
        raise ValueError("recording too short: hold the talk button while speaking")
    return pcm_to_wav(pcm, rate)


def list_devices(sd: Any = None) -> str:
    sd = sd or load_sounddevice()
    return str(sd.query_devices())
