"""Docker manager unit tests (mocked)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


MOCK_CONTAINER = MagicMock()
MOCK_CONTAINER.id = "abc123def456" * 3
MOCK_CONTAINER.name = "rv-agent-abc123"
MOCK_CONTAINER.status = "running"
MOCK_CONTAINER.labels = {"rv.session_id": "test-session-id", "rv.repo": "u/r", "rv.managed": "true"}


def _make_dm():
    """Create a DockerManager with a mocked docker client."""
    with patch("app.services.docker_manager.docker.from_env") as mock_from_env:
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        from app.services.docker_manager import DockerManager
        dm = DockerManager.__new__(DockerManager)
        dm._client = mock_client
        dm._locks = {}
        return dm, mock_client


def test_get_container_status_running():
    dm, mock_client = _make_dm()
    mock_client.containers.get.return_value = MOCK_CONTAINER
    assert dm.get_container_status("abc123") == "running"


def test_get_container_status_not_found():
    import docker.errors
    dm, mock_client = _make_dm()
    mock_client.containers.get.side_effect = docker.errors.NotFound("gone")
    assert dm.get_container_status("abc123") == "not_found"


def test_list_managed_containers():
    dm, mock_client = _make_dm()
    mock_client.containers.list.return_value = [MOCK_CONTAINER]
    result = dm.list_managed_containers()
    assert len(result) == 1
    assert result[0]["session_id"] == "test-session-id"


def test_cleanup_stale_containers():
    de_container = MagicMock()
    dm, mock_client = _make_dm()
    mock_client.containers.list.return_value = [de_container]
    removed = dm.cleanup_stale_containers()
    assert removed == 1
    de_container.remove.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_stop_container_not_found():
    """stop_container should not raise when container is missing."""
    import docker.errors
    dm, mock_client = _make_dm()
    mock_client.containers.get.side_effect = docker.errors.NotFound("gone")
    # Should not raise
    await dm.stop_container("abc123")


def test_find_free_port():
    from app.services.docker_manager import _find_free_port
    port = _find_free_port(start=9800)
    assert 9800 <= port <= 9999


def test_get_docker_manager_singleton():
    from app.services import docker_manager as dm_module
    dm_module._docker_manager = None
    with patch("app.services.docker_manager.docker.from_env"):
        m1 = dm_module.get_docker_manager()
        m2 = dm_module.get_docker_manager()
    assert m1 is m2
    dm_module._docker_manager = None
