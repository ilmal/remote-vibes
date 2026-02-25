"""Web (HTML) router – serves HTMX dashboard and auth pages."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")


def _get_token_from_request(request: Request) -> Optional[str]:
    """Extract JWT from cookie (set at login) or Authorization header."""
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    return token


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    token = _get_token_from_request(request)
    if not token:
        return RedirectResponse("/login")
    return RedirectResponse("/dashboard")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    token = _get_token_from_request(request)
    if not token:
        return RedirectResponse("/login")
    return templates.TemplateResponse("dashboard.html", {"request": request})


@router.get("/settings-page", response_class=HTMLResponse)
async def settings_page(request: Request):
    token = _get_token_from_request(request)
    if not token:
        return RedirectResponse("/login")
    return templates.TemplateResponse("settings.html", {"request": request})


@router.post("/auth/web/login")
async def web_login(request: Request):
    """Handle login form, set cookie, redirect to dashboard."""
    import httpx

    form = await request.form()
    email = form.get("email", "")
    password = form.get("password", "")

    # Call the JWT login endpoint internally
    async with httpx.AsyncClient(base_url=str(request.base_url)) as client:
        resp = await client.post(
            "/auth/jwt/login",
            data={"username": email, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if resp.status_code != 200:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password."},
            status_code=401,
        )

    token = resp.json().get("access_token", "")
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(
        "access_token",
        token,
        httponly=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
    )
    return response


@router.post("/auth/web/logout")
async def web_logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("access_token")
    return response
