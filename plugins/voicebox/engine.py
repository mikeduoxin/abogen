"""Voicebox engine adapter for the TTS plugin architecture.

Synthesis is delegated to a Voicebox server over HTTP. The server is a local
voice-cloning studio; this module does not load a model itself.

Audio bytes on ``SynthesizedAudio.data`` are little-endian float32 PCM at
24 kHz, matching the Kokoro engine. The declared format remains ``audio/wav``
so hosts can treat plugin output the same way.
"""

from __future__ import annotations

import io
import logging
import os
import re
import wave

import numpy as np

from abogen.domain.enums import Language
from abogen.tts_plugin.engine import Engine, EngineSession
from abogen.tts_plugin.errors import EngineError
from abogen.tts_plugin.host_context import HttpClient
from abogen.tts_plugin.types import (
    AudioFormat,
    AudioSegment,
    Duration,
    SynthesisRequest,
    SynthesizedAudio,
)

logger = logging.getLogger(__name__)

VOICEBOX_DEFAULT_URL = "http://localhost:17493"
VOICEBOX_URL_ENV = "ABOGEN_VOICEBOX_URL"
DEFAULT_MODEL_SIZE = "1.7B"
SAMPLE_RATE = 24000
_REQUEST_TIMEOUT_SECONDS = 6000
_MAX_CHUNK_LENGTH = 500
_SPLIT_PATTERN = re.compile(r"\n+")
_OUTPUT_FORMAT = AudioFormat(mime="audio/wav", extension="wav")
_MEANINGFUL_PATTERN = re.compile(r"[\u4E00-\u9FFF\u3400-\u4DBF\w]")


def voicebox_base_url() -> str:
    """Return the Voicebox server URL, without a trailing slash."""
    raw = os.environ.get(VOICEBOX_URL_ENV, VOICEBOX_DEFAULT_URL).strip()
    return (raw or VOICEBOX_DEFAULT_URL).rstrip("/")


def _is_decorative_only(text: str) -> bool:
    """Return True when a chunk has no meaningful speakable content."""
    stripped = text.strip()
    if not stripped:
        return True
    meaningful = len(_MEANINGFUL_PATTERN.findall(stripped))
    return meaningful < 3 or meaningful / len(stripped) < 0.10


def _split_text(text: str, *, max_chunk_length: int = _MAX_CHUNK_LENGTH) -> list[str]:
    stripped = (text or "").strip()
    if not stripped:
        return []
    parts = [part.strip() for part in _SPLIT_PATTERN.split(stripped) if part.strip()]
    result: list[str] = []
    for part in parts:
        if len(part) <= max_chunk_length:
            result.append(part)
            continue
        start = 0
        while start < len(part):
            end = min(len(part), start + max_chunk_length)
            if end < len(part):
                whitespace = part.rfind(" ", start, end)
                if whitespace > start + 40:
                    end = whitespace
            chunk = part[start:end].strip()
            if chunk:
                result.append(chunk)
            if end <= start:
                break
            start = end
    return result


