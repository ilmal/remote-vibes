"""Voice transcription service using faster-whisper."""
from __future__ import annotations

import asyncio
import io
import tempfile
import time
from pathlib import Path
from typing import Optional

import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

_whisper_model = None
_loading = False


def _load_model():
    global _whisper_model, _loading
    if _loading:
        return
    _loading = True
    try:
        from faster_whisper import WhisperModel

        models_dir = settings.whisper_models_dir
        Path(models_dir).mkdir(parents=True, exist_ok=True)

        log.info(
            "loading_whisper_model",
            model=settings.whisper_model,
            device=settings.whisper_device,
        )
        t0 = time.monotonic()
        _whisper_model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            download_root=models_dir,
        )
        log.info("whisper_model_loaded", elapsed=f"{time.monotonic()-t0:.1f}s")
    except Exception as exc:
        log.error("whisper_load_failed", error=str(exc))
        _loading = False
        raise
    finally:
        _loading = False


async def ensure_model_loaded() -> None:
    """Load the model in a thread pool on first call."""
    global _whisper_model
    if _whisper_model is None:
        await asyncio.get_running_loop().run_in_executor(None, _load_model)


async def transcribe_audio(audio_bytes: bytes, language: Optional[str] = None) -> dict:
    """
    Transcribe raw audio bytes (webm/ogg/wav/mp3) → {text, language, duration}.
    Runs whisper in thread pool to avoid blocking the event loop.
    """
    await ensure_model_loaded()

    def _transcribe():
        # Write to a temp file so ffmpeg can read the format headers
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            segments, info = _whisper_model.transcribe(
                tmp_path,
                language=language,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300),
            )
            text = " ".join(seg.text.strip() for seg in segments)
            return {
                "text": text.strip(),
                "language": info.language,
                "language_probability": info.language_probability,
                "duration": info.duration,
            }
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    result = await asyncio.get_running_loop().run_in_executor(None, _transcribe)
    log.info("transcription_done", duration=result["duration"], chars=len(result["text"]))
    return result


def is_model_ready() -> bool:
    return _whisper_model is not None
