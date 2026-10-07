import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.database_url = _env("DATABASE_URL", "sqlite:///./hivemind.db")
        self.secret_key = _env("SECRET_KEY")
        self.public_url = _env("PUBLIC_URL", "http://localhost:8000").rstrip("/")
        self.allow_signups = _env("ALLOW_SIGNUPS", "true").lower() not in ("0", "false", "no")

        # Gemma on Google AI Studio by default; any OpenAI-compatible endpoint
        # serving a Gemma model (e.g. Ollama) also works.
        self.llm_base_url = (
            _env("LLM_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai"
        ).rstrip("/")
        self.llm_api_key = _env("GEMINI_API_KEY") or _env("LLM_API_KEY")
        self.llm_model = _env("LLM_MODEL") or "gemma-4-26b-a4b-it"
        self.llm_timeout = _int("LLM_TIMEOUT", 120)
        # Newer Gemma models reason before answering and that reasoning counts
        # against max_tokens, so they get extra room. Gemma 2/3 don't think.
        _thinks = not any(v in self.llm_model.lower() for v in ("gemma-2", "gemma2", "gemma-3", "gemma3"))
        self.llm_thinking_tokens = _int("LLM_THINKING_TOKENS", 2560 if _thinks else 0)

        self.telegram_token = _env("TELEGRAM_BOT_TOKEN")
        self.telegram_api_base = _env("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
        self.bot_workers = _int("BOT_WORKERS", 8)

        # Tools: agents can search the web and work in the shared sandbox container.
        self.tools_enabled = _env("TOOLS_ENABLED", "true").lower() not in ("0", "false", "no")
        self.sandbox_url = (_env("SANDBOX_URL") or "http://sandbox:7000").rstrip("/")
        self.max_tool_steps = _int("MAX_TOOL_STEPS", 8)
        # Optional: Firecrawl for scraping, search and crawling (self-hosted URLs work too).
        self.firecrawl_api_key = _env("FIRECRAWL_API_KEY")
        self.firecrawl_api_url = (_env("FIRECRAWL_API_URL") or "https://api.firecrawl.dev").rstrip("/")

        self.max_agents = _int("MAX_AGENTS_PER_USER", 12)
        self.max_doc_bytes = _int("MAX_DOC_BYTES", 2_000_000)
        self.max_knowledge_chars = _int("MAX_KNOWLEDGE_CHARS", 400_000)

        # Google Calendar OAuth & Settings
        self.google_client_id = _env("GOOGLE_CLIENT_ID")
        self.google_client_secret = _env("GOOGLE_CLIENT_SECRET")
        self.google_redirect_uri = _env("GOOGLE_REDIRECT_URI", f"{self.public_url}/api/calendar/callback")
        self.default_timezone = _env("DEFAULT_TIMEZONE", "Asia/Kolkata")

        # WhatsApp Cloud API (Meta)
        self.whatsapp_access_token = _env("WHATSAPP_ACCESS_TOKEN")  # permanent system user token
        self.whatsapp_phone_number_id = _env("WHATSAPP_PHONE_NUMBER_ID")  # from Meta Business Manager
        self.whatsapp_verify_token = _env("WHATSAPP_VERIFY_TOKEN", "hivemind_wh_verify")
        self.whatsapp_api_version = _env("WHATSAPP_API_VERSION", "v20.0")
        # When True, Gemma dispatches messages to the best-suited agent (like Telegram).
        # When False, the first agent in the crew always responds.
        self.whatsapp_multi_agent = _env("WHATSAPP_MULTI_AGENT", "true").lower() not in ("0", "false", "no")
        self.whatsapp_scheduler_interval = _int("WHATSAPP_SCHEDULER_INTERVAL", 60)  # seconds between scheduler runs

    @property
    def llm_needs_key(self) -> bool:
        return "googleapis.com" in self.llm_base_url and not self.llm_api_key

    @property
    def cookie_secure(self) -> bool:
        return self.public_url.startswith("https://")

    def validate(self) -> None:
        # HiveMind is Gemma-only by design: prompts, routing and context
        # budgets are all tuned for it.
        if "gemma" not in self.llm_model.lower():
            raise SystemExit(
                f"LLM_MODEL={self.llm_model!r} is not a Gemma model. "
                "HiveMind only runs on Gemma (e.g. gemma-3-27b-it, gemma3:4b)."
            )


settings = Settings()
