"""Auth tests: register, login, token."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_register_and_login(client: AsyncClient):
    email = "newuser@example.com"
    password = "TestPassword1!"

    # Register
    r = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": "New User"},
    )
    assert r.status_code in (200, 201), r.text
    data = r.json()
    assert data["email"] == email

    # Login
    r = await client.post(
        "/auth/jwt/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200, r.text
    assert "access_token" in r.json()


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    email = "dup@example.com"
    password = "TestPass1!"
    payload = {"email": email, "password": password, "display_name": "Dup"}
    r1 = await client.post("/auth/register", json=payload)
    assert r1.status_code in (200, 201)
    r2 = await client.post("/auth/register", json=payload)
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    email = "wrongpass@example.com"
    await client.post(
        "/auth/register",
        json={"email": email, "password": "RealPass1!", "display_name": "Tester"},
    )
    r = await client.post(
        "/auth/jwt/login",
        data={"username": email, "password": "WrongPass999!"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401(client: AsyncClient):
    r = await client.get("/api/repos")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_authenticated_settings(auth_client: AsyncClient):
    r = await auth_client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert "email" in data
    assert "github_pat_set" in data


@pytest.mark.asyncio
async def test_update_settings(auth_client: AsyncClient):
    r = await auth_client.patch(
        "/api/settings",
        json={"display_name": "Updated Name"},
    )
    assert r.status_code == 200
    assert r.json()["display_name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_github_pat(auth_client: AsyncClient):
    r = await auth_client.patch(
        "/api/settings",
        json={"github_pat": "ghp_testtoken12345"},
    )
    assert r.status_code == 200
    assert r.json()["github_pat_set"] is True
