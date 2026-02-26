"""Shared pytest fixtures for all test modules."""
from __future__ import annotations

import asyncio
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.dependencies import get_db
from app.models.base import Base

# ── Event loop (session-scoped) ───────────────────────────────────────────────
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── In-memory SQLite DB ───────────────────────────────────────────────────────
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def engine_fixture():
    engine = create_async_engine(
        TEST_DB_URL, echo=False, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(engine_fixture) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(engine_fixture, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """FastAPI test client backed by in-memory SQLite."""
    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient) -> AsyncClient:
    """Test client pre-authenticated with a fresh user account."""
    uid = uuid.uuid4().hex[:8]
    email = f"testuser_{uid}@example.com"
    password = "SecurePass123!"

    r = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": f"User {uid}"},
    )
    assert r.status_code in (200, 201), f"Registration failed: {r.text}"

    r = await client.post(
        "/auth/jwt/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200, f"Login failed: {r.text}"
    token = r.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest_asyncio.fixture
async def auth_client_with_pat(auth_client: AsyncClient) -> AsyncClient:
    """Authenticated client that also has a GitHub PAT set."""
    r = await auth_client.patch("/api/settings", json={"github_pat": "ghp_faketoken12345678"})
    assert r.status_code == 200
    return auth_client
