"""Voice / transcription tests."""
from __future__ import annotations

import io
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch


MOCK_TRANSCRIPTION = {
    "text": "Hello, create a new feature for user authentication.",
    "language": "en",
    "language_probability": 0.99,
    "duration": 4.5,
}


@pytest.mark.asyncio
async def test_voice_status_unauthenticated(client: AsyncClient):
    r = await client.get("/api/voice/status")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_voice_status_authenticated(auth_client: AsyncClient):
    r = await auth_client.get("/api/voice/status")
    assert r.status_code == 200
    assert "model_ready" in r.json()


@pytest.mark.asyncio
@patch("app.routers.voice.transcribe_audio", new_callable=AsyncMock)
async def test_transcribe_audio(mock_transcribe, auth_client: AsyncClient):
    mock_transcribe.return_value = MOCK_TRANSCRIPTION

    # Create a minimal fake audio file
    audio_data = b"\x00" * 1024  # fake audio bytes
    files = {"audio": ("test.webm", io.BytesIO(audio_data), "audio/webm")}

    r = await auth_client.post("/api/voice/transcribe", files=files)
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == MOCK_TRANSCRIPTION["text"]
    assert data["language"] == "en"


@pytest.mark.asyncio
@patch("app.routers.voice.transcribe_audio", new_callable=AsyncMock)
async def test_transcribe_with_language(mock_transcribe, auth_client: AsyncClient):
    mock_transcribe.return_value = {**MOCK_TRANSCRIPTION, "language": "fr"}
    audio_data = b"\x00" * 512
    files = {"audio": ("test.webm", io.BytesIO(audio_data), "audio/webm")}
    r = await auth_client.post("/api/voice/transcribe?language=fr", files=files)
    assert r.status_code == 200
    mock_transcribe.assert_called_once()
    call_kwargs = mock_transcribe.call_args
    assert call_kwargs.kwargs.get("language") == "fr" or call_kwargs.args[-1] == "fr"


@pytest.mark.asyncio
async def test_transcribe_file_too_large(auth_client: AsyncClient):
    # 26 MB of zeros
    audio_data = b"\x00" * (26 * 1024 * 1024)
    files = {"audio": ("big.webm", io.BytesIO(audio_data), "audio/webm")}
    r = await auth_client.post("/api/voice/transcribe", files=files)
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_transcribe_unauthenticated(client: AsyncClient):
    audio_data = b"\x00" * 512
    files = {"audio": ("test.webm", io.BytesIO(audio_data), "audio/webm")}
    r = await client.post("/api/voice/transcribe", files=files)
    assert r.status_code == 401


@pytest.mark.asyncio
@patch("app.services.voice._whisper_model")
def test_is_model_ready_when_loaded(mock_model):
    from app.services.voice import is_model_ready
    import app.services.voice as voice_module
    voice_module._whisper_model = mock_model
    assert is_model_ready() is True
    voice_module._whisper_model = None


def test_is_model_ready_when_not_loaded():
    from app.services.voice import is_model_ready
    import app.services.voice as voice_module
    voice_module._whisper_model = None
    assert is_model_ready() is False
