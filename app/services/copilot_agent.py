"""Copilot agent proxy – forwards chat messages to per-container agent API
and streams back structured chunks (SSE-friendly)."""
from __future__ import annotations

import json
from typing import AsyncGenerator, Optional

import httpx
import structlog

from app.schemas.chat import ChunkType, StreamChunk

log = structlog.get_logger(__name__)


class CopilotAgentClient:
    """HTTP client for the agent FastAPI running inside each container."""

    def __init__(self, host: str, port: int, timeout: float = 120.0) -> None:
        self._base_url = f"http://{host}:{port}"
        self._timeout = timeout

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def stream_chat(
        self,
        message: str,
        history: list[dict],
        session_id: str,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream chunks from the agent SSE endpoint."""
        payload = {
            "message": message,
            "history": history,
            "session_id": session_id,
        }
        log.info("agent_chat_request", session_id=session_id, chars=len(message))
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/stream",
                    json=payload,
                    headers={"Accept": "text/event-stream"},
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            chunk = StreamChunk.model_validate_json(raw)
                            yield chunk
                        except Exception as exc:
                            log.warning("bad_sse_chunk", raw=raw[:120], error=str(exc))
        except httpx.TimeoutException:
            yield StreamChunk(type=ChunkType.error, content="Agent request timed out.")
        except httpx.HTTPStatusError as exc:
            yield StreamChunk(type=ChunkType.error, content=f"Agent HTTP error: {exc.response.status_code}")
        except Exception as exc:
            log.error("agent_stream_error", error=str(exc))
            yield StreamChunk(type=ChunkType.error, content=f"Unexpected error: {exc}")

    async def trigger_pr(
        self, feature_name: str, session_id: str
    ) -> dict:
        """Ask the agent to commit, branch, and open a PR."""
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self._base_url}/git/create-pr",
                json={"feature_name": feature_name, "session_id": session_id},
            )
            resp.raise_for_status()
            return resp.json()


def get_agent_client(host: str, port: int) -> CopilotAgentClient:
    return CopilotAgentClient(host=host, port=port)
