"""Tests: voice/STT endpoints."""
from __future__ import annotations
import io
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch


async def test_voice_status(auth_client: AsyncClient):
    r = await auth_client.get("/api/voice/status")
    assert r.status_code == 200
    assert "model_ready" in r.json()


async def test_voice_status_unauthenticated(client: AsyncClient):
    r = await client.get("/api/voice/status")
    assert r.status_code == 401


async def test_transcribe_unsupported_type(auth_client: AsyncClient):
    r = await auth_client.post(
        "/api/voice/transcribe",
        files={"audio": ("test.pdf", b"fake pdf data", "application/pdf")},
    )
    assert r.status_code == 415


async def test_transcribe_too_large(auth_client: AsyncClient):
    big_data = b"x" * (25 * 1024 * 1024 + 1)
    r = await auth_client.post(
        "/api/voice/transcribe",
        files={"audio": ("big.webm", big_data, "audio/webm")},
    )
    assert r.status_code == 413


@patch("app.routers.voice.transcribe_audio")
async def test_transcribe_success(mock_transcribe, auth_client: AsyncClient):
    mock_transcribe.return_value = {"text": "hello world", "language": "en", "segments": []}

    r = await auth_client.post(
        "/api/voice/transcribe",
        files={"audio": ("audio.webm", b"fake audio bytes", "audio/webm")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == "hello world"
    assert data["language"] == "en"


@patch("app.routers.voice.transcribe_audio")
async def test_transcribe_with_language_hint(mock_transcribe, auth_client: AsyncClient):
    mock_transcribe.return_value = {"text": "hola mundo", "language": "es", "segments": []}

    r = await auth_client.post(
        "/api/voice/transcribe?language=es",
        files={"audio": ("audio.ogg", b"fake audio", "audio/ogg")},
    )
    assert r.status_code == 200
    mock_transcribe.assert_called_once()
    _, kwargs = mock_transcribe.call_args
    assert kwargs.get("language") == "es"


@patch("app.routers.voice.transcribe_audio")
async def test_transcribe_error_returns_500(mock_transcribe, auth_client: AsyncClient):
    mock_transcribe.side_effect = RuntimeError("GPU out of memory")

    r = await auth_client.post(
        "/api/voice/transcribe",
        files={"audio": ("audio.wav", b"fake audio", "audio/wav")},
    )
    assert r.status_code == 500
    assert "Transcription failed" in r.json()["detail"]


async def test_transcribe_unauthenticated(client: AsyncClient):
    r = await client.post(
        "/api/voice/transcribe",
        files={"audio": ("audio.webm", b"data", "audio/webm")},
    )
    assert r.status_code == 401
