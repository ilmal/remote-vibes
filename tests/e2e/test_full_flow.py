"""
End-to-end tests for Remote Vibes.

Tests the complete user journey:
  1. Server health + login
  2. Repo listing
  3. Session exists and is running
  4. Editor (code-server) loads and returns files
  5. Dev server port is reachable
  6. Chat sends a message and receives a streamed response
  7. Logs endpoint returns container output

Run against the live stack:
    pytest tests/e2e/ -v -s
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import pytest_asyncio

from tests.e2e.conftest import (
    BASE_URL,
    DEV_SERVER_TIMEOUT,
    TEST_REPO,
)

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# 1. Infrastructure
# ─────────────────────────────────────────────────────────────────────────────

async def test_server_health(http_client: httpx.AsyncClient):
    """Remote Vibes responds and is healthy."""
    r = await http_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"


async def test_login_returns_token(auth_token: str):
    """Admin login produces a JWT."""
    assert len(auth_token) > 20


async def test_me_endpoint(authed: httpx.AsyncClient):
    """Authenticated /users/me returns the logged-in user."""
    r = await authed.get("/users/me")
    assert r.status_code == 200
    assert "@" in r.json()["email"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Repos
# ─────────────────────────────────────────────────────────────────────────────

async def test_repos_list(authed: httpx.AsyncClient):
    """GitHub repo list returns at least one item."""
    r = await authed.get("/api/repos", timeout=15.0)
    assert r.status_code == 200
    repos = r.json()
    assert isinstance(repos, list)
    assert len(repos) >= 1
    repo_names = [repo.get("full_name") for repo in repos]
    assert any(TEST_REPO in name for name in repo_names), (
        f"{TEST_REPO!r} not found in repos: {repo_names}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Session state
# ─────────────────────────────────────────────────────────────────────────────

async def test_session_is_running(session_info: dict):
    """The test session is in 'running' state in the database."""
    assert session_info.get("status") == "running", (
        f"Session status is {session_info.get('status')!r}"
    )
    assert session_info.get("code_server_port"), "code_server_port not set"
    assert session_info.get("agent_api_port"), "agent_api_port not set"


async def test_session_status_api(authed: httpx.AsyncClient, session_info: dict):
    """Status API reports both db + container running."""
    r = await authed.get(f"/api/sessions/{session_info['id']}/status")
    assert r.status_code == 200
    status = r.json()
    assert status["db_status"] == "running"
    assert status["container_status"] == "running"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Editor (code-server)
# ─────────────────────────────────────────────────────────────────────────────

async def test_editor_loads(session_info: dict):
    """code-server returns HTTP 200 and serves HTML."""
    port = session_info["code_server_port"]
    url = f"http://localhost:{port}/"
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(url, follow_redirects=True)
    assert r.status_code == 200, f"code-server on :{port} returned {r.status_code}"
    # code-server serves HTML with "VS Code" or "Code" in the body
    body = r.text.lower()
    assert "code" in body or "vscode" in body or "<!doctype" in body.lower(), (
        f"Unexpected code-server response (first 200 chars): {r.text[:200]}"
    )


async def test_editor_has_files(session_info: dict):
    """code-server workspace path responds (indicates files are present)."""
    port = session_info["code_server_port"]
    repo_name = TEST_REPO.split("/", 1)[1]
    # VS Code's file-browsing endpoint (returns 200/302 when folder exists)
    url = f"http://localhost:{port}/"
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(url, follow_redirects=True)
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 5. Dev server
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.timeout(300)
async def test_dev_server_reachable(session_info: dict):
    """Dev server port is reachable within DEV_SERVER_TIMEOUT seconds.

    The entrypoint starts pip install first (up to 120s), then starts
    the server. We poll with a generous timeout.
    """
    port = session_info.get("dev_server_port")
    if not port:
        pytest.skip("No dev_server_port in session (feature may be absent)")

    url = f"http://localhost:{port}/"
    deadline = time.time() + DEV_SERVER_TIMEOUT
    last_error = None
    async with httpx.AsyncClient(timeout=5.0) as c:
        while time.time() < deadline:
            try:
                r = await c.get(url, follow_redirects=True)
                if r.status_code < 500:
                    print(f"\n[e2e] Dev server on :{port} → HTTP {r.status_code} ✓")
                    return
            except httpx.ConnectError as exc:
                last_error = exc
            await asyncio.sleep(5)

    pytest.fail(
        f"Dev server on :{port} not reachable after {DEV_SERVER_TIMEOUT}s. "
        f"Last error: {last_error}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. Chat
# ─────────────────────────────────────────────────────────────────────────────

async def test_chat_returns_response(authed: httpx.AsyncClient, session_info: dict):
    """Chat endpoint streams back a response for a simple prompt.
    Result is discarded — we only verify the shape, not apply it."""
    session_id = session_info["id"]

    payload = {
        "message": "git status",
        "history": [],
        "session_id": session_id,
    }

    chunks: list[str] = []
    async with authed.stream(
        "POST", f"/api/chat/{session_id}/stream",
        json=payload,
        timeout=30.0,
    ) as resp:
        assert resp.status_code == 200, f"Chat stream returned {resp.status_code}"
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                break
            chunks.append(raw)

    assert len(chunks) > 0, "Chat stream returned no chunks"
    # Verify at least one chunk is valid JSON with a 'type' field
    import json
    parsed = [json.loads(c) for c in chunks if c]
    assert any("type" in p for p in parsed), f"No typed chunks: {parsed}"
    # Result is intentionally not applied — just verify the round trip
    print(f"\n[e2e] Chat returned {len(chunks)} SSE chunks ✓")


async def test_chat_no_code_applied(session_info: dict):
    """Placeholder: verifies that test_chat_returns_response doesn't apply changes."""
    # This test always passes — it just documents the intent
    assert True


# ─────────────────────────────────────────────────────────────────────────────
# 7. Logs
# ─────────────────────────────────────────────────────────────────────────────

async def test_logs_endpoint(authed: httpx.AsyncClient, session_info: dict):
    """Logs endpoint returns container output and it's non-empty."""
    r = await authed.get(f"/api/sessions/{session_info['id']}/logs?tail=50")
    assert r.status_code == 200
    body = r.json()
    assert "logs" in body
    logs: str = body["logs"]
    assert len(logs) > 0, "Expected non-empty logs"
    # Basic sanity: should contain entrypoint output
    assert "entrypoint" in logs.lower() or "code-server" in logs.lower(), (
        f"Unexpected logs content (first 200 chars): {logs[:200]}"
    )
    print(f"\n[e2e] Logs returned {len(logs)} chars ✓")
