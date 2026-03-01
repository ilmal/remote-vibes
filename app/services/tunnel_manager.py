"""Cloudflare Named-Tunnel manager.

Keeps cloudflared's config.yaml in sync with active agent sessions so each
repo gets automatic subdomains:

    {repo_slug}.{CF_TUNNEL_DOMAIN}        → dev server / app
    edit.{repo_slug}.{CF_TUNNEL_DOMAIN}   → code-server
    app.{repo_slug}.{CF_TUNNEL_DOMAIN}    → same as dev server (alias)

Prerequisites (one-time manual setup):
  1. `cloudflared tunnel create remote-vibes`  → saves creds.json + tunnel UUID
  2. Add DNS records in Cloudflare (wildcards route everything through one tunnel):
       *.{CF_TUNNEL_DOMAIN}  CNAME  {TUNNEL_UUID}.cfargotunnel.com  (proxied)
       {CF_TUNNEL_DOMAIN}    CNAME  {TUNNEL_UUID}.cfargotunnel.com  (proxied)
  3. Set CF_TUNNEL_ID, CF_TUNNEL_DOMAIN in .env
  4. Place the credentials JSON at ./cloudflared/creds.json

The main-app container writes ./cloudflared/config.yaml (mounted from host).
The cloudflared container reads /etc/cloudflared/config.yaml (same host path).
TunnelManager calls `docker restart rv_cloudflared` after each config change.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Optional

import docker
import docker.errors
import structlog
import yaml

log = structlog.get_logger(__name__)

# ── paths (inside main-app container) ─────────────────────────────────────────
_CF_DIR = Path("/app/cloudflared")
_CONFIG_PATH = _CF_DIR / "config.yaml"
_SESSIONS_PATH = _CF_DIR / "sessions.json"
_CLOUDFLARED_CONTAINER = "rv_cloudflared"


def _repo_slug(repo_name: str) -> str:
    """Convert a repo name to a safe DNS label."""
    slug = re.sub(r"[^a-z0-9]+", "-", repo_name.lower()).strip("-")
    return slug or "repo"


class TunnelManager:
    """Manages cloudflared ingress config for dynamic per-session subdomains."""

    def __init__(self, tunnel_id: str, tunnel_domain: str, main_app_service: str = "http://rv_main:8000") -> None:
        self._tunnel_id = tunnel_id
        self._domain = tunnel_domain.rstrip("/")
        self._main_service = main_app_service
        self._lock = threading.Lock()
        # session_id → {repo_name, code_server_port, dev_server_port}
        self._sessions: dict[str, dict] = {}
        self._load_sessions()

    # ── public API ─────────────────────────────────────────────────────────────

    def register_session(
        self,
        session_id: str,
        repo_name: str,
        code_server_port: int,
        dev_server_port: int,
    ) -> dict[str, str]:
        """Add ingress rules for a session and return the tunnel URLs."""
        with self._lock:
            self._sessions[session_id] = {
                "repo_name": repo_name,
                "code_server_port": code_server_port,
                "dev_server_port": dev_server_port,
            }
            self._save_sessions()
            self._write_config()
        self._restart_cloudflared()
        return self.get_tunnel_urls(repo_name)

    def unregister_session(self, session_id: str) -> None:
        """Remove ingress rules for a stopped session."""
        with self._lock:
            self._sessions.pop(session_id, None)
            self._save_sessions()
            self._write_config()
        self._restart_cloudflared()

    def get_tunnel_urls(self, repo_name: str) -> dict[str, str]:
        slug = _repo_slug(repo_name)
        return {
            "app_url":    f"https://{slug}.{self._domain}",
            "editor_url": f"https://edit.{slug}.{self._domain}",
        }

    # ── internals ──────────────────────────────────────────────────────────────

    def _load_sessions(self) -> None:
        try:
            if _SESSIONS_PATH.exists():
                self._sessions = json.loads(_SESSIONS_PATH.read_text())
                log.info("tunnel_sessions_loaded", count=len(self._sessions))
        except Exception as exc:
            log.warning("tunnel_sessions_load_failed", error=str(exc))

    def _save_sessions(self) -> None:
        try:
            _CF_DIR.mkdir(parents=True, exist_ok=True)
            _SESSIONS_PATH.write_text(json.dumps(self._sessions, indent=2))
        except Exception as exc:
            log.warning("tunnel_sessions_save_failed", error=str(exc))

    def _write_config(self) -> None:
        """Regenerate config.yaml from current state."""
        ingress: list[dict] = []

        # Main app always first
        ingress.append({"hostname": self._domain, "service": self._main_service})

        # Per-session entries
        for info in self._sessions.values():
            repo_name = info["repo_name"]
            slug = _repo_slug(repo_name)
            cs_port = info["code_server_port"]
            dev_port = info["dev_server_port"]

            # code-server  →  edit.{slug}.{domain}
            ingress.append({
                "hostname": f"edit.{slug}.{self._domain}",
                "service":  f"http://host.docker.internal:{cs_port}",
            })
            # dev/app server → {slug}.{domain} AND app.{slug}.{domain}
            ingress.append({
                "hostname": f"{slug}.{self._domain}",
                "service":  f"http://host.docker.internal:{dev_port}",
            })
            ingress.append({
                "hostname": f"app.{slug}.{self._domain}",
                "service":  f"http://host.docker.internal:{dev_port}",
            })

        # Catch-all must be last
        ingress.append({"service": "http_status:404"})

        config = {
            "tunnel": self._tunnel_id,
            "credentials-file": "/etc/cloudflared/creds.json",
            "ingress": ingress,
        }

        try:
            _CF_DIR.mkdir(parents=True, exist_ok=True)
            _CONFIG_PATH.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True))
            log.info("cloudflared_config_written", rules=len(ingress))
        except Exception as exc:
            log.error("cloudflared_config_write_failed", error=str(exc))
            raise

    def _restart_cloudflared(self) -> None:
        """Tell Docker to restart the cloudflared container so it picks up the new config."""
        try:
            client = docker.from_env()
            container = client.containers.get(_CLOUDFLARED_CONTAINER)
            container.restart(timeout=8)
            log.info("cloudflared_restarted")
        except docker.errors.NotFound:
            log.warning("cloudflared_container_not_found", name=_CLOUDFLARED_CONTAINER)
        except Exception as exc:
            log.warning("cloudflared_restart_failed", error=str(exc))


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Optional[TunnelManager] = None


def get_tunnel_manager() -> Optional[TunnelManager]:
    """Return the TunnelManager if CF_TUNNEL_ID and CF_TUNNEL_DOMAIN are configured."""
    global _instance
    if _instance is None:
        from app.config import get_settings
        s = get_settings()
        if s.cf_tunnel_id and s.cf_tunnel_domain:
            _instance = TunnelManager(
                tunnel_id=s.cf_tunnel_id,
                tunnel_domain=s.cf_tunnel_domain,
            )
            log.info("tunnel_manager_initialized", domain=s.cf_tunnel_domain)
        else:
            log.debug("tunnel_manager_disabled", reason="CF_TUNNEL_ID or CF_TUNNEL_DOMAIN not set")
    return _instance
