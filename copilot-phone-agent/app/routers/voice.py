"""Voice / STT router – accepts audio file, returns transcription."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.auth import current_active_user
from app.models.user import User
from app.services.voice import is_model_ready, transcribe_audio

router = APIRouter(prefix="/api/voice", tags=["voice"])
limiter = Limiter(key_func=get_remote_address)

MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25 MB


@router.get("/status")
async def voice_status(_: User = Depends(current_active_user)) -> dict:
    return {"model_ready": is_model_ready()}


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(..., description="Audio file (webm/ogg/wav/mp3)"),
    language: str | None = Query(default=None, description="Force language (e.g. 'en')"),
    _: User = Depends(current_active_user),
) -> dict:
    # Validate content type
    allowed = {
        "audio/webm",
        "audio/ogg",
        "audio/wav",
        "audio/mpeg",
        "audio/mp4",
        "audio/x-wav",
        "application/octet-stream",  # generic browser uploads
    }
    content_type = (audio.content_type or "").split(";")[0].strip()
    if content_type and content_type not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported audio type: {content_type}",
        )

    data = await audio.read(MAX_UPLOAD_SIZE + 1)
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Audio file too large (max 25 MB).")

    try:
        result = await transcribe_audio(data, language=language)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}")

    return result
