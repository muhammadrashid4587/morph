"""Minimal ElevenLabs REST client (standard library only).

- Text-to-speech: POST /v1/text-to-speech/{voice_id}, returns a WAV file (output_format wav_24000).
- Speech-to-text: POST /v1/speech-to-text (multipart: file + model_id), returns {"text": ...}.
- Voices:         GET  /v2/voices, to pick a premade voice when none is configured.

The API key is sent only in the `xi-api-key` header and never appears in errors or reprs.
The HTTP transport is injectable so tests run without network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable

API_URL = "https://api.elevenlabs.io"
TTS_MODEL = "eleven_flash_v2_5"  # lowest-latency ElevenLabs TTS model
TTS_FORMAT = "wav_24000"         # WAV, 24 kHz (44.1 kHz formats need a Pro plan)
STT_MODEL = "scribe_v2"
MAX_TTS_CHARS = 400              # MORPH speaks short sentences; also protects API credits

# (request, timeout_s) -> (HTTP status, response body)
Transport = Callable[[urllib.request.Request, float], tuple[int, bytes]]


class ElevenLabsError(RuntimeError):
    """An ElevenLabs request failed. The message never contains the API key."""


def urllib_transport(request: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise ElevenLabsError(f"cannot reach ElevenLabs: {exc.reason}") from None


def is_wav(data: bytes) -> bool:
    return len(data) > 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


class ElevenLabs:
    def __init__(self, api_key: str, transport: Transport = urllib_transport, timeout: float = 30.0) -> None:
        if not api_key:
            raise ElevenLabsError("an ElevenLabs API key is required")
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def __repr__(self) -> str:
        return "ElevenLabs(api_key=<hidden>)"

    # --- requests ---------------------------------------------------------------------

    def _request(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None) -> bytes:
        headers = {"xi-api-key": self._api_key, "Accept": "*/*"}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(API_URL + path, data=body, method=method, headers=headers)
        status, data = self._transport(request, self._timeout)
        if not 200 <= status < 300:
            raise ElevenLabsError(f"ElevenLabs {method} {path.split('?')[0]} failed: HTTP {status}: {_error_detail(data)}")
        return data

    def tts(self, text: str, voice_id: str, model_id: str = TTS_MODEL, output_format: str = TTS_FORMAT) -> bytes:
        """Speak `text`; returns the WAV file bytes."""
        text = " ".join(text.split())
        if not text:
            raise ValueError("nothing to say")
        if len(text) > MAX_TTS_CHARS:
            raise ValueError(f"text too long for MORPH to say ({len(text)} > {MAX_TTS_CHARS} characters)")
        path = f"/v1/text-to-speech/{urllib.parse.quote(voice_id, safe='')}?output_format={output_format}"
        body = json.dumps({"text": text, "model_id": model_id}).encode()
        audio = self._request("POST", path, body, "application/json")
        if not is_wav(audio):
            raise ElevenLabsError("ElevenLabs returned audio that is not a WAV file")
        return audio

    def stt(self, wav: bytes, model_id: str = STT_MODEL, language_code: str = "en") -> str:
        """Transcribe a WAV recording; returns the text ('' when nothing was said)."""
        boundary = f"morph-{uuid.uuid4().hex}"
        fields = {"model_id": model_id}
        if language_code:
            fields["language_code"] = language_code
        body = _multipart(boundary, fields, "file", "speech.wav", "audio/wav", wav)
        data = self._request("POST", "/v1/speech-to-text", body, f"multipart/form-data; boundary={boundary}")
        try:
            text = json.loads(data)["text"]
        except (ValueError, KeyError, TypeError):
            raise ElevenLabsError("ElevenLabs speech-to-text returned an unexpected response") from None
        return " ".join(str(text).split())

    def default_voice(self) -> tuple[str, str]:
        """(voice_id, name) of the first premade voice on the account."""
        data = self._request("GET", "/v2/voices?category=premade&page_size=10")
        try:
            voices = json.loads(data)["voices"]
            return voices[0]["voice_id"], voices[0].get("name", "?")
        except (ValueError, KeyError, IndexError, TypeError):
            raise ElevenLabsError("no premade ElevenLabs voice found; set ELEVENLABS_VOICE_ID in .env") from None


def _multipart(boundary: str, fields: dict[str, str], file_field: str, filename: str, file_type: str, data: bytes) -> bytes:
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {file_type}\r\n\r\n".encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


def _error_detail(data: bytes) -> str:
    try:
        detail = json.loads(data).get("detail")
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("status")
        return str(detail)[:200] if detail else data[:200].decode("utf-8", "replace")
    except (ValueError, AttributeError):
        return data[:200].decode("utf-8", "replace")
