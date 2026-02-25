"""Settings router – update user PAT, cloudflare token, etc."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_active_user
from app.dependencies import get_db
from app.models.user import User

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    github_pat: str | None = None
    cloudflare_token: str | None = None
    display_name: str | None = None


class SettingsRead(BaseModel):
    display_name: str
    github_pat_set: bool
    cloudflare_token_set: bool
    email: str


@router.get("", response_model=SettingsRead)
async def get_settings_view(user: User = Depends(current_active_user)) -> SettingsRead:
    return SettingsRead(
        display_name=user.display_name,
        github_pat_set=bool(user.github_pat),
        cloudflare_token_set=bool(user.cloudflare_token),
        email=user.email,
    )


@router.patch("", response_model=SettingsRead)
async def update_settings(
    body: SettingsUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> SettingsRead:
    if body.github_pat is not None:
        user.github_pat = body.github_pat
    if body.cloudflare_token is not None:
        user.cloudflare_token = body.cloudflare_token
    if body.display_name is not None:
        user.display_name = body.display_name

    db.add(user)
    await db.flush()
    await db.refresh(user)

    return SettingsRead(
        display_name=user.display_name,
        github_pat_set=bool(user.github_pat),
        cloudflare_token_set=bool(user.cloudflare_token),
        email=user.email,
    )
