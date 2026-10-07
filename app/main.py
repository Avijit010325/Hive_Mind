"""HiveMind API. The dashboard is the Next.js app in frontend/, which proxies /api here."""
import logging
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import segno
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import brain, calendar_agent, firecrawl, knowledge, llm, sandbox, security, whatsapp_agent, whatsapp_scheduler
from .config import settings
from .db import Agent, Doc, Chunk, LinkToken, Memory, Message, Sandbox, TelegramLink, User, WhatsAppLink, WhatsAppSchedule, clean, get_db, init_db, now
from .telegram import Telegram, TelegramError

log = logging.getLogger("hivemind.web")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LINK_TOKEN_MINUTES = 15


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request logs would leak the bot token
    settings.validate()
    init_db()
    security.secret()
    whatsapp_scheduler.start_scheduler()
    yield
    whatsapp_scheduler.stop_scheduler()


app = FastAPI(title="HiveMind", lifespan=lifespan, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """One readable sentence instead of pydantic's nested error list."""
    first = exc.errors()[0] if exc.errors() else {}
    field = str((first.get("loc") or ["input"])[-1]).replace("_", " ").capitalize()
    return JSONResponse({"detail": f"{field}: {first.get('msg', 'invalid value')}."}, status_code=422)


# --- helpers ---------------------------------------------------------------

_attempts: dict[str, deque] = defaultdict(deque)
_attempts_lock = threading.Lock()


def throttle(key: str, limit: int, window: int = 300) -> None:
    """Small in-memory brake: at most `limit` attempts per `window` seconds for `key`."""
    with _attempts_lock:
        q = _attempts[key]
        cutoff = time.time() - window
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "Too many attempts. Please wait a few minutes.")
        q.append(time.time())


def client_ip(request: Request) -> str:
    # Behind the Next.js gateway this is only a real visitor address when a
    # reverse proxy in front sets X-Forwarded-For; otherwise everyone shares
    # one address, which is why the per-address limits below are loose.
    return request.client.host if request.client else "unknown"


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = security.read_session(request.cookies.get(security.COOKIE, ""))
    user = db.get(User, user_id) if user_id else None
    if not user:
        raise HTTPException(401, "Please log in.")
    return user


def own_agent(agent_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Agent:
    agent = db.get(Agent, agent_id)
    if not agent or agent.user_id != user.id:
        raise HTTPException(404, "Agent not found.")
    return agent


def set_session(response: Response, user: User) -> None:
    response.set_cookie(
        security.COOKIE, security.make_session(user.id), max_age=security.SESSION_SECONDS,
        httponly=True, samesite="lax", secure=settings.cookie_secure, path="/",
    )


def agent_json(a: Agent) -> dict:
    return {"id": a.id, "name": a.name, "emoji": a.emoji, "role": a.role, "instructions": a.instructions, "tools": a.tools}


_bot_lock = threading.Lock()
_bot_cache: tuple[float, str] = (0.0, "")


def bot_username() -> str:
    """The platform bot's @username, or '' when Telegram isn't set up."""
    global _bot_cache
    if not settings.telegram_token:
        return ""
    with _bot_lock:
        checked, name = _bot_cache
        if name or time.time() - checked < 30:
            return name
        try:
            tg = Telegram(settings.telegram_token, settings.telegram_api_base)
            name = tg.call("getMe", _timeout=8).get("username") or ""
        except (TelegramError, httpx.HTTPError) as e:
            log.warning("Could not reach Telegram: %s", e)
            name = ""
        _bot_cache = (time.time(), name)
        return name


# --- schemas ---------------------------------------------------------------

class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=200)


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    emoji: str = Field(default="🤖", max_length=16)
    role: str = Field(default="", max_length=160)
    instructions: str = Field(default="", max_length=4000)
    tools: bool = True


class DraftIn(BaseModel):
    description: str = Field(min_length=4, max_length=600)


class InterviewTurn(BaseModel):
    question: str = Field(max_length=200)
    answer: str = Field(max_length=1000)


class InterviewIn(BaseModel):
    answers: list[InterviewTurn] = Field(min_length=1, max_length=6)


class DocIn(BaseModel):
    title: str = Field(default="", max_length=160)
    text: str = Field(min_length=1, max_length=400_000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=6000)


class MemoryIn(BaseModel):
    text: str = Field(min_length=1, max_length=400)


