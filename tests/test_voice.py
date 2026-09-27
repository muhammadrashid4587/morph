"""morph_voice without network, microphone, speaker, API keys or audio packages."""

import array
import json
import math
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from morph_voice import audio
from morph_voice.elevenlabs import ElevenLabs, ElevenLabsError
from morph_voice.env import MissingKeyError, get_secret, get_setting, read_env_file
from morph_voice.speech import PHRASES, PhraseCache, Voice

KEY = "el-secret-KEY-123456"


def wav(seconds: float = 0.5, rate: int = 24000) -> bytes:
    samples = array.array("h", (int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(int(seconds * rate))))
    return audio.pcm_to_wav(samples.tobytes(), rate)


class FakeTransport:
    """Records requests; answers with queued (status, body) responses."""

    def __init__(self, *responses: tuple[int, bytes]) -> None:
        self.responses = list(responses)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
        self.requests.append(request)
        return self.responses.pop(0)


# --- .env -----------------------------------------------------------------------------------


def test_env_file_parsing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('# comment\nexport A=1\nB = "two words"\nC=\'x\'\nD=val # note\nE=\n\nnot a line\n')
    assert read_env_file(env) == {"A": "1", "B": "two words", "C": "x", "D": "val", "E": ""}
    assert read_env_file(tmp_path / "missing.env") == {}


def test_secrets_come_only_from_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(f"ELEVENLABS_API_KEY={KEY}\nEMPTY=\n")
    monkeypatch.setenv("OTHER_KEY", "from-environment")
    assert get_secret("ELEVENLABS_API_KEY", env) == KEY
    with pytest.raises(MissingKeyError) as info:
        get_secret("OTHER_KEY", env)  # the process environment is ignored on purpose
    assert "OTHER_KEY is missing" in str(info.value) and "from-environment" not in str(info.value)
    with pytest.raises(MissingKeyError):
        get_secret("EMPTY", env)
    assert get_setting("ELEVENLABS_VOICE_ID", "fallback", env) == "fallback"


# --- ElevenLabs client --------------------------------------------------------------------------


def test_tts_request_shape_and_key_only_in_header() -> None:
    transport = FakeTransport((200, wav()))
    client = ElevenLabs(KEY, transport)
    audio_bytes = client.tts("  Locked   on blue. ", "voice/1")
    req = transport.requests[0]
    assert req.get_method() == "POST"
    assert req.full_url == "https://api.elevenlabs.io/v1/text-to-speech/voice%2F1?output_format=wav_24000"
    assert req.get_header("Xi-api-key") == KEY
    assert json.loads(req.data) == {"text": "Locked on blue.", "model_id": "eleven_flash_v2_5"}
    assert KEY not in req.full_url and KEY.encode() not in req.data
    assert audio_bytes[:4] == b"RIFF"
    assert KEY not in repr(client)


def test_tts_rejects_empty_long_and_non_wav() -> None:
    client = ElevenLabs(KEY, FakeTransport((200, b"ID3 not a wav at all" * 5)))
    with pytest.raises(ValueError):
        client.tts("   ", "v")
    with pytest.raises(ValueError, match="too long"):
        client.tts("x" * 401, "v")
    with pytest.raises(ElevenLabsError, match="not a WAV"):
        client.tts("hi", "v")