def _resample_linear(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or audio.size == 0:
        return audio
    new_len = int(round(audio.size * dst_rate / float(src_rate)))
    if new_len <= 1:
        return np.zeros(0, dtype="float32")
    x_old = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
    return np.interp(x_new, x_old, audio).astype("float32", copy=False)


def _wav_to_float32(data: bytes) -> tuple[np.ndarray, int]:
    """Decode a PCM WAV payload into mono float32 samples."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frames = wav_file.readframes(wav_file.getnframes())
    except wave.Error as exc:
        raise EngineError(f"Voicebox response was not a PCM WAV: {exc}") from exc

    if sample_width == 2:
        audio = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
    elif sample_width == 1:
        audio = (np.frombuffer(frames, dtype=np.uint8).astype("float32") - 128.0) / 128.0
    elif sample_width == 4:
        audio = np.frombuffer(frames, dtype="<i4").astype("float32") / 2147483648.0
    else:
        raise EngineError(f"Unsupported WAV sample width: {sample_width}")

    if channels > 1:
        audio = audio.reshape(-1, channels)[:, 0]
    return np.ascontiguousarray(audio, dtype="float32"), sample_rate


def _response_bytes(response: object) -> bytes:
    if response is None:
        raise EngineError("Voicebox HTTP client returned no response")
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and status >= 400:
        raise EngineError(f"Voicebox request failed with status {status}")
    raiser = getattr(response, "raise_for_status", None)
    if callable(raiser):
        try:
            raiser()
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(f"Voicebox request failed: {exc}") from exc
    content = getattr(response, "content", None)
    if not isinstance(content, (bytes, bytearray)) or len(content) == 0:
        raise EngineError("Voicebox response did not contain audio bytes")
    return bytes(content)


class VoiceboxSession:
    """EngineSession that synthesizes one request through Voicebox HTTP."""

    def __init__(
        self,
        *,
        http_client: HttpClient,
        base_url: str,
        language: str,
        model_size: str,
    ) -> None:
        self._http = http_client
        self._base_url = base_url
        self._language = language
        self._model_size = model_size
        self._disposed = False

    def synthesize(self, request: SynthesisRequest) -> SynthesizedAudio:
        if self._disposed:
            raise EngineError("Session disposed")
        try:
            return self._synthesize(request)
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(f"Synthesis failed: {exc}") from exc

    def _synthesize(self, request: SynthesisRequest) -> SynthesizedAudio:
        profile_id = str(request.voice.key or "").strip()
        if not profile_id:
            raise EngineError("Voicebox requires a profile id as the voice key")

        model_size = str(
            request.parameters.values.get("model_size", self._model_size) or self._model_size
        )
        chunks = _split_text(request.text)
        audio_parts: list[np.ndarray] = []
        segments: list[AudioSegment] = []
        for chunk in chunks:
            if _is_decorative_only(chunk):
                logger.debug("Skipping non-speakable chunk: %s", chunk[:60])
                continue
            audio = self._generate(chunk, profile_id, model_size)
            if audio.size == 0:
                continue
            audio_parts.append(audio)
            segments.append(
                AudioSegment(
                    graphemes=chunk,
                    audio=audio.astype("<f4", copy=False).tobytes(),
                    sample_rate=SAMPLE_RATE,
                )
            )

        if not audio_parts:
            return SynthesizedAudio(
                data=b"",
                format=_OUTPUT_FORMAT,
                duration=Duration(seconds=0.0),
            )

        combined = np.concatenate(audio_parts).astype("<f4", copy=False)
        return SynthesizedAudio(
            data=combined.tobytes(),
            format=_OUTPUT_FORMAT,
            duration=Duration(seconds=len(combined) / SAMPLE_RATE),
            segments=tuple(segments),
        )

    def _generate(self, text: str, profile_id: str, model_size: str) -> np.ndarray:
        response = self._http.post(
            f"{self._base_url}/generate/stream",
            json={
                "profile_id": profile_id,
                "text": text,
                "language": self._language,
                "model_size": model_size,
            },
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        audio, sample_rate = _wav_to_float32(_response_bytes(response))
        if sample_rate != SAMPLE_RATE:
            audio = _resample_linear(audio, sample_rate, SAMPLE_RATE)
        return audio

    def dispose(self) -> None:
        self._disposed = True


class VoiceboxEngine:
    """Factory for Voicebox sessions. The engine holds no model state."""

    def __init__(
        self,
        *,
        http_client: HttpClient,
        base_url: str,
        language: Language,
        model_size: str = DEFAULT_MODEL_SIZE,
    ) -> None:
        self._http = http_client
        self._base_url = base_url
        self._language = language.value
        self._model_size = model_size
        self._disposed = False

    def createSession(self) -> EngineSession:
        if self._disposed:
            raise EngineError("Engine disposed")
        return VoiceboxSession(
            http_client=self._http,
            base_url=self._base_url,
            language=self._language,
            model_size=self._model_size,
        )

    def dispose(self) -> None:
        self._disposed = True


def build_engine(
    *,
    http_client: HttpClient,
    language: Language,
    base_url: str | None = None,
    model_size: str = DEFAULT_MODEL_SIZE,
) -> Engine:
    """Create a Voicebox engine from an HTTP client."""
    if http_client is None or not callable(getattr(http_client, "post", None)):
        raise EngineError("Voicebox engine requires an HTTP client with post()")
    return VoiceboxEngine(
        http_client=http_client,
        base_url=(base_url or voicebox_base_url()),
        language=language,
        model_size=model_size,
    )
