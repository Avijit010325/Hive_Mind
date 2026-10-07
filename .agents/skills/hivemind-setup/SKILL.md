---
name: hivemind-setup
description: >-
  Complete setup, run, and management guide for the HiveMind multi-agent AI project.
  Activate this skill when a user wants to run HiveMind locally, configure API keys,
  start the backend/frontend servers, connect Telegram, rename the project, or
  understand the project structure. Covers dev mode (no Docker) and full Docker stack.
---

# HiveMind Local Setup & Run Guide

HiveMind is a multi-agent AI system powered by Google Gemma that lets users
interact with multiple specialized AI agents from a single Telegram chat.
Self-hostable with zero external dependencies beyond two free API keys.

---

## Architecture

```
Browser ──► Next.js (port 3000) ──/api proxy──► FastAPI (port 8001) ──► SQLite/Postgres
                                                      │
Telegram ◄─────────────── Bot Worker ─────────────────┘
                                │
                                └──► Gemma (Google AI Studio or local Ollama)
```

Three processes run simultaneously:
1. FastAPI backend  — API logic, auth, database, Telegram state
2. Next.js frontend — dashboard + /api proxy gateway to backend
3. Bot Worker       — long-polls Telegram, dispatches messages to Gemma

---

## Prerequisites

- Python 3.11+  →  `python3 --version`
- Node.js 18+ and npm  →  `node --version && npm --version`
- Gemini API key (free) from https://aistudio.google.com/apikey
- Telegram Bot Token from @BotFather  →  send it `/newbot`

Optional for Docker mode:
- Docker and Docker Compose

---

## Validate API Keys First

```bash
# Test Telegram token
curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getMe"
# Expected: {"ok":true,"result":{"username":"YourBotName",...}}

# Test Gemini key
curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=<YOUR_KEY>" | head -c 200
# Expected: {"models":[{"name":"models/gemini-..."},...]}
```

---

## Method 1: Local Dev (No Docker — Fastest to Start)

### Step 1 — Python backend setup

```bash
python3 -m venv .venv
source .venv/bin/activate          # Mac/Linux
# .venv\Scripts\activate           # Windows
pip install -r requirements.txt -r requirements-dev.txt
```

### Step 2 — Start FastAPI backend (Terminal 1)

```bash
source .venv/bin/activate
GEMINI_API_KEY=<your_key> \
TELEGRAM_BOT_TOKEN=<your_token> \
LLM_MODEL=gemma-4-26b-a4b-it \
PUBLIC_URL=http://localhost:8000 \
uvicorn app.main:app --port 8001
```

Expected: `INFO: Application startup complete. Uvicorn running on http://127.0.0.1:8001`

### Step 3 — Start Next.js frontend (Terminal 2)

```bash
cd frontend
npm install
npm run dev
```

Expected: `✓ Ready in Xms  →  http://localhost:3000`

### Step 4 — Start Telegram bot worker (Terminal 3)

```bash
source .venv/bin/activate
GEMINI_API_KEY=<your_key> \
TELEGRAM_BOT_TOKEN=<your_token> \
LLM_MODEL=gemma-4-26b-a4b-it \
PUBLIC_URL=http://localhost:8000 \
python -m app.bot
```

Expected: `INFO hivemind.bot: Bot online as @YourBotName`

### Step 5 — Verify everything is connected

```bash
curl -s http://localhost:8001/api/status
```

Expected:
```json
{
  "llm":      {"model": "gemma-4-26b-a4b-it", "reachable": true, "key_missing": false},
  "telegram": {"configured": true, "bot": "YourBotName"},
  "signups":  true
}
```

```bash
# Verify frontend picks up the backend
curl -s http://localhost:3000 | grep -o "<title>.*</title>"
# Expected: <title>HiveMind · A crew of AI agents in one Telegram chat</title>

# Create a test account
curl -s -X POST http://localhost:8001/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "yourpassword"}'
# Expected: {"id":1,"email":"you@example.com"}
```

Open http://localhost:3000 in your browser, sign up, and connect Telegram.

---

## Method 2: Docker (Full Production Stack)

```bash
cp .env.example .env
# Edit .env — fill in GEMINI_API_KEY and TELEGRAM_BOT_TOKEN
docker compose up -d
docker compose logs -f        # watch startup
open http://localhost:8000
```

### Run Gemma locally with Ollama instead of AI Studio

```bash
# Add to .env:
# LLM_BASE_URL=http://ollama:11434/v1
# LLM_MODEL=gemma3:4b

docker compose --profile local up -d
```

---

## Using the App

### Web Dashboard (http://localhost:3000)
1. Sign up with email + password
2. Click "Connect Telegram" — scan QR or tap the deep link
3. Start chatting in Telegram — agents reply automatically

### Telegram Commands