def test_http_errors_never_contain_the_key() -> None:
    body = json.dumps({"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}).encode()
    client = ElevenLabs(KEY, FakeTransport((401, body)))
    with pytest.raises(ElevenLabsError) as info:
        client.tts("hi", "v")
    assert "HTTP 401" in str(info.value) and "Invalid API key" in str(info.value)
    assert KEY not in str(info.value)


def test_stt_multipart_request_and_transcript() -> None:
    transport = FakeTransport((200, json.dumps({"text": "  next   slide please ", "language_code": "en"}).encode()))
    recording = wav()
    text = ElevenLabs(KEY, transport).stt(recording)
    assert text == "next slide please"
    req = transport.requests[0]
    assert req.full_url == "https://api.elevenlabs.io/v1/speech-to-text"
    content_type = req.get_header("Content-type")
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.split("boundary=")[1].encode()
    body = req.data
    assert body.count(b"--" + boundary) == 4  # model_id, language_code, file, closing
    assert b'name="model_id"\r\n\r\nscribe_v2\r\n' in body
    assert b'name="language_code"\r\n\r\nen\r\n' in body
    assert b'name="file"; filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n' + recording in body


def test_stt_unexpected_response() -> None:
    with pytest.raises(ElevenLabsError, match="unexpected"):
        ElevenLabs(KEY, FakeTransport((200, b"<html>"))).stt(wav())


def test_default_voice_picks_first_premade() -> None:
    transport = FakeTransport((200, json.dumps({"voices": [{"voice_id": "abc", "name": "Nova"}]}).encode()))
    assert ElevenLabs(KEY, transport).default_voice() == ("abc", "Nova")
    assert transport.requests[0].full_url.endswith("/v2/voices?category=premade&page_size=10")
    with pytest.raises(ElevenLabsError, match="ELEVENLABS_VOICE_ID"):
        ElevenLabs(KEY, FakeTransport((200, b'{"voices": []}'))).default_voice()


# --- cache, say, listen --------------------------------------------------------------------------------


def make_voice(tmp_path: Path, transport: FakeTransport, played: list, recorded: bytes = b"") -> Voice:
    return Voice(
        ElevenLabs(KEY, transport),
        "voice1",
        PhraseCache(tmp_path / "cache"),
        player=played.append,
        recorder=lambda wait, seconds: (wait(seconds), recorded)[1],
    )


def test_say_caches_and_second_call_needs_no_network(tmp_path: Path) -> None:
    transport = FakeTransport((200, wav()))
    played: list[bytes] = []
    voice = make_voice(tmp_path, transport, played)
    voice.say("Locked on blue.")
    voice.say("Locked   on blue.")  # same text after whitespace normalization
    assert len(transport.requests) == 1  # second time came from disk
    assert played[0] == played[1]
    files = list((tmp_path / "cache").glob("*.wav"))
    assert len(files) == 1 and files[0].name.startswith("locked-on-blue-")


def test_cache_key_depends_on_voice_and_model(tmp_path: Path) -> None:
    cache = PhraseCache(tmp_path)
    a = cache.path_for("Hi.", "v1", "m1", "wav_24000")
    assert a == cache.path_for("Hi.", "v1", "m1", "wav_24000")
    assert a != cache.path_for("Hi.", "v2", "m1", "wav_24000")
    assert a != cache.path_for("Hi.", "v1", "m2", "wav_24000")
    assert a != cache.path_for("hi.", "v1", "m1", "wav_24000")


def test_listen_records_between_trigger_events_then_transcribes(tmp_path: Path) -> None:
    events: list[str] = []

    class Trigger:
        def wait_start(self) -> None:
            events.append("start")

        def wait_stop(self, timeout: float) -> None:
            events.append(f"stop<={timeout}")

    transport = FakeTransport((200, b'{"text": "music mode"}'))
    recording = wav()
    voice = make_voice(tmp_path, transport, [], recorded=recording)
    assert voice.listen(Trigger()) == "music mode"
    assert events == ["start", "stop<=15.0"]
    assert recording in transport.requests[0].data


def test_fixed_phrases_are_short_and_unique() -> None:
    assert len(set(PHRASES)) == len(PHRASES)
    assert all(0 < len(p) <= 40 for p in PHRASES)
    assert {"Locked on blue.", "Presentation mode."} <= set(PHRASES)


# --- audio helpers (no sound hardware) --------------------------------------------------------------------


def test_wav_round_trip_and_resample() -> None:
    data = wav(0.1, 24000)
    pcm, rate, channels = audio.read_wav(data)
    assert (rate, channels, len(pcm)) == (24000, 1, 2400 * 2)
    resampled = audio.resample_pcm16(pcm, 24000, 48000)
    assert abs(len(resampled) // 2 - 4800) <= 1
    assert audio.resample_pcm16(pcm, 24000, 24000) == pcm


class FakeSoundDevice:
    class PortAudioError(Exception):
        pass

    def __init__(self, reject_rate: int | None = None) -> None:
        self.reject_rate = reject_rate
        self.written: list[tuple[int, int]] = []

    def query_devices(self, device: Any = None, kind: str | None = None) -> Any:
        return {"default_samplerate": 48000.0} if kind else "fake devices"

    def RawOutputStream(self, samplerate: int, channels: int, dtype: str, device: Any) -> Any:  # noqa: N802
        if samplerate == self.reject_rate:
            raise self.PortAudioError("Invalid sample rate")
        outer = self

        class Stream:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *exc: Any) -> None:
                pass

            def write(self, data: bytes) -> None:
                outer.written.append((samplerate, len(data)))

        return Stream()

    def RawInputStream(self, samplerate: int, channels: int, dtype: str, device: Any, callback: Any) -> Any:  # noqa: N802
        class Stream:
            def __enter__(self) -> Any:
                for _ in range(10):  # 10 blocks of 0.1 s
                    callback(bytes(int(samplerate * 0.1) * 2), 0, None, None)
                return self

            def __exit__(self, *exc: Any) -> None:
                pass

        return Stream()


def test_play_falls_back_to_the_device_rate() -> None:
    sd = FakeSoundDevice(reject_rate=24000)
    audio.play_wav(wav(0.1, 24000), sd=sd)
    assert sd.written == [(48000, 4800 * 2)]
    ok = FakeSoundDevice()
    audio.play_wav(wav(0.1, 24000), sd=ok)
    assert ok.written == [(24000, 2400 * 2)]


def test_record_returns_wav_at_the_device_rate() -> None:
    waited: list[float] = []
    data = audio.record(waited.append, max_seconds=7, sd=FakeSoundDevice())
    pcm, rate, channels = audio.read_wav(data)
    assert (rate, channels, len(pcm)) == (48000, 1, 48000 * 2)
    assert waited == [7]


def test_record_rejects_too_short() -> None:
    class Short(FakeSoundDevice):
        def RawInputStream(self, samplerate: int, channels: int, dtype: str, device: Any, callback: Any) -> Any:  # noqa: N802
            callback(bytes(100), 0, None, None)
            return super().RawInputStream(samplerate, channels, dtype, device, lambda *a: None)

    with pytest.raises(ValueError, match="too short"):
        audio.record(lambda s: None, sd=Short())


def test_missing_sounddevice_gives_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(audio.AudioUnavailable, match="requirements-voice.txt"):
        audio.load_sounddevice()


def test_packages_import_without_audio_or_sdk() -> None:
    code = (
        "import sys, morph_voice, morph_voice.__main__, morph_agent.agent, morph_agent.demo, morph_agent.executor;"
        "bad = {'sounddevice', 'anthropic', 'websockets.sync.client'} & set(sys.modules); assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=Path(__file__).resolve().parent.parent)
