"""Tests: GitHub repository endpoints."""
from __future__ import annotations
import pytest
from httpx import AsyncClient
from unittest.mock import MagicMock, patch, PropertyMock


MOCK_REPOS = [
    {"id": 1, "name": "my-repo", "full_name": "user/my-repo", "description": "Test", "private": False,
     "default_branch": "main", "language": "Python", "stargazers_count": 5, "updated_at": "2024-01-01"},
    {"id": 2, "name": "other-repo", "full_name": "user/other-repo", "description": None, "private": True,
     "default_branch": "main", "language": "Go", "stargazers_count": 0, "updated_at": "2024-01-02"},
]
MOCK_ME = {"login": "testuser", "name": "Test User", "email": "t@example.com", "public_repos": 2}


@patch("app.config.get_settings")
async def test_list_repos_no_pat_returns_422(mock_settings, auth_client: AsyncClient):
    mock_settings.return_value.github_pat = ""
    r = await auth_client.get("/api/repos")
    assert r.status_code == 422


@patch("app.config.get_settings")
async def test_github_me_no_pat_returns_422(mock_settings, auth_client: AsyncClient):
    mock_settings.return_value.github_pat = ""
    r = await auth_client.get("/api/repos/me")
    assert r.status_code == 422


@patch("app.routers.repos.GitHubService")
async def test_list_repos_with_pat(mock_gh_cls, auth_client_with_pat: AsyncClient):
    mock_gh = MagicMock()
    mock_gh.list_repos.return_value = MOCK_REPOS
    mock_gh_cls.return_value = mock_gh

    r = await auth_client_with_pat.get("/api/repos")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["name"] == "my-repo"


@patch("app.routers.repos.GitHubService")
async def test_list_repos_github_error(mock_gh_cls, auth_client_with_pat: AsyncClient):
    mock_gh = MagicMock()
    mock_gh.list_repos.side_effect = Exception("Bad credentials")
    mock_gh_cls.return_value = mock_gh

    r = await auth_client_with_pat.get("/api/repos")
    assert r.status_code == 503
    assert "GitHub error" in r.json()["detail"]


@patch("app.routers.repos.GitHubService")
async def test_github_me(mock_gh_cls, auth_client_with_pat: AsyncClient):
    mock_gh = MagicMock()
    mock_gh.get_user_info.return_value = MOCK_ME
    mock_gh_cls.return_value = mock_gh

    r = await auth_client_with_pat.get("/api/repos/me")
    assert r.status_code == 200
    assert r.json()["login"] == "testuser"


@patch("app.routers.repos.GitHubService")
async def test_github_me_error(mock_gh_cls, auth_client_with_pat: AsyncClient):
    mock_gh = MagicMock()
    mock_gh.get_user_info.side_effect = Exception("Token expired")
    mock_gh_cls.return_value = mock_gh

    r = await auth_client_with_pat.get("/api/repos/me")
    assert r.status_code == 503


async def test_repos_unauthenticated(client: AsyncClient):
    r = await client.get("/api/repos")
    assert r.status_code == 401
