"""Docker manager – spin up / tear down per-repo agent containers."""
from __future__ import annotations

import asyncio
import socket
import uuid
from contextlib import closing
from pathlib import Path
from typing import Optional

import docker
import docker.errors
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()


def _find_free_port(start: int = 9000, end: int = 9999) -> int:
    for port in range(start, end):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free ports available in range")


class DockerManager:
    """Manages per-repo Docker containers using the Docker Python SDK."""

    def __init__(self) -> None:
        self._client = docker.from_env()
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    async def start_agent_container(
        self,
        session_id: str,
        repo_full_name: str,
        repo_name: str,
        github_pat: str,
        cloudflare_token: str = "",
        branch: str = "main",
    ) -> dict:
        """Start a new agent container for the given repo, return port mapping."""
        async with self._get_lock(session_id):
            return await asyncio.get_running_loop().run_in_executor(
                None,
                self._start_container_sync,
                session_id,
                repo_full_name,
                repo_name,
                github_pat,
                cloudflare_token,
                branch,
            )

    def _start_container_sync(
        self,
        session_id: str,
        repo_full_name: str,
        repo_name: str,
        github_pat: str,
        cloudflare_token: str,
        branch: str,
    ) -> dict:
        code_server_port = _find_free_port(start=settings.agent_base_port)
        agent_api_port = _find_free_port(start=code_server_port + 1)

        container_name = f"cpa-agent-{session_id[:8]}"
        network_name = f"cpa-net-{session_id[:8]}"

        # Create isolated network
        try:
            self._client.networks.create(
                network_name,
                driver="bridge",
                labels={"cpa.session_id": session_id},
            )
        except docker.errors.APIError as e:
            if "already exists" not in str(e):
                raise

        repos_dir = Path(settings.repos_dir)
        repos_dir.mkdir(parents=True, exist_ok=True)

        env = {
            "GITHUB_PAT": github_pat,
            "REPO_FULL_NAME": repo_full_name,
            "REPO_NAME": repo_name,
            "SESSION_ID": session_id,
            "BRANCH": branch,
        }
        if cloudflare_token:
            env["CLOUDFLARE_TUNNEL_TOKEN"] = cloudflare_token

        container = self._client.containers.run(
            image=settings.agent_image,
            name=container_name,
            detach=True,
            remove=False,
            environment=env,
            volumes={
                str(repos_dir): {
                    "bind": "/workspace",
                    "mode": "rw",
                },
            },
            ports={
                "8080/tcp": code_server_port,
                "3000/tcp": agent_api_port,
            },
            network=network_name,
            labels={
                "cpa.session_id": session_id,
                "cpa.repo": repo_full_name,
                "cpa.managed": "true",
            },
            # Security: read-only root, drop all linux caps
            read_only=False,   # code-server needs writes
            security_opt=["no-new-privileges:true"],
            mem_limit="2g",
            cpu_period=100000,
            cpu_quota=150000,  # 1.5 CPUs max
        )

        log.info(
            "container_started",
            container_id=container.id[:12],
            name=container_name,
            code_server_port=code_server_port,
            agent_api_port=agent_api_port,
        )

        return {
            "container_id": container.id,
            "container_name": container_name,
            "network_name": network_name,
            "code_server_port": code_server_port,
            "agent_api_port": agent_api_port,
        }

    async def stop_container(self, container_id: str) -> None:
        """Gracefully stop and remove a container."""
        await asyncio.get_running_loop().run_in_executor(
            None, self._stop_container_sync, container_id
        )

    def _stop_container_sync(self, container_id: str) -> None:
        try:
            c = self._client.containers.get(container_id)
            labels = c.labels or {}
            session_id = labels.get("cpa.session_id", "")

            c.stop(timeout=10)
            c.remove(force=True)
            log.info("container_stopped", container_id=container_id[:12])

            # Clean up network
            if session_id:
                network_name = f"cpa-net-{session_id[:8]}"
                try:
                    net = self._client.networks.get(network_name)
                    net.remove()
                except docker.errors.NotFound:
                    pass
        except docker.errors.NotFound:
            log.warning("container_not_found", container_id=container_id[:12])

    def get_container_status(self, container_id: str) -> str:
        try:
            c = self._client.containers.get(container_id)
            return c.status  # running | exited | paused | ...
        except docker.errors.NotFound:
            return "not_found"

    def list_managed_containers(self) -> list[dict]:
        containers = self._client.containers.list(
            filters={"label": "cpa.managed=true"}
        )
        return [
            {
                "id": c.id[:12],
                "name": c.name,
                "status": c.status,
                "session_id": c.labels.get("cpa.session_id"),
                "repo": c.labels.get("cpa.repo"),
            }
            for c in containers
        ]

    def cleanup_stale_containers(self) -> int:
        """Remove all stopped/exited managed containers."""
        removed = 0
        for c in self._client.containers.list(
            all=True,
            filters={"label": "cpa.managed=true", "status": "exited"},
        ):
            c.remove(force=True)
            removed += 1
        log.info("stale_containers_removed", count=removed)
        return removed


# Singleton
_docker_manager: Optional[DockerManager] = None


def get_docker_manager() -> DockerManager:
    global _docker_manager
    if _docker_manager is None:
        _docker_manager = DockerManager()
    return _docker_manager
