"""Main FastAPI application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.auth import auth_backend, fastapi_users
from app.config import get_settings
from app.database import engine
from app.models.base import Base
from app.routers import chat, repos, sessions, settings as settings_router, voice
from app.routers.web import router as web_router
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.services.voice import ensure_model_loaded

settings = get_settings()
log = structlog.get_logger(__name__)

# ── Rate limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", env=settings.app_env)
    # Ensure static directories exist
    Path("app/static/css").mkdir(parents=True, exist_ok=True)
    Path("app/static/js").mkdir(parents=True, exist_ok=True)
    Path(settings.whisper_models_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.repos_dir).mkdir(parents=True, exist_ok=True)

    # Auto-create admin user if not present
    import asyncio
    asyncio.create_task(_ensure_admin())

    # Pre-load whisper model in background (non-blocking)
    asyncio.create_task(_preload_whisper())

    yield
    log.info("shutdown")


async def _preload_whisper():
    try:
        await ensure_model_loaded()
    except Exception as exc:
        log.error("whisper_preload_failed", error=str(exc))


async def _ensure_admin():
    """Create the admin superuser on startup if it doesn't already exist."""
    if not settings.admin_password:
        log.warning("admin_skip", reason="ADMIN_PASSWORD not set in env")
        return
    from app.database import async_session_factory
    from app.dependencies import get_user_db
    from app.auth import UserManager
    from fastapi_users.exceptions import UserAlreadyExists
    from app.schemas.user import UserCreate

    async with async_session_factory() as session:
        async for user_db in get_user_db(session):
            manager = UserManager(user_db)
            try:
                await manager.create(
                    UserCreate(
                        email=settings.admin_email,
                        password=settings.admin_password,
                        is_superuser=True,
                        is_active=True,
                        is_verified=True,
                    ),
                    safe=False,
                )
                log.info("admin_created", email=settings.admin_email)
            except UserAlreadyExists:
                pass  # already set up
            except Exception as exc:
                log.error("admin_create_failed", error=str(exc))


# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore

# ── Static files ──────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# ── fastapi-users auth routes ─────────────────────────────────────────────────
app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)

# ── API routers ───────────────────────────────────────────────────────────────
app.include_router(repos.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(settings_router.router)

# ── Web (HTML) router ─────────────────────────────────────────────────────────
app.include_router(web_router)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "version": "1.0.0"}


# ── Logging config ────────────────────────────────────────────────────────────
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(settings.log_level.upper())
    ),
)
