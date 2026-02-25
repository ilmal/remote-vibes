"""Agent session model – tracks per-repo Docker containers."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
import sqlalchemy as sa

from app.models.base import Base


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Repo info
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)  # owner/repo
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)       # short name
    branch: Mapped[str] = mapped_column(String(255), default="main")

    # Container info
    container_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    container_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    code_server_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    agent_api_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Status: pending | running | stopped | error
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)

    # Tunnel
    tunnel_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    tunnel_active: Mapped[bool] = mapped_column(sa.Boolean, default=False)

    # PR tracking
    last_pr_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    last_pr_title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
