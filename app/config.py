"""Application configuration via pydantic-settings."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_name: str = "Remote Vibes"
    app_env: Literal["development", "production", "test"] = "production"
    debug: bool = False
    log_level: str = "info"
    secret_key: str = Field(..., description="JWT signing secret – use openssl rand -hex 32")
    access_token_expire_minutes: int = 10080  # 7 days

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = Field(..., description="asyncpg connection string")
    database_url_sync: str = Field(
        default="",
        description="psycopg2 sync URL for Alembic",
    )

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── GitHub ────────────────────────────────────────────────────────────────
    github_pat: str = Field(default="", description="GitHub Personal Access Token")

    # ── Cloudflare ────────────────────────────────────────────────────────────
    cloudflare_tunnel_token: str = ""

    # ── Whisper / Voice ───────────────────────────────────────────────────────
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_models_dir: str = "/app/whisper_models"

    # ── Agent Containers ──────────────────────────────────────────────────────
    agent_image: str = "rv_agent:latest"
    agent_base_port: int = 9000
    repos_dir: str = "/app/repos"

    @field_validator("database_url_sync", mode="before")
    @classmethod
    def derive_sync_url(cls, v: str, info) -> str:  # noqa: ANN001
        if v:
            return v
        # Auto-derive sync URL from async URL
        async_url: str = info.data.get("database_url", "")
        return async_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
