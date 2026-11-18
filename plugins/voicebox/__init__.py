"""Voicebox TTS plugin.

Speech is synthesized by a running Voicebox server (default
http://localhost:17493). Profiles are selected by id through the voice key.
This plugin does not import profiles into Abogen settings.
"""

from __future__ import annotations

from pathlib import Path

from abogen.tts_plugin.engine import Engine
from abogen.tts_plugin.errors import EngineError
from abogen.tts_plugin.host_context import HostContext
from abogen.tts_plugin.manifest import (
    AudioFormatManifest,
    EngineManifest,
    ModelManifest,
    ParameterManifest,
    PluginManifest,
    RequirementManifest,
    VoiceSourceManifest,
)
from abogen.tts_plugin.types import EngineConfig

from .engine import DEFAULT_MODEL_SIZE, build_engine

PLUGIN_MANIFEST = PluginManifest(
    id="voicebox",
    name="Voicebox",
    version="0.1.0",
    api_version="1.0",
    description="Local Voicebox HTTP engine for cloned voices",
    author="Abogen",
    capabilities=(),
    requires=RequirementManifest(internet=False),
    engine=EngineManifest(
        voiceSources=(
            VoiceSourceManifest(
                id="voicebox",
                name="Voicebox profiles",
                type="speaker_id",
                config={"url_env": "ABOGEN_VOICEBOX_URL"},
            ),
        ),
        parameters=(
            ParameterManifest(
                id="model_size",
                name="Model size",
                description="Voicebox model size sent with each request",
                type="string",
                default=DEFAULT_MODEL_SIZE,
            ),
        ),
        audioFormats=(AudioFormatManifest(mime="audio/wav", extension="wav"),),
    ),
    voices=(),
)

MODEL_REQUIREMENTS: list[ModelManifest] = []


def create_engine(
    context: HostContext,
    model_path: Path | None,
    config: EngineConfig,
) -> Engine:
    """Create a Voicebox engine. Does not contact the server."""
    del model_path
    try:
        return build_engine(
            http_client=context.http_client,
            language=config.language,
        )
    except EngineError:
        raise
    except Exception as exc:
        raise EngineError(f"Failed to create Voicebox engine: {exc}") from exc
