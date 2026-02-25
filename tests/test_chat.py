"""Chat / SSE streaming tests."""
from __future__ import annotations

import json
import uuid
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_chat_stream_no_session(auth_client: AsyncClient):
    fake_id = uuid.uuid4()
    r = await auth_client.post(
        f"/api/chat/{fake_id}/stream",
        json={"session_id": str(fake_id), "message": "hello", "history": []},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
@patch("app.routers.chat.get_agent_client")
@patch("app.routers.sessions.get_docker_manager")
async def test_chat_stream_running_session(mock_dm_factory, mock_agent_cls, auth_client: AsyncClient):
    """Start a session, then chat with it – stream mock SSE chunks."""
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})

    # Mock docker
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value={
        "container_id": "cid123", "container_name": "rv-agent-test",
        "network_name": "net-test", "code_server_port": 9000, "agent_api_port": 9001,
    })
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post(
        "/api/sessions",
        json={"repo_full_name": "u/r", "repo_name": "r", "branch": "main"},
    )
    assert r.status_code == 201
    session_id = r.json()["id"]

    # Mock agent client stream
    from app.schemas.chat import StreamChunk, ChunkType

    async def fake_stream(*args, **kwargs):
        yield StreamChunk(type=ChunkType.thinking, content="Thinking…")
        yield StreamChunk(type=ChunkType.text, content="Here is my answer.")
        yield StreamChunk(type=ChunkType.done, content="")

    mock_agent = MagicMock()
    mock_agent.stream_chat = fake_stream
    mock_agent_cls.return_value = mock_agent

    r = await auth_client.post(
        f"/api/chat/{session_id}/stream",
        json={"session_id": session_id, "message": "Say hello", "history": []},
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    body = r.text
    assert "data:" in body
    assert "Thinking" in body or "answer" in body


@pytest.mark.asyncio
@patch("app.routers.chat.get_agent_client")
@patch("app.routers.sessions.get_docker_manager")
async def test_create_pr_endpoint(mock_dm_factory, mock_agent_cls, auth_client: AsyncClient):
    """Test the create-pr endpoint."""
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})

    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value={
        "container_id": "cid456", "container_name": "rv-agent-pr",
        "network_name": "net-pr", "code_server_port": 9010, "agent_api_port": 9011,
    })
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post(
        "/api/sessions",
        json={"repo_full_name": "u/r2", "repo_name": "r2", "branch": "main"},
    )
    assert r.status_code == 201
    session_id = r.json()["id"]

    mock_agent = MagicMock()
    mock_agent.trigger_pr = AsyncMock(return_value={
        "branch": "feature/my-feature-123456",
        "pr_url": "https://github.com/u/r2/pull/1",
        "output": "Pull request created.",
    })
    mock_agent_cls.return_value = mock_agent

    r = await auth_client.post(
        f"/api/chat/{session_id}/create-pr",
        params={"feature_name": "my feature"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "pr_url" in data
    assert "branch" in data