| Command | What happens |
|---|---|
| Any message | Best-suited agent responds (auto-dispatch) |
| `@AgentName message` | Talk directly to that agent |
| `/hire a Python expert` | Create a new agent in one sentence |
| `/agents` | List crew, pin one, or return to auto |
| `/remember I'm vegetarian` | Store a fact all agents share |
| `/memory` | View stored memories |
| `/forget` | Delete a memory |
| `/new` | Start a fresh conversation thread |
| `/unlink` | Disconnect this Telegram chat |

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | **required** | Google AI Studio key |
| `TELEGRAM_BOT_TOKEN` | **required** | BotFather token |
| `LLM_MODEL` | `gemma-4-26b-a4b-it` | Must contain "gemma" |
| `PUBLIC_URL` | `http://localhost:8000` | Where users reach the dashboard |
| `PORT` | `8000` | Host port for the web container |
| `ALLOW_SIGNUPS` | `true` | Set `false` to close registration |
| `MAX_AGENTS_PER_USER` | `12` | Crew size limit |
| `TOOLS_ENABLED` | `true` | Web search + sandbox for agents |
| `MAX_TOOL_STEPS` | `8` | Autonomous tool rounds per message |
| `FIRECRAWL_API_KEY` | — | Optional — better JS-rendered scraping |
| `SANDBOX_MEMORY` | `2g` | Sandbox memory cap |
| `SANDBOX_CPUS` | `2` | Sandbox CPU cap |

---

## Authentication System

- Passwords hashed with **bcrypt** (auto-salted, 72-byte input limit)
- Sessions stored as **JWT (HS256)** in an HTTP-only cookie: `hivemind_session`
- Session lifetime: **30 days**
- Secret key: auto-generated on first run and persisted in the DB
- All API routes protected via `Depends(current_user)` in FastAPI

---

## Where Data Lives

| Mode | Database | Path |
|---|---|---|
| Local dev | SQLite | `./hivemind.db` in project root |
| Docker default | PostgreSQL | Docker volume `db-data` |
| Docker custom | External Postgres | Set `DATABASE_URL` in `.env` |

---

## Project File Map

```
hivemind/
├── app/
│   ├── main.py          # REST API routes
│   ├── bot.py           # Telegram bot long-poll worker
│   ├── brain.py         # Gemma dispatcher + prompt assembly
│   ├── llm.py           # Gemma OpenAI-compatible client
│   ├── tools.py         # Agent tools: web_search, shell, serve, etc.
│   ├── sandbox.py       # Sandbox container HTTP client
│   ├── knowledge.py     # BM25 doc chunking + retrieval
│   ├── firecrawl.py     # Firecrawl optional client
│   ├── db.py            # SQLAlchemy ORM models
│   ├── security.py      # bcrypt + PyJWT session auth
│   ├── config.py        # All env var settings
│   └── telegram.py      # Telegram API client + Markdown formatter
├── frontend/
│   ├── app/
│   │   ├── page.tsx              # Landing page
│   │   ├── dashboard/page.tsx    # Dashboard
│   │   └── api/[...path]/route.ts # /api/* proxy to FastAPI
│   ├── components/
│   │   ├── Landing.tsx       # Landing page UI + WebGL orb
│   │   ├── Dashboard.tsx     # Main crew management dashboard
│   │   ├── AgentEditor.tsx   # Agent editor + test chat
│   │   └── VoiceBuilder.tsx  # Voice interview agent creator
│   └── lib/
│       ├── api.ts        # Frontend fetch client
│       └── landing.ts    # WebGL orb shader + star field + animations
├── sandbox/
│   └── runner.py        # Tiny HTTP server for shell execution inside sandbox
├── tests/               # pytest suite
├── .env.example         # Env var template
├── docker-compose.yml   # Full 4-container Docker stack
└── Dockerfile           # Backend image build
```

---

## Renaming the Project

```bash
NEW="YourBrand"
NEW_LOWER="yourbrand"

# Backend
find app/ sandbox/ -name "*.py" | xargs sed -i '' "s/HiveMind/${NEW}/g; s/hivemind/${NEW_LOWER}/g"

# Frontend
find frontend/app frontend/components frontend/lib -name "*.ts" -o -name "*.tsx" -o -name "*.css" | \
  xargs sed -i '' "s/HiveMind/${NEW}/g; s/hivemind/${NEW_LOWER}/g"

# Config files
sed -i '' "s/HiveMind/${NEW}/g; s/hivemind/${NEW_LOWER}/g" \
  docker-compose.yml .env.example frontend/package.json README.md

# Restart servers to pick up the changes
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `command not found: docker` | Install Docker Desktop from docker.com |
| `key_missing: true` in /api/status | Pass keys as env vars inline when starting uvicorn (not just in .env) |
| `model_ready: false` | Normal — Google AI Studio uses REST, not an Ollama model list. Agents still work |
| `503` when chatting | Gemini key invalid or rate-limited. Verify key at aistudio.google.com |
| Bot not responding in Telegram | Bot worker not running — start Terminal 3 |
| Port already in use | `lsof -ti:3000 | xargs kill -9` and `lsof -ti:8001 | xargs kill -9` |
| Login fails after rename | Cookie name changed — clear browser cookies for localhost |
