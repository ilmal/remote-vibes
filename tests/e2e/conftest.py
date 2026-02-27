"""E2E test fixtures — connect to the live stack on localhost:8001."""
from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio

# ── Live server config ────────────────────────────────────────────────────────
BASE_URL = os.getenv("RV_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.getenv("RV_ADMIN_EMAIL", "nils@u1.se")
ADMIN_PASSWORD = os.getenv("RV_ADMIN_PASSWORD", "NilsGustav")
GITHUB_PAT = os.getenv("GITHUB_PAT", "")
TEST_REPO = os.getenv("RV_TEST_REPO", "ilmal/lawcrawl")

# How long to wait for a session to reach "running" state (seconds)
SESSION_STARTUP_TIMEOUT = int(os.getenv("RV_SESSION_TIMEOUT", "120"))
# How long to wait for the dev server port to open (seconds)
DEV_SERVER_TIMEOUT = int(os.getenv("RV_DEV_TIMEOUT", "240"))

# Module-level cache so the docker session isn't created per-test
_session_cache: dict = {}


async def _get_token() -> str:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20.0) as c:
        r = await c.post(
            "/auth/jwt/login",
            data={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        assert r.status_code == 200, f"Login failed {r.status_code}: {r.text}"
        return r.json()["access_token"]


@pytest_asyncio.fixture
async def http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Unauthenticated async client."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20.0) as client:
        yield client


@pytest_asyncio.fixture
async def auth_token() -> str:
    """Log in and return a Bearer token."""
    return await _get_token()


@pytest_asyncio.fixture
async def authed() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Authenticated async client."""
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=30.0,
        headers=headers,
        follow_redirects=True,
    ) as c:
        yield c


@pytest_asyncio.fixture
async def session_info(authed: httpx.AsyncClient) -> dict:
    """Find a running session for TEST_REPO, creating one if needed.
    Result is cached for the test process lifetime so docker startup only happens once.
    """
    if _session_cache.get("data"):
        return _session_cache["data"]

    # ── look for an existing running session ─────────────────────────────────
    r = await authed.get("/api/sessions")
    assert r.status_code == 200
    for s in r.json():
        if s.get("repo_full_name") == TEST_REPO and s.get("status") == "running":
            _session_cache["data"] = s
            return s

    # ── no running session — create one ──────────────────────────────────────
    assert GITHUB_PAT, "Set GITHUB_PAT env var to create a new session"
    repo_name = TEST_REPO.split("/", 1)[1]
    r = await authed.post("/api/sessions/", json={
        "repo_full_name": TEST_REPO,
        "repo_name": repo_name,
        "github_pat": GITHUB_PAT,
        "branch": "main",
    })
    assert r.status_code in (200, 201), f"Create session failed: {r.text}"
    session_id = r.json()["id"]

    # ── poll until running ────────────────────────────────────────────────────
    deadline = time.time() + SESSION_STARTUP_TIMEOUT
    while time.time() < deadline:
        r = await authed.get(f"/api/sessions/{session_id}/status")
        if r.status_code == 200 and r.json().get("container_status") == "running":
            r2 = await authed.get(f"/api/sessions/{session_id}")
            assert r2.status_code == 200
            _session_cache["data"] = r2.json()
            return _session_cache["data"]
        await asyncio.sleep(5)

    pytest.fail(f"Session {session_id} did not reach 'running' within {SESSION_STARTUP_TIMEOUT}s")
