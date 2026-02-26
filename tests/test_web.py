"""Tests: HTML web routes (login page, web login form, dashboard, logout)."""
from __future__ import annotations
import pytest
from httpx import AsyncClient


async def test_login_page_loads(client: AsyncClient):
    r = await client.get("/login")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert b"Sign In" in r.content or b"login" in r.content.lower()


async def test_register_page_redirects_to_login(client: AsyncClient):
    r = await client.get("/register", follow_redirects=False)
    assert r.status_code in (301, 302, 303, 307, 308)
    assert "/login" in r.headers["location"]


async def test_root_unauthenticated_redirects_to_login(client: AsyncClient):
    r = await client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 303, 307, 308)


async def test_dashboard_unauthenticated_redirects_to_login(client: AsyncClient):
    r = await client.get("/dashboard", follow_redirects=False)
    assert r.status_code in (301, 302, 303, 307, 308)
    assert "login" in r.headers["location"].lower()


async def test_web_login_wrong_credentials(client: AsyncClient):
    r = await client.post(
        "/auth/web/login",
        data={"email": "nobody@example.com", "password": "wrong"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code in (401, 400)


async def test_web_login_success_sets_cookie(client: AsyncClient):
    email, password = "weblogintest@example.com", "WebPass123!"
    await client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": "Web"},
    )
    r = await client.post(
        "/auth/web/login",
        data={"email": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "location" in r.headers
    assert "/dashboard" in r.headers["location"]
    set_cookie = r.headers.get("set-cookie", "")
    assert "access_token" in set_cookie


async def test_web_login_then_dashboard_returns_200(client: AsyncClient):
    email, password = "dashtest@example.com", "DashPass123!"
    await client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": "Dash"},
    )
    r = await client.post(
        "/auth/web/login",
        data={"email": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"dashboard" in r.content.lower() or b"remote vibes" in r.content.lower()


async def test_logout_clears_cookie(client: AsyncClient):
    r = await client.post("/auth/web/logout", follow_redirects=False)
    assert r.status_code in (301, 302, 303, 307, 308)
    assert "login" in r.headers["location"].lower()
    # Cookie should be cleared
    set_cookie = r.headers.get("set-cookie", "")
    assert "access_token" in set_cookie


async def test_settings_page_unauthenticated(client: AsyncClient):
    r = await client.get("/settings-page", follow_redirects=False)
    assert r.status_code in (301, 302, 303, 307, 308)
    assert "login" in r.headers["location"].lower()
