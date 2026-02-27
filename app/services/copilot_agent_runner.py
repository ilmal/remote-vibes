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


# ── Subprocess helpers ────────────────────────────────────────────────────────

async def _shell(cmd: str, cwd: Path | None = None) -> str:
    log.info("shell_run", cmd=cmd, cwd=str(cwd))
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    stdout, _ = await proc.communicate()
    elapsed = time.monotonic() - t0
    result = stdout.decode(errors="replace").strip()
    log.info("shell_done", cmd=cmd, elapsed_s=round(elapsed, 2),
             rc=proc.returncode, output_len=len(result))
    return result


async def _run_gh_copilot(
    message: str, cwd: Path | None = None, timeout: int = 30
) -> AsyncGenerator[str, None]:
    """
    Run `gh copilot suggest` and stream output line-by-line with heartbeats.
    Yields SSE strings. Raises on hard failure so caller can fall through.
    """
    cmd = ["gh", "copilot", "suggest", "-t", "shell", message]
    log.info("gh_copilot_launch", cmd=" ".join(cmd), cwd=str(cwd), timeout=timeout)
    t0 = time.monotonic()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,   # prevent hanging waiting for stdin
        env={**os.environ, "GH_TOKEN": GITHUB_PAT, "GH_NO_UPDATE_NOTIFIER": "1",
             "TERM": "dumb"},
        cwd=str(cwd) if cwd else None,
    )
    log.info("gh_copilot_started", pid=proc.pid)

    collected_stdout: list[str] = []
    collected_stderr: list[str] = []
    last_heartbeat = time.monotonic()

    async def _read_stdout() -> None:
        assert proc.stdout
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            decoded = line.decode(errors="replace").rstrip()
            log.info("gh_stdout", line=decoded, elapsed_s=round(time.monotonic() - t0, 2))
            collected_stdout.append(decoded)

    async def _read_stderr() -> None:
        assert proc.stderr
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            decoded = line.decode(errors="replace").rstrip()
            log.warning("gh_stderr", line=decoded, elapsed_s=round(time.monotonic() - t0, 2))
            collected_stderr.append(decoded)

    # Kick off readers
    reader_tasks = [
        asyncio.create_task(_read_stdout()),
        asyncio.create_task(_read_stderr()),
    ]

    # Wait for process to finish with periodic heartbeat yields
    deadline = t0 + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.error("gh_copilot_timeout", pid=proc.pid, timeout=timeout,
                      stdout_lines=len(collected_stdout), stderr_lines=len(collected_stderr))
            proc.kill()
            for t in reader_tasks:
                t.cancel()
            raise asyncio.TimeoutError(f"gh copilot timed out after {timeout}s")

        try:
            await asyncio.wait_for(proc.wait(), timeout=min(remaining, 5.0))
            break  # process finished
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            if time.monotonic() - last_heartbeat >= 5.0:
                log.info("gh_copilot_waiting", elapsed_s=round(elapsed, 2),
                         stdout_so_far=len(collected_stdout))
                last_heartbeat = time.monotonic()
                yield sse({"type": "status",
                           "content": f"Querying Copilot CLI… ({int(elapsed)}s elapsed)"})

    # Wait for readers to drain
    await asyncio.gather(*reader_tasks, return_exceptions=True)

    elapsed = time.monotonic() - t0
    log.info("gh_copilot_finished", pid=proc.pid, rc=proc.returncode,
             elapsed_s=round(elapsed, 2),
             stdout_lines=len(collected_stdout), stderr_lines=len(collected_stderr))

    if collected_stderr:
        log.warning("gh_copilot_stderr_summary", lines=collected_stderr[-10:])

    suggestion = "\n".join(collected_stdout).strip()
    if suggestion:
        yield sse({"type": "text", "content": suggestion})
    else:
        # Nothing came out - surface stderr as error message
        err = "\n".join(collected_stderr).strip() or "(no output)"
        log.warning("gh_copilot_empty_output", stderr=err)
        raise RuntimeError(f"gh copilot returned no output. stderr: {err}")


