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

_try_flask() {
    local port="${1}"
    local dir="${2}"
    # Try common Flask entry patterns (factory or plain app)
    for entry in "central_app.admin_api:create_app()" \
                 "app:create_app()" "application:create_app()" \
                 "central_app.admin_api:app" "central_app:app" \
                 "app:app" "application:app" "wsgi:app" "main:app"; do
        echo "[dev] Trying FLASK_APP=${entry}..."
        FLASK_APP="${entry}" flask run --host 0.0.0.0 --port "${port}" 2>&1 &
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
