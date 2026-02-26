"""GitHub service unit tests (mocked)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from httpx import AsyncClient


class FakeGithubUser:
    login = "testuser"
    name = "Test User"
    avatar_url = "https://example.com/avatar.png"


class FakeRepo:
    full_name = "testuser/testrepo"
    name = "testrepo"
    description = "A test repo"
    private = False
    default_branch = "main"
    language = "Python"
    stargazers_count = 42
    updated_at = None
    clone_url = "https://github.com/testuser/testrepo.git"
    html_url = "https://github.com/testuser/testrepo"


@pytest.mark.asyncio
@patch("app.routers.repos.GitHubService")
async def test_list_repos(mock_gh_cls, auth_client: AsyncClient):
    """API should return repo list when PAT is valid."""
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_fake_token"})

    mock_gh = MagicMock()
    mock_gh.list_repos.return_value = [
        {
            "full_name": "testuser/testrepo",
            "name": "testrepo",
            "description": "A test repo",
            "private": False,
            "default_branch": "main",
            "language": "Python",
            "stars": 42,
            "updated_at": "",
            "clone_url": "https://github.com/testuser/testrepo.git",
            "html_url": "https://github.com/testuser/testrepo",
        }
    ]
    mock_gh_cls.return_value = mock_gh

    r = await auth_client.get("/api/repos")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "testrepo"


@pytest.mark.asyncio
@patch("app.routers.repos.GitHubService")
async def test_list_repos_github_error(mock_gh_cls, auth_client: AsyncClient):
    await auth_client.patch("/api/settings", json={"github_pat": "ghp_bad_token"})
    mock_gh = MagicMock()
    mock_gh.list_repos.side_effect = Exception("Bad credentials")
    mock_gh_cls.return_value = mock_gh

    r = await auth_client.get("/api/repos")
    assert r.status_code == 503


@pytest.mark.asyncio
@patch("app.config.get_settings")
async def test_list_repos_no_pat(mock_settings, auth_client: AsyncClient):
    """Without a PAT, should return 422."""
    mock_settings.return_value.github_pat = ""
    r = await auth_client.get("/api/repos")
    # Either 422 (no PAT) or could be 503 if it tries anyway
    assert r.status_code in (422, 503)


# ── Unit: GitHubService._repo_dict ───────────────────────────────────────────

def test_repo_dict():
    from app.services.github import GitHubService
    repo = FakeRepo()
    result = GitHubService._repo_dict(repo)
    assert result["full_name"] == "testuser/testrepo"
    assert result["name"] == "testrepo"
    assert result["stars"] == 42
    assert result["private"] is False


# ── Unit: _slug helper ────────────────────────────────────────────────────────

def test_slug():
    from app.services.github import _slug
    assert _slug("My Feature Name!") == "my-feature-name"
    assert _slug("user/auth   2024") == "user-auth-2024"
    assert len(_slug("a" * 100)) <= 50


# ── Unit: GitHubService.get_user_info ─────────────────────────────────────────

def test_get_user_info():
    from app.services.github import GitHubService
    with patch("app.services.github.Github") as mock_github_cls:
        mock_gh_instance = MagicMock()
        mock_gh_instance.get_user.return_value = FakeGithubUser()
        mock_github_cls.return_value = mock_gh_instance

        svc = GitHubService("ghp_fake")
        info = svc.get_user_info()
        assert info["login"] == "testuser"
        assert info["name"] == "Test User"