# ── Agent logic ───────────────────────────────────────────────────────────────

async def run_agent(  # noqa: C901
    message: str, history: list[dict]
) -> AsyncGenerator[str, None]:
    """
    Drive Copilot SDK / fallback to gh copilot CLI / or a built-in planner.
    Yields SSE data lines.
    """
    log.info("agent_start", message=message[:120], history_len=len(history),
             repo=REPO_FULL_NAME, workspace=str(WORKSPACE))

    # ── 1. Thinking ────────────────────────────────────────────────────────
    yield sse({"type": "thinking", "content": "Analysing request…"})
    await asyncio.sleep(0.05)

    # ── 2. Parse intent ────────────────────────────────────────────────────
    low = message.lower()
    if any(w in low for w in ("git status", "status")):
        log.info("agent_intent", intent="git_status")
        yield sse({"type": "tool_call", "content": "Running: git status", "tool_name": "shell"})
        out = await _shell("git status", cwd=WORKSPACE)
        yield sse({"type": "tool_result", "content": out, "tool_name": "shell"})
        yield sse({"type": "text", "content": f"```\n{out}\n```"})
        yield sse_done()
        return

    if any(w in low for w in ("git log", "log")):
        log.info("agent_intent", intent="git_log")
        yield sse({"type": "tool_call", "content": "Running: git log --oneline -10", "tool_name": "shell"})
        out = await _shell("git log --oneline -10", cwd=WORKSPACE)
        yield sse({"type": "tool_result", "content": out, "tool_name": "shell"})
        yield sse({"type": "text", "content": f"```\n{out}\n```"})
        yield sse_done()
        return

    # ── 3. Try gh copilot suggest (if available) ────────────────────────────
    yield sse({"type": "status", "content": "Querying Copilot CLI…"})
    try:
        log.info("agent_intent", intent="gh_copilot")
        async for chunk in _run_gh_copilot(message, cwd=WORKSPACE, timeout=30):
            yield chunk
        yield sse_done()
        return
    except FileNotFoundError:
        log.warning("gh_cli_not_found", detail="gh binary missing, falling through to fallback")
    except asyncio.TimeoutError as exc:
        log.error("gh_cli_timeout", detail=str(exc))
        yield sse({"type": "error",
                   "content": "Copilot CLI timed out after 30 s. Falling back to built-in planner."})
    except Exception as exc:
        log.error("gh_cli_error", detail=str(exc))
        yield sse({"type": "status",
                   "content": f"Copilot CLI unavailable ({exc}), using built-in planner."})

    # ── 4. Fallback: echo + run if looks like shell command ─────────────────
    match = re.search(r"(?:run|execute|do|try)\s+[`']?(.+?)[`']?\s*$", message, re.I)
    if match:
        cmd = match.group(1).strip("`'\"")
        log.info("agent_intent", intent="shell_fallback", cmd=cmd)
        yield sse({"type": "tool_call", "content": f"Running: {cmd}", "tool_name": "shell"})
        try:
            out = await asyncio.wait_for(_shell(cmd, cwd=WORKSPACE), timeout=20)
            yield sse({"type": "tool_result", "content": out, "tool_name": "shell"})
            yield sse({"type": "text", "content": f"Done.\n```\n{out}\n```"})
        except asyncio.TimeoutError:
            log.error("shell_timeout", cmd=cmd)
            yield sse({"type": "error", "content": "Command timed out."})
        yield sse_done()
        return

    # ── 5. Generic response ────────────────────────────────────────────────
    log.info("agent_intent", intent="generic_response")
    yield sse({"type": "text", "content": (
        f"I'm your Copilot agent for **{REPO_FULL_NAME}**.\n\n"
        "I can help you:\n"
        "- Run shell commands\n"
        "- Check git status/log\n"
        "- Create branches & PRs (say 'done with feature X')\n\n"
        f"You said: *{message}*"
    )})
    yield sse_done()


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
