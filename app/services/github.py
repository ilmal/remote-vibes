"""GitHub service – list repos, clone, create branches, open PRs."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import structlog
from github import Auth, Github, GithubException
from github.Repository import Repository

log = structlog.get_logger(__name__)


class GitHubService:
    def __init__(self, pat: str, timeout: int = 15) -> None:
        self._pat = pat
        self._gh = Github(auth=Auth.Token(pat), timeout=timeout)

    # ── Auth check ────────────────────────────────────────────────────────────

    def get_user_info(self) -> dict[str, Any]:
        u = self._gh.get_user()
        return {
            "login": u.login,
            "name": u.name,
            "avatar_url": u.avatar_url,
        }

    # ── Repository listing ────────────────────────────────────────────────────

    def list_repos(self, limit: int = 200) -> list[dict[str, Any]]:
        repos = []
        for repo in self._gh.get_user().get_repos(sort="updated"):
            repos.append(self._repo_dict(repo))
            if len(repos) >= limit:
                break
        return repos

    @staticmethod
    def _repo_dict(repo: Repository) -> dict[str, Any]:
        return {
            "full_name": repo.full_name,
            "name": repo.name,
            "description": repo.description or "",
            "private": repo.private,
            "default_branch": repo.default_branch,
            "language": repo.language or "",
            "stars": repo.stargazers_count,
            "updated_at": repo.updated_at.isoformat() if repo.updated_at else "",
            "clone_url": repo.clone_url,
            "html_url": repo.html_url,
        }

    # ── Cloning ───────────────────────────────────────────────────────────────

    async def clone_repo(
        self, full_name: str, target_dir: Path, branch: str = "main"
    ) -> Path:
        """Clone (or update) the repo into target_dir. Returns local path."""
        repo_dir = target_dir / full_name.split("/")[-1]
        auth_url = f"https://x-access-token:{self._pat}@github.com/{full_name}.git"

        if repo_dir.exists():
            log.info("repo_exists_pulling", repo=full_name)
            await _run(["git", "fetch", "--all"], cwd=repo_dir)
            await _run(["git", "checkout", branch], cwd=repo_dir)
            await _run(["git", "pull", "--ff-only"], cwd=repo_dir)
        else:
            log.info("cloning_repo", repo=full_name, target=str(repo_dir))
            repo_dir.mkdir(parents=True, exist_ok=True)
            await _run(
                ["git", "clone", "--depth", "1", "--branch", branch, auth_url, str(repo_dir)]
            )
        return repo_dir

    # ── Branch + PR ───────────────────────────────────────────────────────────

    async def create_branch_and_pr(
        self,
        full_name: str,
        repo_dir: Path,
        feature_name: str,
        pr_title: str,
        pr_body: str,
        base_branch: str = "main",
    ) -> dict[str, str]:
        import time

        branch_name = f"feature/{_slug(feature_name)}-{int(time.time())}"
        log.info("creating_branch", branch=branch_name, repo=full_name)

        # Create and push new branch
        await _run(["git", "checkout", "-b", branch_name], cwd=repo_dir)
        await _run(["git", "add", "--all"], cwd=repo_dir)

        try:
            await _run(
                ["git", "commit", "-m", f"feat: {feature_name}\n\n{pr_body}"],
                cwd=repo_dir,
            )
        except RuntimeError as exc:
            if "nothing to commit" in str(exc).lower():
                log.warning("nothing_to_commit", branch=branch_name)
            else:
                raise

        auth_url = f"https://x-access-token:{self._pat}@github.com/{full_name}.git"
        await _run(["git", "push", auth_url, branch_name], cwd=repo_dir)

        # Open PR via PyGithub (runs in thread pool to avoid blocking)
        repo = await asyncio.get_running_loop().run_in_executor(
            None, self._gh.get_repo, full_name
        )
        pr = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: repo.create_pull(
                title=pr_title,
                body=pr_body,
                head=branch_name,
                base=base_branch,
            ),
        )
        log.info("pr_created", pr_number=pr.number, url=pr.html_url)
        return {"pr_url": pr.html_url, "pr_number": pr.number, "branch": branch_name}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _run(cmd: list[str], cwd: Path | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command {cmd} failed (rc={proc.returncode}): {stderr.decode()}"
        )
    return stdout.decode()


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]
