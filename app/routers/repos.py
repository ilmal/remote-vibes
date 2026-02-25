"""Repository router – list and get GitHub repos."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.auth import current_active_user
from app.models.user import User
from app.services.github import GitHubService

router = APIRouter(prefix="/api/repos", tags=["repos"])
limiter = Limiter(key_func=get_remote_address)


def _get_github(user: User = Depends(current_active_user)) -> GitHubService:
    pat = user.github_pat or ""
    if not pat:
        from app.config import get_settings

        pat = get_settings().github_pat
    if not pat:
        raise HTTPException(status_code=422, detail="No GitHub PAT configured. Visit /settings.")
    return GitHubService(pat)


@router.get("", response_model=list[dict])
async def list_repos(
    gh: GitHubService = Depends(_get_github),
) -> Any:
    try:
        return gh.list_repos()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"GitHub error: {exc}")


@router.get("/me")
async def github_me(gh: GitHubService = Depends(_get_github)) -> Any:
    try:
        return gh.get_user_info()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
