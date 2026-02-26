"""Tests: chat streaming and PR creation."""
from __future__ import annotations
import uuid
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch

MOCK_CONTAINER = {
    "container_id": "chatcafe" * 8,
    "container_name": "rv-agent-chat",
    "network_name": "rv-net-chat",
    "code_server_port": 9100,
    "agent_api_port": 9101,
}
SESSION_PAYLOAD = {"repo_full_name": "user/chatrepo", "repo_name": "chatrepo", "branch": "main"}


async def test_chat_nonexistent_session(auth_client: AsyncClient):
    sid = str(uuid.uuid4())
    r = await auth_client.post(
        f"/api/chat/{sid}/stream",
        json={"session_id": sid, "message": "hello", "history": []},
    )
    assert r.status_code == 404


@patch("app.routers.sessions.get_docker_manager")
async def test_chat_session_not_running(mock_dm_factory, auth_client: AsyncClient):
    """A session in status != running should return 409."""
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER)
    mock_dm.stop_container = AsyncMock()
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post("/api/sessions", json=SESSION_PAYLOAD)
    session_id = r.json()["id"]
    # Stop it
    await auth_client.delete(f"/api/sessions/{session_id}")

    r = await auth_client.post(
        f"/api/chat/{session_id}/stream",
        json={"session_id": session_id, "message": "hello", "history": []},
    )
    assert r.status_code == 409


@patch("app.routers.sessions.get_docker_manager")
@patch("app.routers.chat.get_agent_client")
async def test_chat_stream_running_session(mock_agent_cls, mock_dm_factory, auth_client: AsyncClient):
    """Chat stream on a running session returns 200 SSE."""
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER)
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post("/api/sessions", json=SESSION_PAYLOAD)
    assert r.status_code == 201
    session_id = r.json()["id"]

    # Mock agent client
    from app.schemas.chat import StreamChunk
    async def mock_stream(*args, **kwargs):
        yield StreamChunk(type="text", content="Hello!")
    mock_agent = MagicMock()
    mock_agent.stream_chat = mock_stream
    mock_agent_cls.return_value = mock_agent

    r = await auth_client.post(
        f"/api/chat/{session_id}/stream",
        json={"session_id": session_id, "message": "hello", "history": []},
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]


async def test_create_pr_nonexistent_session(auth_client: AsyncClient):
    r = await auth_client.post(
        f"/api/chat/{uuid.uuid4()}/create-pr",
        params={"feature_name": "my-feature"},
    )
    assert r.status_code == 404


@patch("app.routers.sessions.get_docker_manager")
async def test_create_pr_session_not_running(mock_dm_factory, auth_client: AsyncClient):
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER)
    mock_dm.stop_container = AsyncMock()
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post("/api/sessions", json=SESSION_PAYLOAD)
    session_id = r.json()["id"]
    await auth_client.delete(f"/api/sessions/{session_id}")

    r = await auth_client.post(
        f"/api/chat/{session_id}/create-pr",
        params={"feature_name": "feature"},
    )
    assert r.status_code == 409


@patch("app.routers.sessions.get_docker_manager")
@patch("app.routers.chat.get_agent_client")
async def test_create_pr_success(mock_agent_cls, mock_dm_factory, auth_client: AsyncClient):
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER)
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post("/api/sessions", json=SESSION_PAYLOAD)
    session_id = r.json()["id"]

    mock_agent = MagicMock()
    mock_agent.trigger_pr = AsyncMock(return_value={"pr_url": "https://github.com/user/repo/pull/1", "pr_number": 1})
    mock_agent_cls.return_value = mock_agent

    r = await auth_client.post(
        f"/api/chat/{session_id}/create-pr",
        params={"feature_name": "cool-feature"},
    )
    assert r.status_code == 200
    assert "pr_url" in r.json()
