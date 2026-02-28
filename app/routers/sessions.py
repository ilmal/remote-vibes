"""Sessions router – spin up/down per-repo agent containers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from docker.errors import DockerException
from fastapi import APIRouter, Depends, HTTPException, Path as FPath, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_active_user
from app.config import get_settings
import app.crud as crud
from app.dependencies import get_db
from app.models.user import User
from app.schemas.session import AgentSessionCreate, AgentSessionRead, AgentSessionUpdate
from app.services.docker_manager import get_docker_manager

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
settings = get_settings()


@router.get("", response_model=list[AgentSessionRead])
async def list_sessions(
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    return await crud.list_sessions(db, user.id)


@router.post("", response_model=AgentSessionRead, status_code=201)
async def start_session(
    body: AgentSessionCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    pat = user.github_pat or settings.github_pat
    if not pat:
        raise HTTPException(status_code=422, detail="GitHub PAT not configured.")

    session = await crud.create_session(db, user.id, body)

    try:
        dm = get_docker_manager()
    except DockerException as exc:
        await crud.update_session(db, session, AgentSessionUpdate(status="error"))
        raise HTTPException(
            status_code=503,
            detail="Cannot connect to Docker daemon. Check that the Docker socket is mounted and the app has permission.",
        )

    try:
        container_info = await dm.start_agent_container(
            session_id=str(session.id),
            repo_full_name=body.repo_full_name,
            repo_name=body.repo_name,
            github_pat=pat,
            cloudflare_token=user.cloudflare_token or settings.cloudflare_tunnel_token,
            branch=body.branch,
        )
        update = AgentSessionUpdate(
            status="running",
            container_id=container_info["container_id"],
            container_name=container_info["container_name"],
            code_server_port=container_info["code_server_port"],
            agent_api_port=container_info["agent_api_port"],
            dev_server_port=container_info["dev_server_port"],
        )
        session = await crud.update_session(db, session, update)
    except Exception as exc:
        await crud.update_session(db, session, AgentSessionUpdate(status="error"))
        raise HTTPException(status_code=500, detail=f"Container start failed: {exc}")

    return AgentSessionRead.model_validate(session)


@router.get("/{session_id}", response_model=AgentSessionRead)
async def get_session(
    session_id: uuid.UUID = FPath(...),
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    session = await crud.get_session(db, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")
    return AgentSessionRead.model_validate(session)


@router.get("/{session_id}/status")
async def get_session_status(
    session_id: uuid.UUID = FPath(...),
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    session = await crud.get_session(db, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")

    dm = get_docker_manager()
    live_status = (
        dm.get_container_status(session.container_id)
        if session.container_id
        else "unknown"
    )
    return {"session_id": str(session_id), "db_status": session.status, "container_status": live_status}


@router.get("/{session_id}/logs")
async def get_session_logs(
    session_id: uuid.UUID = FPath(...),
    tail: int = 300,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    session = await crud.get_session(db, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")
    if not session.container_id:
        return {"logs": "No container associated with this session.", "container_status": "none"}
    dm = get_docker_manager()
    logs = dm.get_container_logs(session.container_id, tail=tail)
    status = dm.get_container_status(session.container_id)
    return {"logs": logs, "container_status": status}


@router.get("/{session_id}/compose-containers")
async def list_compose_containers(
    session_id: uuid.UUID = FPath(...),
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """List docker-compose containers that the agent has joined for this session."""
    session = await crud.get_session(db, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")
    dm = get_docker_manager()
    containers = dm.get_compose_containers_for_session(str(session_id))
    return {"containers": containers}


@router.get("/{session_id}/compose-logs/{container_name:path}")
async def get_compose_container_logs(
    session_id: uuid.UUID = FPath(...),
    container_name: str = FPath(...),
    tail: int = 300,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Return stdout/stderr logs for a specific compose container by name."""
    session = await crud.get_session(db, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")
    dm = get_docker_manager()
    logs = dm.get_named_container_logs(container_name, tail=tail)
    return {"logs": logs, "container_name": container_name}


@router.post("/{session_id}/compose-restart/{service_name:path}")
async def restart_compose_service(
    session_id: uuid.UUID = FPath(...),
    service_name: str = FPath(...),
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Pull latest image and force-recreate a compose service via the agent container."""
    import asyncio
    session = await crud.get_session(db, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")
    dm = get_docker_manager()
    try:
        output = await asyncio.get_event_loop().run_in_executor(
            None, dm.restart_compose_service, str(session_id), service_name
        )
        return {"output": output, "service": service_name}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{session_id}")
async def stop_session(
    session_id: uuid.UUID = FPath(...),
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    session = await crud.get_session(db, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")

    dm = get_docker_manager()
    if session.container_id:
        try:
            await dm.stop_container(session.container_id)
        except Exception as exc:
            # Log but don't fail – still mark stopped
            import structlog
            structlog.get_logger().warning("stop_container_error", error=str(exc))

    await crud.stop_session(db, session)
    return Response(status_code=204)
