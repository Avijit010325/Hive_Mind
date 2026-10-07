# HiveMind — Agent & IDE Guide

This file is automatically read by AI coding assistants (Cursor, Claude, Windsurf,
Copilot, Antigravity, Gemini CLI, etc.) to understand how to work with this project.

---

## What this project is

HiveMind is a self-hostable multi-agent AI system. Users create a "crew" of
specialized AI agents and talk to them from Telegram. A Gemma dispatcher reads
each message and routes it to the best agent. Agents can browse the web, run
shell commands, write files, and host live web pages.

**Tech stack:**
- Backend: Python 3.12, FastAPI, SQLAlchemy (SQLite in dev, Postgres in Docker)
- Frontend: Next.js 16 App Router, TypeScript, Vanilla CSS (no Tailwind)
- AI: Google Gemma via OpenAI-compatible API (Google AI Studio or local Ollama)
- Bot: Telegram long-polling (no webhooks needed)

---

## How to run locally (no Docker)

**Prerequisites:**
```bash
python3 --version    # must be 3.11+
node --version       # must be 18+
npm --version
```

**Quick setup:**
```bash
bash setup.sh        # interactive wizard — installs deps and collects API keys
```

**Or manual (3 terminals):**

```bash
# Terminal 1 — Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
GEMINI_API_KEY=<key> TELEGRAM_BOT_TOKEN=<token> LLM_MODEL=gemma-4-26b-a4b-it \
  uvicorn app.main:app --port 8001

# Terminal 2 — Frontend
cd frontend && npm install && npm run dev

# Terminal 3 — Bot
source .venv/bin/activate
GEMINI_API_KEY=<key> TELEGRAM_BOT_TOKEN=<token> LLM_MODEL=gemma-4-26b-a4b-it \
  python -m app.bot
```

**Makefile shortcuts (any terminal):**
```bash
make setup     # first-time interactive setup
make backend   # start FastAPI on :8001
make frontend  # start Next.js on :3000
make bot       # start Telegram bot worker
make dev       # start all 3 in tmux (requires: brew install tmux)
make status    # health-check all services
make test      # run pytest
make kill      # stop all HiveMind processes
make clean     # remove .venv, node_modules, *.db
```

**Docker (full stack):**
```bash
cp .env.example .env   # fill in GEMINI_API_KEY and TELEGRAM_BOT_TOKEN
docker compose up -d
open http://localhost:8000
```

---

## Verify everything is working

```bash
# Backend status (all keys should show configured)
curl -s http://localhost:8001/api/status | python3 -m json.tool

# Frontend title (should say HiveMind)
curl -s http://localhost:3000 | grep -o "<title>.*</title>"

# Create a test account
curl -s -X POST http://localhost:8001/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"password123"}'
```

---

## Key environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ | — | From aistudio.google.com/apikey |
| `TELEGRAM_BOT_TOKEN` | ✅ | — | From @BotFather on Telegram |
| `LLM_MODEL` | — | `gemma-4-26b-a4b-it` | Must contain "gemma" |
| `PUBLIC_URL` | — | `http://localhost:8000` | For preview links |
| `ALLOW_SIGNUPS` | — | `true` | Set false to close registration |
| `TOOLS_ENABLED` | — | `true` | Web search + sandbox for agents |
| `MAX_AGENTS_PER_USER` | — | `12` | Crew size limit |

In dev mode, pass keys inline with the command. In Docker, put them in `.env`.

---

## Architecture

```
Browser ──► Next.js :3000 ──/api proxy──► FastAPI :8001 ──► SQLite/Postgres
                                               │
Telegram ◄────── bot worker (app/bot.py) ──────┘
                      │
                      └──► Gemma (Google AI Studio or Ollama)
```

The Next.js app never exposes FastAPI directly — all `/api/*` calls are proxied
through `frontend/app/api/[...path]/route.ts`, keeping session cookies first-party.

---

## File map (what does what)

```
app/
  main.py       → All REST API routes (auth, agents, chat, memory, Telegram)
  bot.py        → Telegram long-poll worker; handles commands and file uploads
  brain.py      → Gemma prompt builder + multi-agent dispatcher + agent drafter
  llm.py        → Gemma API client (OpenAI-compatible, handles retries + thinking tokens)
  tools.py      → Tool protocol: web_search, fetch_url, crawl_site, shell, write_file, serve
  sandbox.py    → HTTP client to the sandbox container running commands
  knowledge.py  → BM25 document chunker + retriever (no embeddings, no vector DB)
  firecrawl.py  → Optional Firecrawl client for JS-rendered web scraping
  db.py         → SQLAlchemy ORM models: User, Agent, Message, Memory, Doc, Chunk, etc.
  security.py   → bcrypt password hashing + PyJWT session tokens (HTTP-only cookie)
  config.py     → All settings loaded from env vars
  telegram.py   → Minimal Telegram Bot API client + Markdown-to-HTML formatter

frontend/
  app/page.tsx                   → Landing page
  app/dashboard/page.tsx         → Dashboard (requires login)
  app/api/[...path]/route.ts     → /api/* reverse proxy to FastAPI
  components/Landing.tsx         → Landing page with WebGL orb and demo animations
  components/Dashboard.tsx       → Crew management dashboard
  components/AgentEditor.tsx     → Agent create/edit form + test chat + knowledge docs
  components/VoiceBuilder.tsx    → Voice interview wizard using Web Speech API
  lib/api.ts                     → Frontend fetch client (typed, throws ApiError)
  lib/landing.ts                 → WebGL orb shader + star field + scroll animations

sandbox/
  runner.py     → Tiny HTTP server inside the sandbox container; runs shell commands
```

---

## Authentication system

- Passwords: **bcrypt** (auto-salted, 72-byte limit enforced)
- Sessions: **JWT (HS256)** stored in HTTP-only cookie `hivemind_session` (30-day expiry)
- Secret key: auto-generated on first startup and persisted in the database
- All protected routes use `Depends(current_user)` in FastAPI

---

## Database

Dev mode uses **SQLite** at `./hivemind.db` (auto-created on first startup).
Docker uses **PostgreSQL** in a volume. Schema is created by `db.init_db()` using
SQLAlchemy `create_all` + manual column-patch migrations for additive changes.

---

## Coding conventions

- Python: type hints everywhere, no external framework for DI (just FastAPI Depends)
- TypeScript: strict mode on, no `any`, prefer `unknown`
- CSS: Vanilla CSS with CSS custom properties, no Tailwind, no CSS-in-JS
- No build step for the backend — plain Python modules
- Tests in `tests/` use `pytest` with a test FastAPI client and a fake LLM stub

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `key_missing: true` in /api/status | Env vars not passed to uvicorn | Pass them inline: `GEMINI_API_KEY=... uvicorn ...` |
| `model_ready: false` in /api/status | Normal for Google AI Studio | Ignore — agents still respond |
| `503` on chat | Gemini key invalid or rate-limited | Check key at aistudio.google.com |
| Bot silent in Telegram | Bot worker not running | Start `python -m app.bot` |
| Port conflict | Another process on :3000 or :8001 | `make kill` or `lsof -ti:3000 \| xargs kill -9` |
| Login broken after rename | Cookie name changed | Clear browser cookies for localhost |
