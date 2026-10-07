# =============================================================================
# HiveMind Makefile — works in any terminal on macOS / Linux
# Usage: make <target>
# =============================================================================

# Load .env if it exists
ifneq (,$(wildcard ./.env))
  include .env
  export
endif

VENV        = .venv
PYTHON      = $(VENV)/bin/python
PIP         = $(VENV)/bin/pip
UVICORN     = $(VENV)/bin/uvicorn
BOT_CMD     = $(PYTHON) -m app.bot
BACKEND_PORT = 8001
FRONTEND_DIR = frontend

.DEFAULT_GOAL := help

.PHONY: help setup venv deps-py deps-js backend frontend bot dev status test clean kill

# ── Help ──────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  HiveMind — available commands:"
	@echo ""
	@echo "  Setup:"
	@echo "    make setup       Run the interactive setup wizard (first time)"
	@echo "    make deps-py     Install Python dependencies only"
	@echo "    make deps-js     Install Node.js dependencies only"
	@echo ""
	@echo "  Run (open 3 separate terminals):"
	@echo "    make backend     Start FastAPI backend on :$(BACKEND_PORT)"
	@echo "    make frontend    Start Next.js dashboard on :3000"
	@echo "    make bot         Start Telegram bot worker"
	@echo ""
	@echo "  Dev (all-in-one, requires tmux):"
	@echo "    make dev         Start all 3 services in split tmux panes"
	@echo ""
	@echo "  Utilities:"
	@echo "    make status      Check if backend and frontend are responding"
	@echo "    make test        Run the pytest test suite"
	@echo "    make kill        Kill all HiveMind processes"
	@echo "    make clean       Remove .venv, node_modules, *.db"
	@echo ""

# ── Setup ─────────────────────────────────────────────────────────────────
setup:
	@bash setup.sh

venv:
	@test -d $(VENV) || python3 -m venv $(VENV)
	@echo "✓ Virtual environment ready"

deps-py: venv
	@$(PIP) install -q -r requirements.txt -r requirements-dev.txt
	@echo "✓ Python dependencies installed"

deps-js:
	@cd $(FRONTEND_DIR) && npm install --silent
	@echo "✓ Node dependencies installed"

# ── Run ───────────────────────────────────────────────────────────────────
backend: deps-py
	@echo "▶ Starting FastAPI backend on :$(BACKEND_PORT)..."
	@$(UVICORN) app.main:app --port $(BACKEND_PORT)

frontend: deps-js
	@echo "▶ Starting Next.js frontend on :3000..."
	@cd $(FRONTEND_DIR) && npm run dev

bot: deps-py
	@echo "▶ Starting Telegram bot worker..."
	@$(BOT_CMD)

# ── Dev (tmux all-in-one) ─────────────────────────────────────────────────
dev:
	@command -v tmux >/dev/null 2>&1 || { echo "✗ tmux not found. Install it with: brew install tmux"; exit 1; }
	@tmux new-session -d -s hivemind -x 220 -y 50 2>/dev/null || true
	@tmux rename-window -t hivemind:0 'HiveMind'
	@tmux send-keys -t hivemind:0 'make backend' Enter
	@tmux split-window -h -t hivemind:0
	@tmux send-keys -t hivemind:0 'sleep 3 && make frontend' Enter
	@tmux split-window -v -t hivemind:0
	@tmux send-keys -t hivemind:0 'sleep 4 && make bot' Enter
	@tmux select-pane -t hivemind:0.0
	@echo ""
	@echo "✓ HiveMind started in tmux session 'hivemind'"
	@echo ""
	@echo "  Attach:     tmux attach -t hivemind"
	@echo "  Dashboard:  http://localhost:3000"
	@echo "  Kill all:   make kill"
	@echo ""
	@tmux attach -t hivemind

# ── Utilities ─────────────────────────────────────────────────────────────
status:
	@echo ""
	@echo "── Backend (http://localhost:$(BACKEND_PORT)) ──"
	@curl -s http://localhost:$(BACKEND_PORT)/api/status 2>/dev/null \
		| python3 -m json.tool 2>/dev/null \
		|| echo "  ✗ Backend not running"
	@echo ""
	@echo "── Frontend (http://localhost:3000) ──"
	@curl -s -o /dev/null -w "  HTTP %{http_code}\n" http://localhost:3000 2>/dev/null \
		|| echo "  ✗ Frontend not running"
	@echo ""

test: deps-py
	@$(VENV)/bin/pytest tests/ -v

kill:
	@echo "Stopping HiveMind processes..."
	@pkill -f "uvicorn app.main" 2>/dev/null && echo "✓ Backend stopped" || echo "  Backend was not running"
	@pkill -f "app.bot" 2>/dev/null && echo "✓ Bot stopped" || echo "  Bot was not running"
	@pkill -f "next dev" 2>/dev/null && echo "✓ Frontend stopped" || echo "  Frontend was not running"
	@tmux kill-session -t hivemind 2>/dev/null && echo "✓ tmux session closed" || true
	@echo "Done."

clean:
	@rm -rf $(VENV) $(FRONTEND_DIR)/node_modules $(FRONTEND_DIR)/.next *.db .pytest_cache __pycache__
	@echo "✓ Clean complete"
