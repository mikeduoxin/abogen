"""Voicebox plugin: loader checks and one mocked synthesis."""

from __future__ import annotations

import io
import logging
import wave
from pathlib import Path

import pytest

from abogen.domain.enums import Language
from abogen.tts_plugin.errors import EngineError
from abogen.tts_plugin.host_context import HostContext
from abogen.tts_plugin.loader import load_plugin_from_dir
from abogen.tts_plugin.types import (
    AudioFormat,
    EngineConfig,
    ParameterValues,
    SynthesisRequest,
    VoiceSelection,
)
from tests.contracts.engine_contract import EngineContractMixin

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "voicebox"


def _wav_bytes(sample_rate: int = 24000, count: int = 240) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x10" * count)
    return buffer.getvalue()


class FakeVoiceboxClient:
    """HTTP client that records generate calls and returns a short WAV."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.posts: list[dict] = []

    def get(self, url: str, **kwargs: object) -> object:
        del url, kwargs
        return None

    def post(self, url: str, **kwargs: object) -> object:
        self.posts.append({"url": url, "json": kwargs.get("json")})
        if self.fail:
            raise RuntimeError("voicebox unavailable")

        class Response:
            status_code = 200
            content = _wav_bytes()

            def raise_for_status(self) -> None:
                return None

        return Response()


def _context(client: FakeVoiceboxClient, tmp_path: Path) -> HostContext:
    return HostContext(
        config_dir=tmp_path,
        logger=logging.getLogger("voicebox-test"),
        http_client=client,
    )


def _engine(client: FakeVoiceboxClient, tmp_path: Path):
    result = load_plugin_from_dir(PLUGIN_DIR)
    assert result.success, result.error
    return result.create_engine(
        _context(client, tmp_path),
        None,
        EngineConfig(device="cpu", language=Language.ZH),
    )


class TestVoiceboxEngineContract(EngineContractMixin):
    @pytest.fixture
    def engine(self, tmp_path: Path):
        return _engine(FakeVoiceboxClient(), tmp_path)


def test_plugin_loads_and_declares_no_static_voices() -> None:
    result = load_plugin_from_dir(PLUGIN_DIR)
    assert result.success
    assert result.manifest is not None
    assert result.manifest.id == "voicebox"
    assert result.manifest.id == PLUGIN_DIR.name
    assert result.manifest.api_version == "1.0"
    assert result.manifest.capabilities == ()
    assert result.manifest.voices == ()
    assert result.manifest.engine.voiceSources[0].id == "voicebox"
    assert result.manifest.engine.audioFormats[0].mime == "audio/wav"


def test_one_synthesis_posts_profile_and_text(tmp_path: Path) -> None:
    client = FakeVoiceboxClient()
    engine = _engine(client, tmp_path)
    session = engine.createSession()
    audio = session.synthesize(
        SynthesisRequest(
            text="你好，世界",
            voice=VoiceSelection(source="voicebox", key="profile-7"),
            parameters=ParameterValues(values={"model_size": "0.6B"}),
            format=AudioFormat(mime="audio/wav", extension="wav"),
        )
    )
    session.dispose()
    engine.dispose()

    assert len(audio.data) > 0
    assert audio.duration.seconds > 0
    assert len(audio.segments) == 1
    assert client.posts[0]["url"].endswith("/generate/stream")
    assert client.posts[0]["json"] == {
        "profile_id": "profile-7",
        "text": "你好，世界",
        "language": "zh",
        "model_size": "0.6B",
    }


def test_paragraphs_are_separate_requests(tmp_path: Path) -> None:
    client = FakeVoiceboxClient()
    engine = _engine(client, tmp_path)
    session = engine.createSession()
    session.synthesize(
        SynthesisRequest(
            text="第一段\n\n第二段",
            voice=VoiceSelection(source="voicebox", key="profile-7"),
            parameters=ParameterValues(values={}),
            format=AudioFormat(mime="audio/wav", extension="wav"),
        )
    )
    session.dispose()
    engine.dispose()
    assert [call["json"]["text"] for call in client.posts] == ["第一段", "第二段"]


def test_http_failure_raises_and_session_stays_usable(tmp_path: Path) -> None:
    client = FakeVoiceboxClient(fail=True)
    engine = _engine(client, tmp_path)
    session = engine.createSession()
    request = SynthesisRequest(
        text="Hello",
        voice=VoiceSelection(source="voicebox", key="profile-7"),
        parameters=ParameterValues(values={}),
        format=AudioFormat(mime="audio/wav", extension="wav"),
    )
    with pytest.raises(EngineError):
        session.synthesize(request)
    client.fail = False
    audio = session.synthesize(request)
    assert len(audio.data) > 0
    session.dispose()
    engine.dispose()