# --- status & auth ---------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/status")
def status():
    return {
        "llm": llm.status(),
        "telegram": {"configured": bool(settings.telegram_token), "bot": bot_username()},
        "sandbox": {"available": sandbox.available()},
        "firecrawl": {"configured": firecrawl.configured()},
        "signups": settings.allow_signups,
    }


@app.post("/api/auth/signup")
def signup(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    if not settings.allow_signups:
        raise HTTPException(403, "Signups are closed on this server.")
    throttle(f"signup:{client_ip(request)}", limit=30)
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "That doesn't look like an email address.")
    user = User(email=email, password_hash=security.hash_password(body.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An account with that email already exists. Try logging in.")
    # Everyone starts with one agent so Telegram works the moment it's linked.
    db.add(Agent(user_id=user.id, **brain.STARTER))
    db.commit()
    set_session(response, user)
    return {"id": user.id, "email": user.email}


@app.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    # Per account, so guessing one password is slow whatever address it comes from.
    throttle(f"login:{email}", limit=10)
    throttle(f"login-ip:{client_ip(request)}", limit=120)
    user = db.scalar(select(User).where(User.email == email))
    if not user or not security.verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Wrong email or password.")
    set_session(response, user)
    return {"id": user.id, "email": user.email}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(security.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email}


# --- agents ----------------------------------------------------------------

def _validated(body: AgentIn, existing: list[Agent], editing: Agent | None = None) -> dict:
    name = " ".join(clean(body.name).split())
    if not brain.valid_name(name):
        raise HTTPException(422, "Agent names can use letters, numbers, spaces and dashes (max 32).")
    if any(a.name.lower() == name.lower() and a is not editing for a in existing):
        raise HTTPException(409, f"You already have an agent called {name}.")
    return {
        "name": name,
        "emoji": clean(body.emoji).strip() or "🤖",
        "role": " ".join(clean(body.role).split()),
        "instructions": clean(body.instructions).strip(),
        "tools": body.tools,
    }


@app.get("/api/agents")
def list_agents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [agent_json(a) for a in brain.agents_of(db, user.id)]


@app.post("/api/agents")
def create_agent(body: AgentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    existing = brain.agents_of(db, user.id)
    if len(existing) >= settings.max_agents:
        raise HTTPException(409, f"Your crew is full ({settings.max_agents} agents).")
    agent = Agent(user_id=user.id, **_validated(body, existing))
    db.add(agent)
    db.commit()
    return agent_json(agent)


@app.post("/api/agents/draft")
def draft_agent(body: DraftIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    existing = brain.agents_of(db, user.id)
    db.commit()
    try:
        draft = brain.draft_agent(body.description)
    except llm.LLMError as e:
        raise HTTPException(503, str(e))
    draft["name"] = brain.unique_name(existing, draft["name"])
    return draft


@app.post("/api/agents/interview")
def interview_agent(body: InterviewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Build and save an agent from the answers given in the voice interview."""
    if len(clean(body.answers[0].answer).strip()) < 3:
        raise HTTPException(422, "Tell me what the agent should help with first.")
    existing = brain.agents_of(db, user.id)
    if len(existing) >= settings.max_agents:
        raise HTTPException(409, f"Your crew is full ({settings.max_agents} agents).")
    db.commit()
    try:
        draft = brain.draft_from_interview([(t.question, t.answer) for t in body.answers])
    except llm.LLMError as e:
        raise HTTPException(503, str(e))
    draft["name"] = brain.unique_name(existing, draft["name"])
    agent = Agent(user_id=user.id, **draft)
    db.add(agent)
    db.commit()
    return agent_json(agent)


@app.patch("/api/agents/{agent_id}")
def update_agent(body: AgentIn, agent: Agent = Depends(own_agent), db: Session = Depends(get_db)):
    agent = db.merge(agent)
    existing = brain.agents_of(db, agent.user_id)
    current = next(a for a in existing if a.id == agent.id)
    for key, value in _validated(body, existing, editing=current).items():
        setattr(agent, key, value)
    db.commit()
    return agent_json(agent)


@app.delete("/api/agents/{agent_id}")
def remove_agent(agent: Agent = Depends(own_agent), db: Session = Depends(get_db)):
    brain.delete_agent(db, db.merge(agent))
    return {"ok": True}


# --- knowledge -------------------------------------------------------------

@app.get("/api/agents/{agent_id}/docs")
def list_docs(agent: Agent = Depends(own_agent), db: Session = Depends(get_db)):
    docs = db.scalars(select(Doc).where(Doc.agent_id == agent.id).order_by(Doc.id))
    return [{"id": d.id, "title": d.title, "chars": d.chars} for d in docs]


@app.post("/api/agents/{agent_id}/docs")
def add_text(body: DocIn, agent: Agent = Depends(own_agent), db: Session = Depends(get_db)):
    title = body.title.strip() or " ".join(body.text.split())[:60]
    try:
        doc = brain.add_doc(db, agent, title, body.text, settings.max_knowledge_chars)
    except ValueError as e:
        raise HTTPException(413, str(e))
    return {"id": doc.id, "title": doc.title, "chars": doc.chars}


@app.post("/api/agents/{agent_id}/docs/upload")
def upload_doc(file: UploadFile = File(...), agent: Agent = Depends(own_agent), db: Session = Depends(get_db)):
    data = file.file.read(settings.max_doc_bytes + 1)
    if len(data) > settings.max_doc_bytes:
        raise HTTPException(413, f"Files can be up to {settings.max_doc_bytes // 1_000_000} MB.")
    try:
        text = knowledge.extract_text(file.filename or "", data)
        doc = brain.add_doc(db, agent, file.filename or "Upload", text, settings.max_knowledge_chars)
    except knowledge.UnsupportedFile as e:
        raise HTTPException(415, str(e))
    except ValueError as e:
        raise HTTPException(413, str(e))
    return {"id": doc.id, "title": doc.title, "chars": doc.chars}


@app.delete("/api/docs/{doc_id}")
def remove_doc(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = db.get(Doc, doc_id)
    agent = db.get(Agent, doc.agent_id) if doc else None
    if not doc or not agent or agent.user_id != user.id:
        raise HTTPException(404, "Document not found.")
    db.execute(delete(Chunk).where(Chunk.doc_id == doc.id))
    db.delete(doc)
    db.commit()
    return {"ok": True}


# --- test chat in the dashboard ---------------------------------------------

@app.get("/api/agents/{agent_id}/chat")
def chat_history(agent: Agent = Depends(own_agent), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Message).where(Message.chat_key == f"web:{agent.id}").order_by(Message.id.desc()).limit(40)
    )
    return [{"role": m.role, "content": m.content} for m in reversed(list(rows))]


@app.post("/api/agents/{agent_id}/chat")
def chat(body: ChatIn, agent: Agent = Depends(own_agent), user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        reply = brain.respond(db, user, agent, f"web:{agent.id}", body.message, "the web dashboard")
    except llm.LLMError as e:
        raise HTTPException(503, str(e))
    return {"reply": reply.text, "steps": reply.steps}


@app.delete("/api/agents/{agent_id}/chat")
def reset_chat(agent: Agent = Depends(own_agent), db: Session = Depends(get_db)):
    db.execute(delete(Message).where(Message.chat_key == f"web:{agent.id}"))
    db.commit()
    return {"ok": True}


# --- the agent's computer ---------------------------------------------------

@app.get("/api/agents/{agent_id}/sandbox")
def sandbox_state(agent: Agent = Depends(own_agent)):
    return sandbox.state(agent.id)


@app.delete("/api/agents/{agent_id}/sandbox")
def sandbox_reset(agent: Agent = Depends(own_agent)):
    """Wipe the agent's workspace: its files, its running server and its preview link."""
    sandbox.remove(agent.id)
    return {"ok": True}


_preview_http = httpx.Client(timeout=httpx.Timeout(30, connect=3), follow_redirects=False)
_ASLEEP = (
    "<!doctype html><meta charset=utf-8><title>Preview asleep</title>"
    "<body style='font:16px system-ui;background:#09090b;color:#eee;display:grid;place-items:center;height:100vh;margin:0'>"
    "<p>This preview isn't running right now. Ask the agent to start it again.</p>"
)


@app.api_route("/preview/{token}/{path:path}", methods=["GET", "HEAD", "POST"])
async def preview(token: str, path: str, request: Request, db: Session = Depends(get_db)):
    """Serve what an agent's sandbox has on its web port. The token in the URL is the access control."""
    row = db.scalar(select(Sandbox).where(Sandbox.token == token))
    if not row:
        raise HTTPException(404, "No such preview.")
    agent_id = row.agent_id
    db.commit()
    url = f"http://{sandbox.host()}:{sandbox.port(agent_id)}/{path}"
    body = await request.body()

    def fetch():
        return _preview_http.request(
            request.method, url, params=request.query_params, content=body or None,
            headers={k: v for k, v in request.headers.items() if k in ("accept", "content-type", "range", "user-agent")},
        )

    try:
        upstream = await run_in_threadpool(fetch)
    except httpx.HTTPError:
        return HTMLResponse(_ASLEEP, status_code=502)
    headers = {k: v for k, v in upstream.headers.items() if k.lower() in ("content-type", "location", "cache-control", "content-range", "accept-ranges")}
    location = next((v for k, v in headers.items() if k.lower() == "location"), "")
    if location.startswith("/"):  # keep the server's own redirects inside this preview
        headers = {k: v for k, v in headers.items() if k.lower() != "location"} | {"Location": f"/p/{token}{location}"}
    # A preview is untrusted content. `sandbox` gives it an opaque origin, so
    # scripts in it can never call this app's API as the person viewing it.
    headers["Content-Security-Policy"] = "sandbox allow-scripts allow-forms allow-popups allow-modals"
    headers["X-Robots-Tag"] = "noindex"
    return Response(upstream.content, status_code=upstream.status_code, headers=headers)


# --- shared memory ----------------------------------------------------------

@app.get("/api/memory")
def list_memory(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Memory).where(Memory.user_id == user.id).order_by(Memory.id))
    return [{"id": m.id, "text": m.text} for m in rows]


@app.post("/api/memory")
def add_memory(body: MemoryIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if len(list(db.scalars(select(Memory.id).where(Memory.user_id == user.id)))) >= 100:
        raise HTTPException(409, "Shared memory is full (100 facts). Remove some first.")
    row = Memory(user_id=user.id, text=clean(body.text).strip())
    db.add(row)
    db.commit()
    return {"id": row.id, "text": row.text}


@app.delete("/api/memory/{memory_id}")
def remove_memory(memory_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(Memory, memory_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "Not found.")
    db.delete(row)
    db.commit()
    return {"ok": True}


# --- telegram ---------------------------------------------------------------

@app.get("/api/telegram")
def telegram_state(user: User = Depends(current_user), db: Session = Depends(get_db)):
    links = db.scalars(select(TelegramLink).where(TelegramLink.user_id == user.id).order_by(TelegramLink.id))
    return {
        "configured": bool(settings.telegram_token),
        "bot": bot_username(),
        "links": [{"id": l.id, "display": l.display} for l in links],
    }


@app.post("/api/telegram/link")
def telegram_link(user: User = Depends(current_user), db: Session = Depends(get_db)):
    bot = bot_username()
    if not bot:
        raise HTTPException(503, "Telegram isn't set up on this server yet (TELEGRAM_BOT_TOKEN).")
    db.execute(delete(LinkToken).where(LinkToken.user_id == user.id))
    token = secrets.token_urlsafe(24)
    db.add(LinkToken(token=token, user_id=user.id, expires_at=now() + timedelta(minutes=LINK_TOKEN_MINUTES)))
    db.commit()
    url = f"https://t.me/{bot}?start={token}"
    qr = segno.make(url, error="m").svg_inline(omitsize=True, border=2, dark="#111827", light="#ffffff")
    return {"url": url, "qr": qr, "minutes": LINK_TOKEN_MINUTES}


@app.delete("/api/telegram/links/{link_id}")
def telegram_unlink(link_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    link = db.get(TelegramLink, link_id)
    if not link or link.user_id != user.id:
        raise HTTPException(404, "Not found.")
    db.execute(delete(Message).where(Message.chat_key == f"tg:{link.chat_id}"))
    db.delete(link)
    db.commit()
    return {"ok": True}


# --- google calendar --------------------------------------------------------

class CalendarPreviewRequest(BaseModel):
    message: str
    client_timezone: str | None = None


class CalendarEventModel(BaseModel):
    title: str
    start: str
    end: str
    timezone: str = "Asia/Kolkata"
    description: str = ""
    location: str = ""
    attendees: list[str] = Field(default_factory=list)


class CalendarConfirmRequest(BaseModel):
    event: CalendarEventModel


class TimezoneUpdateRequest(BaseModel):
    timezone: str


@app.get("/api/calendar/status")
def calendar_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {
        "configured": bool(settings.google_client_id and settings.google_client_secret),
        "connected": calendar_agent.is_connected(db, user.id),
        "timezone": user.timezone or settings.default_timezone or "Asia/Kolkata",
    }


@app.get("/api/calendar/auth")
def calendar_auth(request: Request, user: User = Depends(current_user)):
    if not settings.google_client_id or not settings.google_client_secret:
        return {
            "configured": False,
            "error": "Google Calendar OAuth is not configured on this server (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET).",
        }
    redirect_uri = settings.google_redirect_uri
    try:
        url = calendar_agent.get_authorization_url(user.id, redirect_uri=redirect_uri)
        return {"configured": True, "url": url}
    except calendar_agent.CalendarError as e:
        raise HTTPException(500, str(e))


@app.get("/api/calendar/callback")
def calendar_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    dashboard_url = "/dashboard"
    if error:
        return RedirectResponse(f"{dashboard_url}?calendar_error={error}")
    if not code or not state:
        return RedirectResponse(f"{dashboard_url}?calendar_error=missing_code_or_state")
    try:
        redirect_uri = settings.google_redirect_uri
        calendar_agent.handle_oauth_callback(code, state, db, redirect_uri=redirect_uri)
        return RedirectResponse(f"{dashboard_url}?calendar=connected")
    except calendar_agent.CalendarError as e:
        log.warning("Calendar callback error: %s", e)
        return RedirectResponse(f"{dashboard_url}?calendar_error={str(e)}")


@app.post("/api/calendar/disconnect")
def calendar_disconnect(user: User = Depends(current_user), db: Session = Depends(get_db)):
    calendar_agent.disconnect_calendar(db, user.id)
    return {"ok": True}


@app.post("/api/calendar/preview")
def calendar_preview(
    body: CalendarPreviewRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    tz = calendar_agent.resolve_timezone(user, body.client_timezone)
    intent = calendar_agent.parse_intent(body.message, tz)
    action = intent.get("action")

    if action == "create_calendar_event":
        # Pure preview — DOES NOT create event in Google Calendar
        return {"action": "create_calendar_event", "event": intent}
    elif action == "list_calendar_events":
        if not calendar_agent.is_connected(db, user.id):
            return {
                "action": "list_calendar_events",
                "connected": False,
                "events": [],
                "message": "Google Calendar is not connected.",
            }
        try:
            service = calendar_agent.get_calendar_service(db, user.id)
            events = calendar_agent.list_upcoming_events(
                service,
                time_min=intent.get("time_min"),
                time_max=intent.get("time_max"),
            )
            return {"action": "list_calendar_events", "connected": True, "events": events}
        except calendar_agent.CalendarError as e:
            return {"action": "list_calendar_events", "connected": False, "events": [], "error": str(e)}
    else:
        return {
            "action": "clarification_needed",
            "question": intent.get("question", "Could you please clarify your request?"),
        }


@app.post("/api/calendar/confirm")
def calendar_confirm(
    body: CalendarConfirmRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not calendar_agent.is_connected(db, user.id):
        raise HTTPException(400, "Google Calendar is not connected. Please connect your calendar first.")

    try:
        service = calendar_agent.get_calendar_service(db, user.id)
        ev = body.event
        res = calendar_agent.create_calendar_event(
            service=service,
            title=ev.title,
            start=ev.start,
            end=ev.end,
            timezone=ev.timezone,
            description=ev.description,
            location=ev.location,
            attendees=ev.attendees,
        )
        return res
    except calendar_agent.CalendarError as e:
        raise HTTPException(400, str(e))


@app.get("/api/calendar/events")
def calendar_events(
    time_min: str | None = None,
    time_max: str | None = None,
    max_results: int = 10,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not calendar_agent.is_connected(db, user.id):
        return {"connected": False, "events": []}
    try:
        service = calendar_agent.get_calendar_service(db, user.id)
        events = calendar_agent.list_upcoming_events(
            service,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
        )
        return {"connected": True, "events": events}
    except calendar_agent.CalendarError as e:
        raise HTTPException(400, str(e))


@app.post("/api/calendar/timezone")
def update_user_timezone(
    body: TimezoneUpdateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not calendar_agent.is_valid_timezone(body.timezone):
        raise HTTPException(400, "Invalid timezone name.")
    user.timezone = body.timezone
    db.commit()
    return {"ok": True, "timezone": user.timezone}


# --- whatsapp ----------------------------------------------------------------

from concurrent.futures import ThreadPoolExecutor as _WaPool

_wa_pool = _WaPool(max_workers=8)


class WhatsAppSchedulePreviewRequest(BaseModel):
    message: str
    client_timezone: str | None = None


class WhatsAppScheduleConfirmRequest(BaseModel):
    recipient: str
    message: str
    scheduled_at: str          # ISO 8601 with offset
    timezone: str = "Asia/Kolkata"
    recurrence: str | None = None
    recipient_phone: str | None = None


@app.get("/api/whatsapp/status")
def whatsapp_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    links = db.query(WhatsAppLink).filter(WhatsAppLink.user_id == user.id).all()
    return {
        "configured": whatsapp_agent.is_configured(),
        "phone_number_id": settings.whatsapp_phone_number_id or None,
        "multi_agent": settings.whatsapp_multi_agent,
        "linked_phones": [{"id": lnk.id, "phone": lnk.phone, "display": lnk.display} for lnk in links],
    }


@app.get("/api/whatsapp/webhook")
def whatsapp_webhook_verify(request: Request):
    """Meta calls this to verify the webhook URL.

    Meta sends query parameters with dots (hub.mode, hub.challenge, hub.verify_token)
    which cannot be mapped to Python identifiers; we read them from the raw query string.
    """
    qp = request.query_params
    hub_mode = qp.get("hub.mode") or qp.get("hub_mode", "")
    hub_challenge = qp.get("hub.challenge") or qp.get("hub_challenge", "")
    hub_verify_token = qp.get("hub.verify_token") or qp.get("hub_verify_token", "")
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return int(hub_challenge) if hub_challenge.isdigit() else hub_challenge
    raise HTTPException(403, "Webhook verification failed.")


@app.post("/api/whatsapp/webhook")
async def whatsapp_webhook_receive(request: Request):
    """Receive incoming messages and status updates from Meta."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON payload.")

    for entry in payload.get("entry", []):
        events = whatsapp_agent.parse_webhook_entry(entry)
        for event in events:
            if event["type"] == "message":
                _wa_pool.submit(whatsapp_agent.handle_incoming, event)

    return {"ok": True}


@app.post("/api/whatsapp/link")
def whatsapp_link_phone(
    phone: str,
    display: str = "",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Associate a WhatsApp phone number with the authenticated user account."""
    phone = phone.strip()
    if not phone:
        raise HTTPException(400, "Phone number is required.")
    existing = db.query(WhatsAppLink).filter(WhatsAppLink.phone == phone).first()
    if existing and existing.user_id != user.id:
        raise HTTPException(409, "That number is already linked to another account.")
    if existing:
        existing.display = display or existing.display
        db.commit()
        return {"ok": True, "id": existing.id}
    link_row = WhatsAppLink(user_id=user.id, phone=phone, display=display or phone)
    db.add(link_row)
    db.commit()
    return {"ok": True, "id": link_row.id}


@app.delete("/api/whatsapp/link/{link_id}")
def whatsapp_unlink(link_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    link_row = db.get(WhatsAppLink, link_id)
    if not link_row or link_row.user_id != user.id:
        raise HTTPException(404, "Not found.")
    db.delete(link_row)
    db.commit()
    return {"ok": True}


# --- whatsapp scheduled messages ---

@app.get("/api/whatsapp/scheduled")
def whatsapp_scheduled_list(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = whatsapp_scheduler.list_schedules(db, user.id)
    return {"schedules": [whatsapp_scheduler.schedule_to_dict(r) for r in rows]}


@app.post("/api/whatsapp/scheduled/preview")
def whatsapp_scheduled_preview(
    body: WhatsAppSchedulePreviewRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    tz = whatsapp_agent.resolve_timezone(user, body.client_timezone)
    intent = whatsapp_agent.parse_schedule_intent(body.message, tz)

    if intent.get("needs_clarification"):
        return {
            "action": "clarification_needed",
            "question": intent.get("clarification_question", "Could you clarify the details?"),
        }

    return {
        "action": "schedule_whatsapp_message",
        "preview": intent,
        "preview_text": whatsapp_agent.format_preview_text(intent),
    }


@app.post("/api/whatsapp/scheduled/confirm")
def whatsapp_scheduled_confirm(
    body: WhatsAppScheduleConfirmRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    intent = {
        "recipient": body.recipient,
        "recipient_phone": body.recipient_phone,
        "message": body.message,
        "scheduled_at": body.scheduled_at,
        "timezone": body.timezone,
        "recurrence": body.recurrence,
    }
    row = whatsapp_scheduler.create_schedule(db, user.id, intent)
    return whatsapp_scheduler.schedule_to_dict(row)


@app.delete("/api/whatsapp/scheduled/{schedule_id}")
def whatsapp_scheduled_cancel(
    schedule_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ok = whatsapp_scheduler.cancel_schedule(db, schedule_id, user.id)
    if not ok:
        raise HTTPException(404, "Schedule not found or already completed.")
    return {"ok": True}
