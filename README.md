<p align="center">
  <img src="frontend/public/logo.png" alt="HiveMind" width="80" />
</p>

<h1 align="center">HiveMind</h1>

<p align="center">
  A self-hostable multi-agent AI system — talk to a crew of specialized AI agents from <strong>Telegram</strong>, <strong>WhatsApp</strong>, and a web dashboard. Powered by <strong>Google Gemma</strong>.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/python-3.12-blue" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/Next.js-16-black" alt="Next.js 16" />
  <img src="https://img.shields.io/badge/powered%20by-Gemma-orange" alt="Gemma" />
</p>

---

## ✨ Features

| Feature | Description |
|---|---|
| 🤖 **Multi-agent crew** | Create specialized AI agents (researcher, coder, writer, etc.) |
| 📱 **Telegram integration** | Chat with your entire crew from Telegram |
| 💬 **WhatsApp integration** | Two-way WhatsApp messaging via Meta Cloud API |
| 📅 **Google Calendar** | Natural-language event scheduling with OAuth |
| ⏰ **Scheduled messages** | "Send Rahul Happy Birthday tomorrow at 9 AM" |
| 🧠 **Gemma dispatcher** | Automatically routes messages to the best agent |
| 🌐 **Web dashboard** | Manage your crew, view memory, test agents |
| 🔒 **Self-hostable** | Runs entirely on your machine or Docker |

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- A [Google AI Studio](https://aistudio.google.com/apikey) API key
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### Setup (Interactive wizard)
```bash
git clone https://github.com/Avijit010325/Hive_Mind.git
cd Hive_Mind
bash setup.sh
```

### Manual setup (3 terminals)

```bash
# Terminal 1 — Backend (FastAPI on :8001)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
GEMINI_API_KEY=<key> TELEGRAM_BOT_TOKEN=<token> uvicorn app.main:app --port 8001

# Terminal 2 — Frontend (Next.js on :3000)
cd frontend && npm install && npm run dev

# Terminal 3 — Telegram bot worker
source .venv/bin/activate
GEMINI_API_KEY=<key> TELEGRAM_BOT_TOKEN=<token> python -m app.bot
```

### Docker (full stack)
```bash
cp .env.example .env   # fill in API keys
docker compose up -d
open http://localhost:8000
```

---

## 🔑 Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | From [aistudio.google.com](https://aistudio.google.com/apikey) |
| `TELEGRAM_BOT_TOKEN` | ✅ | From [@BotFather](https://t.me/BotFather) |
| `LLM_MODEL` | — | Default: `gemma-4-26b-a4b-it` |
| `WHATSAPP_ACCESS_TOKEN` | Optional | Meta WhatsApp Cloud API token |
| `WHATSAPP_PHONE_NUMBER_ID` | Optional | From Meta Business Manager |
| `WHATSAPP_VERIFY_TOKEN` | Optional | Webhook verify token (any string) |
| `GOOGLE_CLIENT_ID` | Optional | For Google Calendar OAuth |
| `GOOGLE_CLIENT_SECRET` | Optional | For Google Calendar OAuth |

> **⚠️ Never commit your `.env` file.** It is listed in `.gitignore`.

---

## 🏗️ Architecture

```
Browser ──► Next.js :3000 ──/api proxy──► FastAPI :8001 ──► SQLite/Postgres
                                               │
Telegram ◄────── bot worker (app/bot.py) ──────┤
                      │                        │
WhatsApp ◄──── webhook (app/whatsapp_agent.py)─┤
                                               │
Google Calendar ◄── calendar_agent.py ─────────┘
                      │
                   Gemma LLM (Google AI Studio / Ollama)
```

---

## 📁 File Structure

```
app/
  main.py              → All REST API routes
  bot.py               → Telegram long-poll worker
  brain.py             → Gemma dispatcher + agent drafter
  llm.py               → Gemma API client
  tools.py             → web_search, shell, write_file, serve
  calendar_agent.py    → Google Calendar NL scheduling
  whatsapp_agent.py    → WhatsApp Cloud API client + NL parsing
  whatsapp_scheduler.py→ Persistent background scheduler
  knowledge.py         → BM25 document retriever
  db.py                → SQLAlchemy ORM models
  security.py          → bcrypt + JWT auth
  config.py            → All settings from env vars

frontend/
  app/page.tsx         → Landing page
  app/dashboard/       → Dashboard (crew management)
  components/
    Dashboard.tsx      → Main dashboard layout
    AgentEditor.tsx    → Create/edit agents + knowledge docs
    CalendarAgent.tsx  → Google Calendar card
    WhatsAppAgent.tsx  → WhatsApp connection card
    ScheduledWhatsApp.tsx → Scheduled message automation
    TelegramCard.tsx   → Telegram link card
    MemoryCard.tsx     → Agent memory viewer
```

---

## 🧪 Tests

```bash
# Run all tests
make test

# Or directly
PYTHONPATH=. pytest tests/ -v
```

**72 tests** covering: API auth, agent CRUD, Telegram bot, Google Calendar, WhatsApp scheduling, tools, and LLM logic.

---

## 🤝 Contributing

Pull requests are welcome! For major changes, open an issue first.

1. Fork the repo
2. Create your branch: `git checkout -b feature/my-feature`
3. Commit: `git commit -m 'Add my feature'`
4. Push: `git push origin feature/my-feature`
5. Open a Pull Request

---

## 📄 License

[MIT](LICENSE) © 2026 Avijit Aditya
