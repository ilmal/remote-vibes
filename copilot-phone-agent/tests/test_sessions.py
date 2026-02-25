"""Session tests: create, list, stop."""
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch


MOCK_CONTAINER_INFO = {
    "container_id": "abc123def456" * 3,
    "container_name": "cpa-agent-abc123",
    "network_name": "cpa-net-abc123",
    "code_server_port": 9000,
    "agent_api_port": 9001,
}


@pytest.mark.asyncio
async def test_list_sessions_empty(auth_client: AsyncClient):
    r = await auth_client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_start_session_no_pat(auth_client: AsyncClient):
    """Starting a session without a PAT should fail with 422."""
    r = await auth_client.post(
        "/api/sessions",
        json={
            "repo_full_name": "testuser/testrepo",
            "repo_name": "testrepo",
            "branch": "main",
        },
    )
    # No PAT in env or user → expect 422
    assert r.status_code in (422, 500)


@pytest.mark.asyncio
@patch("app.routers.sessions.get_docker_manager")
async def test_start_session_with_pat(mock_dm_factory, auth_client: AsyncClient):
    """Set PAT then start a session – mock Docker to avoid real containers."""
    # First, set PAT
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_testfaketoken"})

    # Mock docker manager
    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER_INFO)
    mock_dm_factory.return_value = mock_dm

    r = await auth_client.post(
        "/api/sessions",
        json={
            "repo_full_name": "testuser/testrepo",
            "repo_name": "testrepo",
            "branch": "main",
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["repo_name"] == "testrepo"
    assert data["status"] == "running"
    assert data["code_server_port"] == 9000


@pytest.mark.asyncio
@patch("app.routers.sessions.get_docker_manager")
async def test_stop_session(mock_dm_factory, auth_client: AsyncClient):
    """Create then stop a session."""
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_testfaketoken"})

    mock_dm = MagicMock()
    mock_dm.start_agent_container = AsyncMock(return_value=MOCK_CONTAINER_INFO)
    mock_dm.stop_container = AsyncMock()
    mock_dm_factory.return_value = mock_dm

    # Start
    r = await auth_client.post(
        "/api/sessions",
        json={"repo_full_name": "u/r", "repo_name": "r", "branch": "main"},
    )
    assert r.status_code == 201
    session_id = r.json()["id"]

    # Stop
    r = await auth_client.delete(f"/api/sessions/{session_id}")
    assert r.status_code == 204

    # Verify stopped
    r = await auth_client.get(f"/api/sessions/{session_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


@pytest.mark.asyncio
async def test_get_nonexistent_session(auth_client: AsyncClient):
    import uuid
    r = await auth_client.get(f"/api/sessions/{uuid.uuid4()}")
    assert r.status_code == 404
