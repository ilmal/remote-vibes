#!/bin/bash
set -euo pipefail

# ── Validate required vars ───────────────────────────────────────────────────
: "${GITHUB_PAT:?GITHUB_PAT is required}"
: "${REPO_NAME:?REPO_NAME is required}"
: "${SESSION_ID:?SESSION_ID is required}"

# /workspace is the shared repos volume; create repo-specific subdir inside it
WORKSPACE_DIR="/workspace/${REPO_NAME}"
mkdir -p "${WORKSPACE_DIR}"

echo "[entrypoint] Configuring git identity..."
git config --global user.email "agent@remote-vibes.local"
git config --global user.name "Copilot Agent"
git config --global credential.helper store
echo "https://x-access-token:${GITHUB_PAT}@github.com" > ~/.git-credentials
chmod 600 ~/.git-credentials

# ── Authenticate gh CLI ──────────────────────────────────────────────────────
echo "${GITHUB_PAT}" | gh auth login --with-token 2>/dev/null || true
echo "[entrypoint] gh CLI authenticated"

# ── Clone repo if not already present (or if prior clone failed and left empty repo) ─────
if [ ! -d "${WORKSPACE_DIR}/.git" ] || ! git -C "${WORKSPACE_DIR}" rev-parse HEAD &>/dev/null; then
    if [ -d "${WORKSPACE_DIR}/.git" ]; then
        echo "[entrypoint] Stale/empty repo detected – removing and re-cloning..."
        rm -rf "${WORKSPACE_DIR}"
        mkdir -p "${WORKSPACE_DIR}"
    fi
    echo "[entrypoint] Cloning ${REPO_FULL_NAME:-unknown}..."
    git clone "https://x-access-token:${GITHUB_PAT}@github.com/${REPO_FULL_NAME}" "${WORKSPACE_DIR}" || {
        echo "[entrypoint] Clone failed, initialising empty repo"
        git init "${WORKSPACE_DIR}"
    }
fi

# ── code-server config ───────────────────────────────────────────────────────
CODE_SERVER_CONFIG="${HOME}/.config/code-server/config.yaml"
mkdir -p "$(dirname "${CODE_SERVER_CONFIG}")"
cat > "${CODE_SERVER_CONFIG}" <<EOF
bind-addr: 0.0.0.0:8080
auth: none
cert: false
github-auth: ${GITHUB_PAT:-}
EOF

# ── Install Copilot VSIX if provided ─────────────────────────────────────────
if [ -n "${COPILOT_VSIX_PATH:-}" ] && [ -f "${COPILOT_VSIX_PATH}" ]; then
    echo "[entrypoint] Installing Copilot VSIX..."
    code-server --install-extension "${COPILOT_VSIX_PATH}" --force 2>/dev/null || true
fi

# Attempt marketplace install (may fail without auth – ok)
code-server --install-extension GitHub.copilot --force 2>/dev/null || true
code-server --install-extension GitHub.copilot-chat --force 2>/dev/null || true

echo "[entrypoint] Starting code-server on :8080"
code-server --config "${CODE_SERVER_CONFIG}" "${WORKSPACE_DIR}" &
CODE_SERVER_PID=$!

echo "[entrypoint] Starting agent API on :3000"
/opt/agent-venv/bin/uvicorn agent_runner:app \
    --host 0.0.0.0 \
    --port 3000 \
    --log-level info \
    --app-dir /app &
AGENT_PID=$!

# ── optional cloudflared ──────────────────────────────────────────────────────
if [ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]; then
    echo "[entrypoint] Starting cloudflared tunnel..."
    cloudflared tunnel --no-autoupdate run --token "${CLOUDFLARE_TUNNEL_TOKEN}" &
fi

# ── Auto-detect and start dev server ─────────────────────────────────────────
DEV_PORT="${DEV_SERVER_PORT:-5000}"

_port_open() {
    timeout 2 bash -c "echo >/dev/tcp/localhost/${1}" 2>/dev/null
}

_fallback_server() {
    local port="${1}"
    local dir="${2}"
    echo "[dev] ⚠ Starting fallback static server on :${port} (serving ${dir})"
    cd "${dir}"
    python3 -m http.server "${port}" --bind 0.0.0.0
}

