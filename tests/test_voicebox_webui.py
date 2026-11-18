"""Voicebox profile import and conversion routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from abogen.domain.enums import Language
from abogen.domain.pipeline_factory import create_pipeline_for_job
from abogen.domain.settings_core import normalize_setting_value
from abogen.domain.voice_utils import resolve_voice_target
from abogen.voice_profiles import (
    import_voicebox_profiles,
    load_profiles,
    normalize_profile_entry,
)


def test_normalize_keeps_voicebox_profile() -> None:
    entry = normalize_profile_entry(
        {
            "provider": "voicebox",
            "language": "z",
            "voicebox_profile_id": "abc",
            "voicebox_profile_name": "Narrator",
        }
    )
    assert entry["provider"] == "voicebox"
    assert entry["language"] == "zh"
    assert entry["voicebox_profile_id"] == "abc"
    assert entry["voicebox_profile_name"] == "Narrator"


def test_resolve_voice_target_uses_profile_id() -> None:
    provider, spec, speed, steps = resolve_voice_target(
        "speaker:VB: Narrator",
        {
            "VB: Narrator": {
                "provider": "voicebox",
                "language": "zh",
                "voicebox_profile_id": "abc",
            }
        },
    )
    assert (provider, spec, speed, steps) == ("voicebox", "abc", None, None)


def test_resolve_voicebox_prefix() -> None:
    provider, spec, _, _ = resolve_voice_target("voicebox:abc", {})
    assert provider == "voicebox"
    assert spec == "abc"


def test_import_voicebox_profiles_writes_speakers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "abogen.voice_profiles._get_profiles_path",
        lambda: str(tmp_path / "voice_profiles.json"),
    )
    imported = import_voicebox_profiles(
        [
            {"id": "p1", "name": "Ada", "language": "zh"},
            {"id": "p2", "name": "Bea", "language": "en"},
        ],
        base_url="http://localhost:17493",
    )
    assert imported == ["VB: Ada", "VB: Bea"]
    saved = load_profiles()
    assert saved["VB: Ada"]["voicebox_profile_id"] == "p1"
    assert saved["VB: Ada"]["language"] == "zh"
    assert saved["VB: Bea"]["language"] == "en-US"


def test_pipeline_factory_selects_voicebox_plugin() -> None:
    with patch("abogen.domain.pipeline_factory.is_plugin_registered", return_value=True), patch(
        "abogen.domain.pipeline_factory.create_pipeline", return_value="pipeline"
    ) as create:
        result = create_pipeline_for_job("voicebox", Language.ZH, use_gpu=False)
    assert result == "pipeline"
    create.assert_called_once_with("voicebox", language=Language.ZH)


def test_voicebox_url_setting_strips_slash() -> None:
    defaults = {"voicebox_url": "http://localhost:17493"}
    assert (
        normalize_setting_value("voicebox_url", " http://127.0.0.1:17493/ ", defaults)
        == "http://127.0.0.1:17493"
    )


def test_import_route_stores_remote_profiles(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("flask")
    from abogen.webui.app import create_app
    monkeypatch.setattr(
        "abogen.voice_profiles._get_profiles_path",
        lambda: str(tmp_path / "voice_profiles.json"),
    )

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, str]]:
            return [{"id": "p1", "name": "Ada", "language": "en"}]

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "OUTPUT_FOLDER": str(tmp_path / "output"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        }
    )
    with patch("requests.get", return_value=Response()):
        client = app.test_client()
        page = client.get("/voices/")
        assert page.status_code == 200
        assert b"Import Voicebox" in page.data
        assert b'value="voicebox"' in page.data
        response = client.post("/voices/voicebox/import")

    assert response.status_code == 200
    body = response.get_json()
    assert body["imported"] == ["VB: Ada"]
    assert body["profiles"]["VB: Ada"]["voicebox_profile_id"] == "p1"
    assert body["profiles"]["VB: Ada"]["language"] == "en-US"
