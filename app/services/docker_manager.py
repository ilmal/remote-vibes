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
        dev_server_port = _find_free_port(start=agent_api_port + 1)

        container_name = f"rv-agent-{session_id[:8]}"
        network_name = f"rv-net-{session_id[:8]}"

        # Create isolated network
        try:
            self._client.networks.create(
                network_name,
                driver="bridge",
                options={"com.docker.network.driver.mtu": "1260"},
                labels={"rv.session_id": session_id},
            )
        except docker.errors.APIError as e:
            if "already exists" not in str(e):
                raise

        # repos_dir only needed locally for mkdir; the agent gets the named volume.
        Path(settings.repos_dir).mkdir(parents=True, exist_ok=True)

        env = {
            "GITHUB_PAT": github_pat,
            "REPO_FULL_NAME": repo_full_name,
            "REPO_NAME": repo_name,
            "SESSION_ID": session_id,
            "BRANCH": branch,
            "DEV_SERVER_PORT": "5000",  # internal port – mapped to dev_server_port on host
            "REPOS_VOLUME_NAME": settings.repos_volume,  # needed by entrypoint to resolve bind-mount paths
        }
        if cloudflare_token:
            env["CLOUDFLARE_TUNNEL_TOKEN"] = cloudflare_token

        container = self._client.containers.run(
            image=settings.agent_image,
            name=container_name,
            detach=True,
            remove=False,
            user="root",
            environment=env,
            volumes={
                settings.repos_volume: {
                    "bind": "/workspace",
                    "mode": "rw",
                },
                "/var/run/docker.sock": {
                    "bind": "/var/run/docker.sock",
                    "mode": "rw",
                },
            },
            ports={
                "8080/tcp": code_server_port,
                "3000/tcp": agent_api_port,
                "5000/tcp": dev_server_port,
            },
            network=network_name,
            labels={
                "rv.session_id": session_id,
                "rv.repo": repo_full_name,
                "rv.managed": "true",
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

        # Connect the agent to rv_main so rv_main FastAPI can reach it by
        # container name without hairpin-NAT issues (host-published ports can't
        # be reached from within other containers via host gateway on Linux).
        try:
            main_net = self._client.networks.get(settings.docker_main_network)
            main_net.connect(container.id, aliases=[container_name])
            log.info("agent_joined_main_net", container=container_name)
        except Exception as exc:
            log.warning("agent_main_net_join_failed", error=str(exc))

        return {
            "container_id": container.id,
            "container_name": container_name,
            "network_name": network_name,
            "code_server_port": code_server_port,
            "agent_api_port": agent_api_port,
            "dev_server_port": dev_server_port,
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
            session_id = labels.get("rv.session_id", "")

            c.stop(timeout=10)
            c.remove(force=True)
            log.info("container_stopped", container_id=container_id[:12])

            # Clean up network
            if session_id:
                network_name = f"rv-net-{session_id[:8]}"
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

    def get_container_logs(self, container_id: str, tail: int = 300) -> str:
        """Return recent logs from a container as a plain string."""
        try:
            c = self._client.containers.get(container_id)
            raw = c.logs(tail=tail, timestamps=True, stream=False)
            return raw.decode("utf-8", errors="replace")
        except docker.errors.NotFound:
            return "(container not found)"
        except Exception as exc:
            return f"(error fetching logs: {exc})"

    def get_named_container_logs(self, name: str, tail: int = 300) -> str:
        """Return logs for a container by name or short id."""
        try:
            c = self._client.containers.get(name)
            raw = c.logs(tail=tail, timestamps=True, stream=False)
            return raw.decode("utf-8", errors="replace")
        except docker.errors.NotFound:
            return f"(container '{name}' not found)"
        except Exception as exc:
            return f"(error fetching logs: {exc})"

    def get_compose_containers_for_session(self, session_id: str) -> list[dict]:
        """Find docker-compose containers joined by the agent for this session.

        The agent container connects itself to the compose project's default
        network during startup.  We find all non-rv networks the agent is a
        member of and list the other containers on those networks.
        """
        container_name = f"rv-agent-{session_id[:8]}"
        try:
            agent = self._client.containers.get(container_name)
            agent.reload()
        except docker.errors.NotFound:
            return []

        networks = agent.attrs.get("NetworkSettings", {}).get("Networks", {})
        result: list[dict] = []
        seen_ids: set[str] = set()

        # Skip internal rv networks
        skip_prefixes = ("rv-net-", "rv_", "bridge", "host", "none")

        for net_name, net_meta in networks.items():
            if any(net_name.startswith(p) or net_name == p for p in skip_prefixes):
                continue
            net_id = net_meta.get("NetworkID", "")
            if not net_id:
                continue
            try:
                net_obj = self._client.networks.get(net_id)
                net_obj.reload()
            except Exception:
                continue

            for cid, cinfo in (net_obj.attrs.get("Containers") or {}).items():
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                cname = cinfo.get("Name", "")
                if cname == container_name:
                    continue  # skip the agent itself
                try:
                    c = self._client.containers.get(cid)
                    result.append({
                        "id": c.id[:12],
                        "name": c.name,
                        "service": c.labels.get("com.docker.compose.service", c.name),
                        "status": c.status,
                        "compose_project": c.labels.get("com.docker.compose.project", ""),
                        "network": net_name,
                    })
                except docker.errors.NotFound:
                    pass

        result.sort(key=lambda x: x["service"])
        return result

    def list_managed_containers(self) -> list[dict]:
        containers = self._client.containers.list(
            filters={"label": "rv.managed=true"}
        )
        return [
            {
                "id": c.id[:12],
                "name": c.name,
                "status": c.status,
                "session_id": c.labels.get("rv.session_id"),
                "repo": c.labels.get("rv.repo"),
            }
            for c in containers
        ]

    def cleanup_stale_containers(self) -> int:
        """Remove all stopped/exited managed containers."""
        removed = 0
        for c in self._client.containers.list(
            all=True,
            filters={"label": "rv.managed=true", "status": "exited"},
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