_run_compose_dev() {
    local dir="${1}"
    local port="${2}"
    cd "${dir}"

    echo "[dev] docker-compose.yml found → preparing compose dev server"

    # ── Step 1: generate override ──────────────────────────────────────────
    # Translate relative bind-mount sources to absolute host paths
    # (docker socket uses HOST daemon, so all paths must be host-absolute)
    # Also convert network_mode:host services to bridge + fix 127.0.0.1 URLs
    cat > /tmp/rv-compose-patch.py << 'PATCHEOF'
import sys, re

repo_host_path = sys.argv[1]  # absolute path on HOST to the repo directory

try:
    import yaml
except ImportError:
    import subprocess
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'pyyaml'],
                   capture_output=True)
    import yaml

with open('docker-compose.yml') as f:
    c = yaml.safe_load(f)

override = {'services': {}}
host_mode_svcs = []

for svc_name, svc in (c.get('services') or {}).items():
    s = {}

    # Translate ./relative bind mounts → absolute host paths
    vols = svc.get('volumes') or []
    new_vols, changed = [], False
    for v in vols:
        if isinstance(v, str) and v.startswith('./'):
            parts = v.split(':')
            host_src = repo_host_path + parts[0][1:]  # strip leading '.'
            new_vols.append(':'.join([host_src] + parts[1:]))
            changed = True
        else:
            new_vols.append(v)
    if changed:
        s['volumes'] = new_vols

    # For network_mode:host services: we cannot override host mode via the
    # compose override file (Compose v2 treats null as "keep base value").
    # Instead, we patch docker-compose.yml directly, removing host mode so
    # those services join the project's default bridge network naturally.
    # We write the patched compose back in place (override still handles volumes/env).
    if svc.get('network_mode') == 'host':
        host_mode_svcs.append(svc_name)
        # Fix 127.0.0.1 references in env so sibling services resolve by DNS
        env = dict(svc.get('environment') or {})
        changed_env = False
        for k, v in list(env.items()):
            if isinstance(v, str) and '127.0.0.1' in v:
                v = re.sub(r'@127\.0\.0\.1:5432', '@postgres:5432', v)
                v = re.sub(r'redis://127\.0\.0\.1:', 'redis://redis:', v)
                env[k] = v
                changed_env = True
        if changed_env:
            s['environment'] = env

    if s:
        override['services'][svc_name] = s

# Remove network_mode:host from the original compose file so those services
# join the project network. Backup the original first.
import shutil
if host_mode_svcs:
    shutil.copy('docker-compose.yml', 'docker-compose.yml.rv-bak')
    for svc_name in host_mode_svcs:
        del c['services'][svc_name]['network_mode']
    with open('docker-compose.yml', 'w') as fw:
        yaml.dump(c, fw, default_flow_style=False)
    print(f'[dev] Patched docker-compose.yml: removed network_mode:host from {host_mode_svcs}')

with open('docker-compose.override.yml', 'w') as f:
    yaml.dump(override, f, default_flow_style=False)

# Write services that need force-recreation (were network_mode:host)
with open('/tmp/rv-recreate-svcs.txt', 'w') as f:
    f.write(' '.join(host_mode_svcs))

