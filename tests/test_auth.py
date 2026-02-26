"""Tests: registration, login, JWT, settings endpoints."""
from __future__ import annotations
import pytest
from httpx import AsyncClient


async def test_health(client: AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "version" in r.json()


async def test_register_success(client: AsyncClient):
    r = await client.post(
        "/auth/register",
        json={"email": "fresh@example.com", "password": "Pass123!", "display_name": "Fresh"},
    )
    assert r.status_code in (200, 201)
    data = r.json()
    assert data["email"] == "fresh@example.com"


async def test_register_and_login(client: AsyncClient):
    email, password = "logintest@example.com", "TestPass123!"
    r = await client.post("/auth/register", json={"email": email, "password": password, "display_name": "Login"})
    assert r.status_code in (200, 201)

    r = await client.post(
        "/auth/jwt/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    token_data = r.json()
    assert "access_token" in token_data
    assert token_data["token_type"] == "bearer"


async def test_register_duplicate_email(client: AsyncClient):
    payload = {"email": "dup@example.com", "password": "Dup123!", "display_name": "Dup"}
    r1 = await client.post("/auth/register", json=payload)
    assert r1.status_code in (200, 201)
    r2 = await client.post("/auth/register", json=payload)
    assert r2.status_code == 400


async def test_login_wrong_password(client: AsyncClient):
    email = "wrongpass@example.com"
    await client.post("/auth/register", json={"email": email, "password": "RealPass1!", "display_name": "T"})
    r = await client.post(
        "/auth/jwt/login",
        data={"username": email, "password": "WrongPass!"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 400


async def test_login_nonexistent_user(client: AsyncClient):
    r = await client.post(
        "/auth/jwt/login",
        data={"username": "nobody@example.com", "password": "Whatever1!"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 400


async def test_unauthenticated_repos_returns_401(client: AsyncClient):
    r = await client.get("/api/repos")
    assert r.status_code == 401


async def test_unauthenticated_sessions_returns_401(client: AsyncClient):
    r = await client.get("/api/sessions")
    assert r.status_code == 401


async def test_unauthenticated_settings_returns_401(client: AsyncClient):
    r = await client.get("/api/settings")
    assert r.status_code == 401


async def test_settings_get(auth_client: AsyncClient):
    r = await auth_client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert "email" in data
    assert "display_name" in data
    assert "github_pat_set" in data
    assert "cloudflare_token_set" in data
    assert data["github_pat_set"] is False


async def test_settings_update_display_name(auth_client: AsyncClient):
    r = await auth_client.patch("/api/settings", json={"display_name": "Updated Name"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "Updated Name"


async def test_settings_update_github_pat(auth_client: AsyncClient):
    r = await auth_client.patch("/api/settings", json={"github_pat": "ghp_testtoken_abc123"})
    assert r.status_code == 200
    data = r.json()
    assert data["github_pat_set"] is True


async def test_settings_update_cloudflare_token(auth_client: AsyncClient):
    r = await auth_client.patch("/api/settings", json={"cloudflare_token": "cf_test_token"})
    assert r.status_code == 200
    assert r.json()["cloudflare_token_set"] is True


async def test_settings_update_multiple_fields(auth_client: AsyncClient):
    r = await auth_client.patch(
        "/api/settings",
        json={"display_name": "Multi", "github_pat": "ghp_multi123", "cloudflare_token": "cf_multi"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["display_name"] == "Multi"
    assert data["github_pat_set"] is True
    assert data["cloudflare_token_set"] is True


async def test_get_current_user(auth_client: AsyncClient):
    r = await auth_client.get("/users/me")
    assert r.status_code == 200
    assert "email" in r.json()
