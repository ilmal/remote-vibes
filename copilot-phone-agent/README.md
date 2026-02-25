# Copilot Phone Agent

> **A beautiful, mobile-first AI coding dashboard** — voice to git, repos to PRs, right from your phone.

![Tech Stack](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi) ![HTMX](https://img.shields.io/badge/HTMX-1.9-3366CC) ![TailwindCSS](https://img.shields.io/badge/Tailwind-v3-06B6D4?logo=tailwindcss) ![Docker](https://img.shields.io/badge/Docker-first-2496ED?logo=docker) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql)

---

## Features

| Feature | Details |
|---|---|
| **Voice-to-text** | `faster-whisper` `large-v3-turbo` — runs fully offline on CPU/GPU after first download |
| **Per-repo containers** | Each repo gets an isolated Docker container with `code-server` + GitHub Copilot |
| **Copilot agent chat** | Live streaming SSE — thinking steps, tool calls, shell output, file edits |
| **Auto PR creation** | "Done with feature X" → commit → branch → PR — all in one sentence |
| **GitHub integration** | List repos, clone, push, create branches/PRs via PyGithub + `gh` CLI |
| **Cloudflare Tunnel** | Optional `cloudflared` in each container for public dev URLs |
| **Mobile-first UI** | HTMX + TailwindCSS + DaisyUI + Alpine.js — loads instantly on 3G |
| **JWT auth** | fastapi-users with email/password registration |
| **Postgres + Alembic** | Full async SQLAlchemy 2.0, schema migrations |

---

## Quick Start

### 1. Clone & Configure

```bash
git clone https://github.com/you/copilot-phone-agent.git
cd copilot-phone-agent

cp .env.example .env
# Edit .env – set POSTGRES_PASSWORD, SECRET_KEY, and optionally GITHUB_PAT
nano .env
```

**Generate a strong secret key:**
```bash
openssl rand -hex 32
```

### 2. Build & Start

```bash
docker compose up --build -d
```

This starts:
- `cpa_postgres` — PostgreSQL 16
- `cpa_redis` — Redis 7
- `cpa_main` — FastAPI app on port **8000**

### 3. Run Database Migrations

```bash
docker compose exec main-app alembic upgrade head
```

### 4. Create Your User Account

Open [http://localhost:8000/register](http://localhost:8000/register) in your browser.

Or via API:
```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"YourPass123!","display_name":"Admin"}'
```

### 5. Add Your GitHub PAT

1. Go to [GitHub Settings → Tokens](https://github.com/settings/tokens/new?scopes=repo,workflow)
2. Create a token with scopes: `repo`, `workflow`, `read:user`
3. Open [http://localhost:8000/settings-page](http://localhost:8000/settings-page) and paste it

---

## Build the Agent Image

The per-repo agent container uses `Dockerfile.agent`:

```bash
docker build -f Dockerfile.agent -t cpa_agent:latest .
```

This image is automatically used when you start a repo session from the dashboard.

---

## Accessing from Your Phone

### Local Network (same WiFi)

```bash
# Find your machine's LAN IP
ip addr show | grep "inet " | grep -v 127.0.0.1
```

Visit `http://YOUR_LAN_IP:8000` from your phone browser.

### Public Access via Cloudflare Tunnel (recommended)

```bash
# Install cloudflared locally
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
     -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared

# Create a quick tunnel (no account needed)
cloudflared tunnel --url http://localhost:8000
```

Copy the `trycloudflare.com` URL – open it on any device, anywhere.

---

## Running Tests

```bash
# Install test dependencies
pip install -r requirements.txt -r tests/requirements-test.txt aiosqlite

# Run all tests with coverage
pytest --cov=app --cov-report=term-missing --cov-fail-under=70

# Run specific test file
pytest tests/test_auth.py -v
```

---

## Project Structure

```
copilot-phone-agent/
├── docker-compose.yml          # All services
├── Dockerfile.main             # FastAPI app
├── Dockerfile.agent            # Per-repo container (code-server + Copilot)
├── agent_entrypoint.sh         # Agent container startup script
├── agent_requirements.txt      # Agent Python deps
├── requirements.txt            # Main app Python deps
├── alembic.ini                 # Alembic config
├── alembic/
│   ├── env.py                  # Alembic environment
│   └── versions/
│       └── 0001_initial.py     # Initial schema migration
├── app/
│   ├── main.py                 # FastAPI app factory
│   ├── config.py               # Pydantic settings
│   ├── auth.py                 # fastapi-users JWT config
│   ├── database.py             # Async SQLAlchemy engine
│   ├── models/                 # SQLAlchemy ORM models
│   │   ├── user.py
│   │   └── session.py
│   ├── schemas/                # Pydantic schemas
│   │   ├── user.py
│   │   ├── session.py
│   │   └── chat.py
│   ├── crud/                   # Database operations
│   ├── dependencies/           # FastAPI DI providers
│   ├── routers/                # API endpoint routers
│   │   ├── repos.py            # GitHub repo listing
│   │   ├── sessions.py         # Container lifecycle
│   │   ├── chat.py             # Agent SSE streaming
│   │   ├── voice.py            # Whisper transcription
│   │   ├── settings.py         # User settings
│   │   └── web.py              # HTML page routes
│   ├── services/
│   │   ├── github.py           # PyGithub integration
│   │   ├── docker_manager.py   # docker-py container management
│   │   ├── voice.py            # faster-whisper STT
│   │   ├── copilot_agent.py    # Agent container HTTP client
│   │   └── copilot_agent_runner.py  # Runs INSIDE agent containers
│   └── templates/              # Jinja2 HTML templates
│       ├── base.html
│       ├── login.html
│       ├── register.html
│       ├── dashboard.html
│       └── settings.html
└── tests/
    ├── conftest.py
    ├── test_auth.py
    ├── test_sessions.py
    ├── test_chat.py
    ├── test_voice.py
    ├── test_github.py
    └── test_docker.py
```

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `POSTGRES_USER` | Yes | PostgreSQL username |
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `POSTGRES_DB` | Yes | Database name |
| `DATABASE_URL` | Yes | AsyncPG connection string |
| `SECRET_KEY` | Yes | JWT signing secret (min 32 chars) |
| `GITHUB_PAT` | Yes* | GitHub Personal Access Token (*or set per-user in Settings) |
| `CLOUDFLARE_TUNNEL_TOKEN` | No | Cloudflare tunnel token for public URLs |
| `WHISPER_MODEL` | No | Whisper model name (default: `large-v3-turbo`) |
| `WHISPER_DEVICE` | No | `cpu` or `cuda` (default: `cpu`) |
| `AGENT_IMAGE` | No | Docker image for agent containers (default: `cpa_agent:latest`) |
| `AGENT_BASE_PORT` | No | Starting port for agent containers (default: `9000`) |

---

## How Agent Sessions Work

```
User selects repo → "Start Session"
         ↓
FastAPI calls DockerManager.start_agent_container()
         ↓
New Docker container: cpa_agent:latest
  ├── code-server on :8080   (EXPOSED as localhost:9000)
  ├── agent FastAPI on :3000  (EXPOSED as localhost:9001)
  └── isolated Docker network
         ↓
User types/speaks in dashboard
         ↓
POST /api/chat/{session_id}/stream
  → streams SSE from agent container port
  → renders thinking/tool_call/text chunks live
         ↓
"Done with feature auth" → auto PR
  → commit → git push → gh pr create
```

---

## Voice Workflow

1. Tap the 🎤 microphone button
2. Speak your request
3. Tap again to stop
4. Audio is sent to `/api/voice/transcribe`
5. `faster-whisper` transcribes locally (offline, no API keys)
6. Text appears in chat and auto-sends

**First run:** The `large-v3-turbo` model (~800 MB) downloads automatically to the `whisper_models` Docker volume.

---

## Security Notes

- All agent containers run as **non-root** (`agent` user)
- Each container has its own **isolated Docker network**
- GitHub PAT is never exposed to the browser — backend-only
- JWT tokens stored as `httponly` cookies (no XSS access)
- Rate limiting: 200 req/min per IP
- Container resource limits: 2 GB RAM, 1.5 CPU

---

## Development

```bash
# Run locally without Docker (requires postgres + redis running)
pip install -r requirements.txt -r tests/requirements-test.txt aiosqlite
cp .env.example .env  # edit DATABASE_URL to point to local postgres
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Watch tailwind (optional, CDN already included)
# tailwindcss -i app/static/css/input.css -o app/static/css/output.css --watch
```

---

## License

MIT — build something great. ✨
