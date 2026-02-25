"""Agent session schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AgentSessionCreate(BaseModel):
    repo_full_name: str
    repo_name: str
    branch: str = "main"


class AgentSessionRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    repo_full_name: str
    repo_name: str
    branch: str
    container_id: Optional[str] = None
    container_name: Optional[str] = None
    code_server_port: Optional[int] = None
    agent_api_port: Optional[int] = None
    status: str
    tunnel_url: Optional[str] = None
    tunnel_active: bool
    last_pr_url: Optional[str] = None
    last_pr_title: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    stopped_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AgentSessionUpdate(BaseModel):
    status: Optional[str] = None
    container_id: Optional[str] = None
    container_name: Optional[str] = None
    code_server_port: Optional[int] = None
    agent_api_port: Optional[int] = None
    tunnel_url: Optional[str] = None
    tunnel_active: Optional[bool] = None
    last_pr_url: Optional[str] = None
    last_pr_title: Optional[str] = None
    stopped_at: Optional[datetime] = None
