from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


def now() -> datetime:
    """Naive UTC timestamp (portable across SQLite and Postgres)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


_url = settings.database_url
if _url.startswith("postgres://"):
    _url = "postgresql+psycopg://" + _url[len("postgres://"):]
elif _url.startswith("postgresql://"):
    _url = "postgresql+psycopg://" + _url[len("postgresql://"):]

if _url.startswith("sqlite"):
    engine = create_engine(_url, connect_args={"check_same_thread": False})
else:
    engine = create_engine(_url, pool_pre_ping=True, pool_size=10, max_overflow=20)

SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(32))
    emoji: Mapped[str] = mapped_column(String(16), default="🤖")
    # One line describing what the agent is for; the router reads this.
    role: Mapped[str] = mapped_column(String(160), default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    # Whether this agent may use its sandbox computer and the internet.
    tools: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Sandbox(Base):
    """The secret token in an agent's preview links."""
    __tablename__ = "sandboxes"
    agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class Doc(Base):
    __tablename__ = "docs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(160))
    chars: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int] = mapped_column(Integer, index=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    text: Mapped[str] = mapped_column(Text)


class Memory(Base):
    """Facts about the user, shared by every agent in their crew."""
    __tablename__ = "memories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    text: Mapped[str] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class TelegramLink(Base):
    __tablename__ = "telegram_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    display: Mapped[str] = mapped_column(String(120), default="")
    # NULL means "auto": the dispatcher picks an agent per message.
    active_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class LinkToken(Base):
    __tablename__ = "link_tokens"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_key: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class KV(Base):
    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class GoogleToken(Base):
    __tablename__ = "google_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    credentials: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class WhatsAppLink(Base):
    """Maps a WhatsApp phone number to a Hivemind user account."""
    __tablename__ = "whatsapp_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # E.164 format
    display: Mapped[str] = mapped_column(String(120), default="")
    # NULL = auto-dispatch via Gemma. Set to an agent ID to pin one agent.
    active_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class WhatsAppSchedule(Base):
    """A user-confirmed scheduled WhatsApp message."""
    __tablename__ = "whatsapp_schedules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    # Who to send the message to — stored as the display name the user provided.
    # Resolved to an actual WhatsApp phone number by the scheduler at send time.
    recipient: Mapped[str] = mapped_column(String(128))
    # The phone number in E.164 if already known (e.g. linked contact).
    recipient_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    # When to send. Stored as naive UTC.
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    # null = one-time; "daily" or "weekly" for recurring.
    recurrence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # scheduled / sending / sent / failed / cancelled / template_required
    status: Mapped[str] = mapped_column(String(24), default="scheduled", index=True)
    # Idempotency: we only attempt if sent_at is None
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


def init_db() -> None:
    Base.metadata.create_all(engine)
    # create_all never alters existing tables, so columns added after the first
    # release are patched in here.
    if "tools" not in {c["name"] for c in inspect(engine).get_columns("agents")}:
        default = "TRUE" if engine.dialect.name == "postgresql" else "1"
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE agents ADD COLUMN tools BOOLEAN NOT NULL DEFAULT {default}"))
    if "timezone" not in {c["name"] for c in inspect(engine).get_columns("users")}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN timezone VARCHAR(64)"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def clean(text: str) -> str:
    """Postgres rejects NUL bytes in text columns."""
    return (text or "").replace("\x00", "")

