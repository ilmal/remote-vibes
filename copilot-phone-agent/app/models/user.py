"""User model compatible with fastapi-users."""
from __future__ import annotations

import uuid

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(SQLAlchemyBaseUserTableUUID, Base):
    """Application user – single-user setup recommended, but multi-user capable."""

    __tablename__ = "users"

    # Extra profile fields
    display_name: Mapped[str] = mapped_column(String(120), default="Admin")
    github_pat: Mapped[str] = mapped_column(String(512), default="")
    cloudflare_token: Mapped[str] = mapped_column(String(512), default="")
    is_setup_complete: Mapped[bool] = mapped_column(Boolean, default=False)