print('[dev] docker-compose.override.yml written')
PATCHEOF

    # Get the volume mountpoint so bind-mounts resolve on the HOST
    local repos_vol="${REPOS_VOLUME_NAME:-remote-vibes_repos_data}"
    local vol_root
    vol_root=$(docker volume inspect "${repos_vol}" --format '{{.Mountpoint}}' 2>/dev/null || true)

    if [ -z "${vol_root}" ]; then
        echo "[dev] ⚠ Cannot resolve repos volume '${repos_vol}', falling back to static server"
        _fallback_server "${port}" "${dir}"
        return
    fi

    local repo_host_path="${vol_root}/${REPO_NAME}"
    echo "[dev] Repo host path: ${repo_host_path}"

    # Copy .env.example → .env if missing (common in docker-compose projects)
    if [ ! -f "${dir}/.env" ] && [ -f "${dir}/.env.example" ]; then
        echo "[dev] .env not found — copying .env.example → .env"
        cp "${dir}/.env.example" "${dir}/.env"
    fi

    python3 /tmp/rv-compose-patch.py "${repo_host_path}" || {
        echo "[dev] ⚠ Patch failed, falling back to static server"
        _fallback_server "${port}" "${dir}"
        return
    }

    # ── Step 2: start stack (use cached images; build only if missing) ────────
    echo "[dev] Starting compose stack…"
    # --no-build: use existing images; if an image is missing, fall back to build with timeout
    docker compose up -d --no-build 2>&1 | tail -20 || {
        echo "[dev] Images missing, building with 5-min timeout…"
        timeout 300 docker compose build 2>&1 | tail -10 || true
        docker compose up -d --no-build 2>&1 | tail -20 || true
    }

    # Force-recreate the formerly host-network services: patching docker-compose.yml
    # removes their host mode, so Compose will recreate them on the project network.
    # This step is a safety net in case they were already running from a prior boot.
    local host_svcs
    host_svcs=$(cat /tmp/rv-recreate-svcs.txt 2>/dev/null || echo "")
    if [ -n "${host_svcs}" ]; then
        echo "[dev] Force-recreating ex-host-network services: ${host_svcs}"
        # shellcheck disable=SC2086
        docker compose up -d --no-build --force-recreate ${host_svcs} 2>&1 | tail -10 || true
    fi

    # ── Step 3: join compose network for direct container-to-container routing
    sleep 8
    local proj
    proj=$(basename "${dir}")
    local compose_net="${proj}_default"
    echo "[dev] Joining ${compose_net} network…"
    docker network connect "${compose_net}" "${HOSTNAME}" 2>/dev/null \
        && echo "[dev] Joined ${compose_net}" \
        || echo "[dev] Already connected or failed (continuing)"
    sleep 2

    # ── Step 4: probe compose services by DNS name within the network ───────
    echo "[dev] Probing compose services for web UI…"
    local ui_target
    ui_target=$(docker compose ps --format '{{json .}}' 2>/dev/null | python3 -c "
import sys, json, socket

SKIP_SVC  = {'postgres','redis','mysql','mongodb','rabbitmq',
             'worker','scheduler','celery','cron',
             'dummy_site','db'}
WEB_PORTS = [5173, 3000, 8080, 8000, 4200, 4000, 80]

records = []
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        svc  = json.loads(line)
        name = svc.get('Service', '')
        cname = svc.get('Name', '')
        if name and name.lower() not in SKIP_SVC:
            records.append((name, cname))
    except Exception:
        pass

for svc_name, container_name in records:
    for probe_port in WEB_PORTS:
        for host in [svc_name, container_name]:
            try:
                s = socket.create_connection((host, probe_port), timeout=2)
                s.close()
                print(f'{host}:{probe_port}')
                sys.exit(0)
            except Exception:
                pass
print('fallback:0')
" 2>/dev/null || echo "fallback:0")

    local ui_host="${ui_target%%:*}"
    local ui_port_num="${ui_target##*:}"

    if [ "${ui_port_num}" = "0" ]; then
        echo "[dev] No web UI found in compose network; falling back to static server"
        _fallback_server "${port}" "${dir}"
        return
    fi

    echo "[dev] Found web UI at ${ui_target}"

    # ── Step 5: TCP proxy DEV_PORT → compose service (DNS name) ────────────
    cat > /tmp/rv-proxy.py << 'PROXYEOF'
import sys, socket, threading

ui_host = sys.argv[1].strip()
ui_port = int(sys.argv[2].strip())
listen  = int(sys.argv[3].strip())

def pipe(src, dst):
    try:
        while chunk := src.recv(16384):
            dst.sendall(chunk)
    except Exception:
        pass
    for s in (src, dst):
        try: s.shutdown(socket.SHUT_RDWR)
        except Exception: pass
        try: s.close()
        except Exception: pass

def handle(client):
    try:
        server = socket.create_connection((ui_host, ui_port), timeout=30)
        threading.Thread(target=pipe, args=(client, server), daemon=True).start()
        threading.Thread(target=pipe, args=(server, client), daemon=True).start()
    except Exception as e:
        print(f'[proxy] connect {ui_host}:{ui_port} failed: {e}', flush=True)
        client.close()

srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(('0.0.0.0', listen))
srv.listen(128)
print(f'[dev] Compose proxy :{listen} → {ui_host}:{ui_port} ready', flush=True)
while True:
    c, _ = srv.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
PROXYEOF
    exec python3 /tmp/rv-proxy.py "${ui_host}" "${ui_port_num}" "${port}"
}

_try_flask() {
    local port="${1}"
    local dir="${2}"
    # Try common Flask entry patterns (factory or plain app)
    for entry in "central_app.admin_api:create_app()" \
                 "app:create_app()" "application:create_app()" \
                 "central_app.admin_api:app" "central_app:app" \
                 "app:app" "application:app" "wsgi:app" "main:app"; do
        echo "[dev] Trying FLASK_APP=${entry}..."
        FLASK_APP="${entry}" python3 -m flask run --host 0.0.0.0 --port "${port}" 2>&1 &
        local fpid=$!
        sleep 5
        if _port_open "${port}"; then
            echo "[dev] Flask started with FLASK_APP=${entry} (pid ${fpid})"
            wait "${fpid}"
            return 0
        fi
        kill "${fpid}" 2>/dev/null; wait "${fpid}" 2>/dev/null
    done
    return 1
}

