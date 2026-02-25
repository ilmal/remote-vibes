"""Agent runner FastAPI – runs INSIDE each per-repo container.
Provides /chat/stream (SSE) and /git/create-pr endpoints.
Uses subprocess to call GitHub Copilot CLI or a Copilot SDK stub."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

log = structlog.get_logger(__name__)

app = FastAPI(title="CPA Agent Runner", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

GITHUB_PAT = os.environ.get("GITHUB_PAT", "")
REPO_FULL_NAME = os.environ.get("REPO_FULL_NAME", "")
REPO_NAME = os.environ.get("REPO_NAME", "")
WORKSPACE = Path(f"/workspace/{REPO_NAME}") if REPO_NAME else Path("/workspace")


# ── Pydantic models ───────────────────────────────────────────────────────────

class ChatPayload(BaseModel):
    message: str
    history: list[dict] = []
    session_id: str = ""


class PRPayload(BaseModel):
    feature_name: str
    session_id: str = ""


# ── SSE helpers ───────────────────────────────────────────────────────────────

def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def sse_done() -> str:
    return "data: [DONE]\n\n"


# ── Agent logic ───────────────────────────────────────────────────────────────

async def run_agent(  # noqa: C901
    message: str, history: list[dict]
) -> AsyncGenerator[str, None]:
    """
    Drive Copilot SDK / fallback to gh copilot CLI / or a built-in planner.
    Yields SSE data lines.
    """
    # ── 1. Thinking ────────────────────────────────────────────────────────
    yield sse({"type": "thinking", "content": "Analysing request…"})
    await asyncio.sleep(0.1)

    # ── 2. Parse intent ────────────────────────────────────────────────────
    low = message.lower()
    if any(w in low for w in ("git status", "status")):
        yield sse({"type": "tool_call", "content": "Running: git status", "tool_name": "shell"})
        out = await _shell("git status", cwd=WORKSPACE)
        yield sse({"type": "tool_result", "content": out, "tool_name": "shell"})
        yield sse({"type": "text", "content": f"```\n{out}\n```"})
        yield sse_done()
        return

    if any(w in low for w in ("git log", "log")):
        yield sse({"type": "tool_call", "content": "Running: git log --oneline -10", "tool_name": "shell"})
        out = await _shell("git log --oneline -10", cwd=WORKSPACE)
        yield sse({"type": "tool_result", "content": out, "tool_name": "shell"})
        yield sse({"type": "text", "content": f"```\n{out}\n```"})
        yield sse_done()
        return

    # ── 3. Try gh copilot explain / suggest (if available) ─────────────────
    try:
        yield sse({"type": "status", "content": "Querying Copilot CLI…"})
        proc = await asyncio.create_subprocess_exec(
            "gh", "copilot", "suggest", "-t", "shell", message,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GH_TOKEN": GITHUB_PAT},
            cwd=str(WORKSPACE),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        suggestion = stdout.decode().strip()
        if suggestion:
            yield sse({"type": "text", "content": suggestion})
            yield sse_done()
            return
    except (FileNotFoundError, asyncio.TimeoutError, Exception):
        pass  # gh CLI not available or timed out

    # ── 4. Fallback: echo + run if looks like shell command ─────────────────
    # Simple heuristic: if message starts with run/execute/do, extract command
    match = re.search(r"(?:run|execute|do|try)\s+[`']?(.+?)[`']?\s*$", message, re.I)
    if match:
        cmd = match.group(1).strip("`'\"")
        yield sse({"type": "tool_call", "content": f"Running: {cmd}", "tool_name": "shell"})
        try:
            out = await asyncio.wait_for(_shell(cmd, cwd=WORKSPACE), timeout=20)
            yield sse({"type": "tool_result", "content": out, "tool_name": "shell"})
            yield sse({"type": "text", "content": f"Done.\n```\n{out}\n```"})
        except asyncio.TimeoutError:
            yield sse({"type": "error", "content": "Command timed out."})
        yield sse_done()
        return

    # ── 5. Generic response ────────────────────────────────────────────────
    yield sse({"type": "text", "content": (
        f"I'm your Copilot agent for **{REPO_FULL_NAME}**.\n\n"
        "I can help you:\n"
        "- Run shell commands\n"
        "- Check git status/log\n"
        "- Create branches & PRs (say 'done with feature X')\n\n"
        f"You said: *{message}*"
    )})
    yield sse_done()


async def _shell(cmd: str, cwd: Path | None = None) -> str:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode(errors="replace").strip()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "repo": REPO_FULL_NAME}


@app.post("/chat/stream")
async def chat_stream(payload: ChatPayload):
    async def event_generator():
        async for chunk in run_agent(payload.message, payload.history):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/git/create-pr")
async def create_pr(payload: PRPayload):
    feature = payload.feature_name

    async def _git(cmd: str) -> str:
        return await _shell(cmd, cwd=WORKSPACE)

    import time as _time
    branch = f"feature/{_slug(feature)}-{int(_time.time())}"

    try:
        await _git(f"git checkout -b {branch}")
        await _git("git add --all")
        try:
            await _git(f'git commit -m "feat: {feature}"')
        except Exception as exc:
            if "nothing to commit" not in str(exc).lower():
                raise

        auth_url = f"https://x-access-token:{GITHUB_PAT}@github.com/{REPO_FULL_NAME}.git"
        await _git(f"git push {auth_url} {branch}")

        # Open PR via gh CLI
        pr_body = f"Automated PR for feature: {feature}\n\nGenerated by Remote Vibes."
        proc_output = await _shell(
            f'gh pr create --title "feat: {feature}" --body "{pr_body}" '
            f'--head {branch} --base main',
            cwd=WORKSPACE,
        )
        # Parse URL from output
        import re
        match = re.search(r"https://github\.com/\S+", proc_output)
        pr_url = match.group(0) if match else ""

        return {"branch": branch, "pr_url": pr_url, "output": proc_output}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]
