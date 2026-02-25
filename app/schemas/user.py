"""User schemas for fastapi-users."""
import uuid
from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    display_name: str
    github_pat: str
    is_setup_complete: bool


class UserCreate(schemas.BaseUserCreate):
    display_name: str = "Admin"


class UserUpdate(schemas.BaseUserUpdate):
    display_name: str | None = None
    github_pat: str | None = None
    cloudflare_token: str | None = None
