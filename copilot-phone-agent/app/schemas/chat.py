"""Chat / agent streaming schemas."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"


class ChunkType(str, Enum):
    thinking = "thinking"
    tool_call = "tool_call"
    tool_result = "tool_result"
    status = "status"
    text = "text"
    done = "done"
    error = "error"


class ChatMessage(BaseModel):
    role: MessageRole
    content: str


class ChatRequest(BaseModel):
    session_id: str
    message: str
    history: list[ChatMessage] = []


class StreamChunk(BaseModel):
    type: ChunkType
    content: str
    tool_name: Optional[str] = None
    metadata: Optional[dict] = None
