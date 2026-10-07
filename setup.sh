#!/usr/bin/env bash
# =============================================================================
# HiveMind — One-command local setup script
# Works on macOS and Linux. Run: bash setup.sh
# =============================================================================

set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
CYAN="\033[0;36m"
RESET="\033[0m"

header() { echo -e "\n${BOLD}${CYAN}▶ $1${RESET}"; }
ok()     { echo -e "${GREEN}✓ $1${RESET}"; }
warn()   { echo -e "${YELLOW}⚠ $1${RESET}"; }
err()    { echo -e "${RED}✗ $1${RESET}"; exit 1; }

echo -e "${BOLD}"
echo "  ██╗  ██╗██╗██╗   ██╗███████╗███╗   ███╗██╗███╗   ██╗██████╗ "
echo "  ██║  ██║██║██║   ██║██╔════╝████╗ ████║██║████╗  ██║██╔══██╗"
echo "  ███████║██║██║   ██║█████╗  ██╔████╔██║██║██╔██╗ ██║██║  ██║"
echo "  ██╔══██║██║╚██╗ ██╔╝██╔══╝  ██║╚██╔╝██║██║██║╚██╗██║██║  ██║"
echo "  ██║  ██║██║ ╚████╔╝ ███████╗██║ ╚═╝ ██║██║██║ ╚████║██████╔╝"
echo "  ╚═╝  ╚═╝╚═╝  ╚═══╝  ╚══════╝╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝╚═════╝ "
echo -e "${RESET}"
echo "  Multi-agent AI crew in one Telegram chat — powered by Gemma"
echo ""

# ── 1. Check prerequisites ─────────────────────────────────────────────────
header "Checking prerequisites"

command -v python3 >/dev/null 2>&1 || err "Python 3 not found. Install from https://python.org"
PY_VER=$(python3 -c "import sys; print(sys.version_info.minor)")
[ "$PY_VER" -ge 11 ] || err "Python 3.11+ required. Found 3.$PY_VER"
ok "Python $(python3 --version)"

command -v node >/dev/null 2>&1 || err "Node.js not found. Install from https://nodejs.org"
ok "Node.js $(node --version)"

command -v npm >/dev/null 2>&1 || err "npm not found."
ok "npm $(npm --version)"

# ── 2. Collect API keys ────────────────────────────────────────────────────
header "API Key Setup"

if [ -f .env ]; then
  warn ".env already exists — skipping key prompts. Edit it manually if needed."
else
  echo ""
  echo "  You need two free keys:"
  echo "  1. Gemini API key  →  https://aistudio.google.com/apikey"
  echo "  2. Telegram token  →  https://t.me/BotFather  (send /newbot)"
  echo ""

  read -rp "  Enter your Gemini API key: " GEMINI_KEY
  read -rp "  Enter your Telegram Bot Token: " TG_TOKEN

  # Validate Gemini key
  echo -n "  Validating Gemini key... "
  GEMINI_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "https://generativelanguage.googleapis.com/v1beta/models?key=${GEMINI_KEY}")
  [ "$GEMINI_STATUS" = "200" ] && ok "Valid" || err "Invalid Gemini key (HTTP $GEMINI_STATUS)"

  # Validate Telegram token
  echo -n "  Validating Telegram token... "
  TG_OK=$(curl -s "https://api.telegram.org/bot${TG_TOKEN}/getMe" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ok','false'))")
  [ "$TG_OK" = "True" ] && ok "Valid" || err "Invalid Telegram token"

  cp .env.example .env
  # Use Python for cross-platform sed-like replacement
  python3 - <<PYEOF
import re
with open('.env', 'r') as f:
    content = f.read()
content = content.replace('GEMINI_API_KEY=', f'GEMINI_API_KEY=${GEMINI_KEY}')
content = content.replace('TELEGRAM_BOT_TOKEN=', f'TELEGRAM_BOT_TOKEN=${TG_TOKEN}')
with open('.env', 'w') as f:
    f.write(content)
PYEOF
  ok ".env file created with your keys"
fi

# ── 3. Python virtualenv + dependencies ───────────────────────────────────
header "Installing Python dependencies"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  ok "Virtual environment created"
fi

source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt -q
ok "Python packages installed"

# ── 4. Node.js dependencies ───────────────────────────────────────────────
header "Installing Node.js dependencies"
cd frontend && npm install --silent && cd ..
ok "Node packages installed"

# ── 5. Done — print start instructions ────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}✅ Setup complete! Start HiveMind with:${RESET}"
echo ""
echo -e "  ${CYAN}make dev${RESET}         # Start all 3 services in one command (requires tmux)"
echo ""
echo -e "  ${CYAN}Or manually in 3 separate terminals:${RESET}"
echo ""
echo -e "  Terminal 1:  ${YELLOW}make backend${RESET}    # FastAPI on :8001"
echo -e "  Terminal 2:  ${YELLOW}make frontend${RESET}   # Next.js on :3000"
echo -e "  Terminal 3:  ${YELLOW}make bot${RESET}        # Telegram bot worker"
echo ""
echo -e "  Then open:  ${BOLD}http://localhost:3000${RESET}"
echo ""