_start_dev_server() {
    local dir="${WORKSPACE_DIR}"
    local port="${DEV_PORT}"
    # Give clone a moment to finish, allow code-server to start first
    sleep 5

    echo "[dev] Detecting project type in ${dir}..."

    # Docker Compose project takes highest priority
    if [ -f "${dir}/docker-compose.yml" ] || [ -f "${dir}/docker-compose.yaml" ]; then
        _run_compose_dev "${dir}" "${port}"
        return
    fi

    if [ -f "${dir}/package.json" ]; then
        cd "${dir}"

        # Pick package manager
        if [ -f "pnpm-lock.yaml" ]; then
            PM="pnpm"
        elif [ -f "yarn.lock" ]; then
            PM="yarn"
        else
            PM="npm"
        fi

        echo "[dev] Installing JS dependencies with ${PM}..."
        timeout 180 ${PM} install --legacy-peer-deps 2>&1 | tail -5 || true

        # Detect framework
        if [ -f "next.config.js" ] || [ -f "next.config.ts" ] || [ -f "next.config.mjs" ]; then
            echo "[dev] Next.js detected → PORT=${port} ${PM} run dev"
            PORT=${port} ${PM} run dev

        elif [ -f "vite.config.js" ] || [ -f "vite.config.ts" ] || [ -f "vite.config.mjs" ]; then
            echo "[dev] Vite detected → ${PM} run dev --port ${port} --host"
            ${PM} run dev -- --port ${port} --host 0.0.0.0

        elif jq -e '.scripts.dev' package.json > /dev/null 2>&1; then
            echo "[dev] npm dev script → PORT=${port} ${PM} run dev"
            PORT=${port} ${PM} run dev

        elif jq -e '.scripts.start' package.json > /dev/null 2>&1; then
            echo "[dev] npm start script → PORT=${port} ${PM} start"
            PORT=${port} ${PM} start

        else
            echo "[dev] No recognized npm start script; falling back to static server"
            _fallback_server "${port}" "${dir}"
        fi

    elif [ -f "${dir}/manage.py" ]; then
        cd "${dir}"
        echo "[dev] Django detected → installing deps + runserver :${port}"
        [ -f requirements.txt ] && timeout 120 pip install -q -r requirements.txt 2>&1 | tail -3 || true
        python manage.py runserver "0.0.0.0:${port}"

    elif [ -f "${dir}/pyproject.toml" ] || [ -f "${dir}/requirements.txt" ]; then
        cd "${dir}"
        REQ="${dir}/requirements.txt"
        if [ -f "${dir}/pyproject.toml" ]; then
            echo "[dev] Python project (pyproject.toml) → pip install -e . (timeout 120s)"
            timeout 120 pip install -q -e . 2>&1 | tail -3 || true
        else
            echo "[dev] Python project → pip install -r requirements.txt (timeout 120s)"
            timeout 120 pip install -q -r "${REQ}" 2>&1 | tail -5 || true
        fi

        # Detect ASGI/WSGI framework
        if grep -rqi "fastapi\|uvicorn" "${REQ}" "${dir}/pyproject.toml" 2>/dev/null; then
            echo "[dev] FastAPI/uvicorn detected → uvicorn on :${port}"
            uvicorn main:app --host 0.0.0.0 --port ${port} --reload 2>/dev/null || \
            uvicorn app.main:app --host 0.0.0.0 --port ${port} --reload 2>/dev/null || \
            uvicorn app:app --host 0.0.0.0 --port ${port} --reload 2>/dev/null || \
            _fallback_server "${port}" "${dir}"

        elif grep -rqi "flask" "${REQ}" "${dir}/pyproject.toml" 2>/dev/null; then
            echo "[dev] Flask detected → scanning entry points"
            _try_flask "${port}" "${dir}" || _fallback_server "${port}" "${dir}"

        else
            echo "[dev] Python project – no web framework detected; using static server"
            _fallback_server "${port}" "${dir}"
        fi

    else
        echo "[dev] No package.json or requirements.txt; using static server"
        _fallback_server "${port}" "${dir}"
    fi
}

_start_dev_server &
DEV_PID=$!

echo "[entrypoint] All services started. SESSION_ID=${SESSION_ID}"

# Wait for either process to exit
wait -n "${CODE_SERVER_PID}" "${AGENT_PID}" || true
echo "[entrypoint] A service exited, shutting down..."
kill "${CODE_SERVER_PID}" "${AGENT_PID}" "${DEV_PID}" 2>/dev/null || true
