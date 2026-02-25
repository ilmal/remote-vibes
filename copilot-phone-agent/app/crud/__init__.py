"""Session CRUD operations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import AgentSession
from app.schemas.session import AgentSessionCreate, AgentSessionUpdate


async def create_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    data: AgentSessionCreate,
) -> AgentSession:
    session = AgentSession(
        user_id=user_id,
        repo_full_name=data.repo_full_name,
        repo_name=data.repo_name,
        branch=data.branch,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> Optional[AgentSession]:
    result = await db.execute(select(AgentSession).where(AgentSession.id == session_id))
    return result.scalar_one_or_none()


async def list_sessions(
    db: AsyncSession,
    user_id: uuid.UUID,
    active_only: bool = False,
) -> Sequence[AgentSession]:
    q = select(AgentSession).where(AgentSession.user_id == user_id)
    if active_only:
        q = q.where(AgentSession.status == "running")
    q = q.order_by(AgentSession.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


async def update_session(
    db: AsyncSession,
    session: AgentSession,
    data: AgentSessionUpdate,
) -> AgentSession:
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(session, field, value)
    await db.flush()
    await db.refresh(session)
    return session


async def stop_session(db: AsyncSession, session: AgentSession) -> AgentSession:
    session.status = "stopped"
    session.stopped_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(session)
    return session
