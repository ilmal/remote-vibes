"""Tests: session CRUD – create, list, get, status, stop."""
from __future__ import annotations
import uuid
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch

MOCK_CONTAINER = {
    "container_id": "deadbeef" * 8,
    "container_name": "rv-agent-deadbeef",
    "network_name": "rv-net-deadbeef",
    "code_server_port": 9000,
    "agent_api_port": 9001,
    "dev_server_port": 9002,
}
SESSION_PAYLOAD = {"repo_full_name": "user/repo", "repo_name": "repo", "branch": "main"}


async def test_list_sessions_empty(auth_client: AsyncClient):
    r = await auth_client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == []


@patch("app.routers.sessions.settings")
async def test_start_session_no_pat(mock_settings, auth_client: AsyncClient):
    """No PAT → 422 when neither user nor global settings have a PAT."""
    mock_settings.github_pat = ""
    r = await auth_client.post("/api/sessions", json=SESSION_PAYLOAD)
    assert r.status_code == 422


@patch("app.routers.sessions.get_docker_manager")
async def test_start_session_with_pat(mock_dm_factory, auth_client: AsyncClient):
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER)
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post("/api/sessions", json=SESSION_PAYLOAD)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["repo_name"] == "repo"
    assert data["status"] == "running"
    assert data["code_server_port"] == 9000
    assert data["agent_api_port"] == 9001


@patch("app.routers.sessions.get_docker_manager")
async def test_get_session_by_id(mock_dm_factory, auth_client: AsyncClient):
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER)
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post("/api/sessions", json=SESSION_PAYLOAD)
    assert r.status_code == 201
    session_id = r.json()["id"]

    r = await auth_client.get(f"/api/sessions/{session_id}")
    assert r.status_code == 200
    assert r.json()["id"] == session_id


@patch("app.routers.sessions.get_docker_manager")
async def test_get_session_status(mock_dm_factory, auth_client: AsyncClient):
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER)
    mock_dm.get_container_status = MagicMock(return_value="running")
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post("/api/sessions", json=SESSION_PAYLOAD)
    session_id = r.json()["id"]

    r = await auth_client.get(f"/api/sessions/{session_id}/status")
    assert r.status_code == 200
    data = r.json()
    assert "db_status" in data
    assert "container_status" in data


@patch("app.routers.sessions.get_docker_manager")
async def test_stop_session(mock_dm_factory, auth_client: AsyncClient):
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER)
    mock_dm.stop_container = AsyncMock()
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post("/api/sessions", json=SESSION_PAYLOAD)
    session_id = r.json()["id"]

    r = await auth_client.delete(f"/api/sessions/{session_id}")
    assert r.status_code == 204


@patch("app.routers.sessions.get_docker_manager")
async def test_stop_session_then_status_is_stopped(mock_dm_factory, auth_client: AsyncClient):
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER)
    mock_dm.stop_container = AsyncMock()
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post("/api/sessions", json=SESSION_PAYLOAD)
    session_id = r.json()["id"]
    await auth_client.delete(f"/api/sessions/{session_id}")

    r = await auth_client.get(f"/api/sessions/{session_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


async def test_get_nonexistent_session(auth_client: AsyncClient):
    r = await auth_client.get(f"/api/sessions/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_delete_nonexistent_session(auth_client: AsyncClient):
    r = await auth_client.delete(f"/api/sessions/{uuid.uuid4()}")
    assert r.status_code == 404


@patch("app.routers.sessions.get_docker_manager")
async def test_list_sessions_after_create(mock_dm_factory, auth_client: AsyncClient):
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake"})
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER)
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post("/api/sessions", json=SESSION_PAYLOAD)
    assert r.status_code == 201

    r = await auth_client.get("/api/sessions")
    assert r.status_code == 200
    sessions = r.json()
    assert len(sessions) >= 1
    assert any(s["repo_name"] == "repo" for s in sessions)
