"""Chat router – SSE streaming from agent containers + PR trigger."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud as crud
from app.auth import current_active_user
from app.config import get_settings
from app.dependencies import get_db
from app.models.user import User
from app.schemas.chat import ChatRequest
from app.services.copilot_agent import get_agent_client

router = APIRouter(prefix="/api/chat", tags=["chat"])
settings = get_settings()


@router.post("/{session_id}/stream")
async def stream_chat(
    session_id: uuid.UUID,
    body: ChatRequest,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    session = await crud.get_session(db, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.status != "running" or not session.agent_api_port:
        raise HTTPException(status_code=409, detail="Session is not running.")

    client = get_agent_client(host="localhost", port=session.agent_api_port)

    async def event_stream():
        try:
            async for chunk in client.stream_chat(
                message=body.message,
                history=[m.model_dump() for m in body.history],
                session_id=str(session_id),
            ):
                yield f"data: {chunk.model_dump_json()}\n\n"
        except Exception as exc:
            error_chunk = json.dumps({"type": "error", "content": str(exc)})
            yield f"data: {error_chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/{session_id}/create-pr")
async def create_pr(
    session_id: uuid.UUID,
    feature_name: str,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    session = await crud.get_session(db, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.status != "running" or not session.agent_api_port:
        raise HTTPException(status_code=409, detail="Session is not running.")

    client = get_agent_client(host="localhost", port=session.agent_api_port)
    try:
        result = await client.trigger_pr(feature_name=feature_name, session_id=str(session_id))
        # Persist PR info
        from app.schemas.session import AgentSessionUpdate
        await crud.update_session(
            db,
            session,
            AgentSessionUpdate(
                last_pr_url=result.get("pr_url"),
                last_pr_title=f"feat: {feature_name}",
            ),
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
