"""WhatsApp Cloud API (Meta) integration for HiveMind.

Handles:
- Sending text and document messages via the official Meta WhatsApp Cloud API.
- Processing incoming webhook payloads (message, status, document).
- Linking/unlinking WhatsApp phone numbers to Hivemind accounts.
- Natural-language scheduling intent parsing (for the scheduler).

Architecture
------------
Incoming message → webhook → whatsapp_agent → brain.respond → send reply
Scheduled send   → whatsapp_scheduler.tick → whatsapp_agent.send_text
"""
import json
import logging
import re
from datetime import datetime, timezone as dt_tz
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from . import brain, knowledge, llm
from .config import settings
from .db import Agent, Message, User, WhatsAppLink, WhatsAppSchedule, SessionLocal, clean, now

log = logging.getLogger("hivemind.whatsapp")

# ── Constants ────────────────────────────────────────────────────────────────

GRAPH_BASE = "https://graph.facebook.com"
SUPPORTED_DOC_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class WhatsAppError(Exception):
    """Raised when the Cloud API returns an error or credentials are missing."""


# ── Low-level Cloud API calls ─────────────────────────────────────────────────

def _api_url() -> str:
    if not settings.whatsapp_phone_number_id:
        raise WhatsAppError("WHATSAPP_PHONE_NUMBER_ID is not configured.")
    return (
        f"{GRAPH_BASE}/{settings.whatsapp_api_version}"
        f"/{settings.whatsapp_phone_number_id}/messages"
    )


def _headers() -> dict:
    if not settings.whatsapp_access_token:
        raise WhatsAppError("WHATSAPP_ACCESS_TOKEN is not configured.")
    return {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }


def _post(payload: dict) -> dict:
    """POST to the Cloud API. Raises WhatsAppError on any API error."""
    try:
        r = httpx.post(_api_url(), json=payload, headers=_headers(), timeout=30)
    except httpx.HTTPError as e:
        raise WhatsAppError(f"Network error sending WhatsApp message: {e}") from e

    if r.status_code == 200:
        return r.json()

    # Handle known WhatsApp error codes
    try:
        data = r.json()
        err = data.get("error", {})
        code = err.get("code", r.status_code)
        message = err.get("message", r.text)

        # 131047 = message outside 24-hour window (template required)
        if code == 131047 or "template" in message.lower():
            raise WhatsAppError(
                f"template_required: WhatsApp requires an approved message template for this delivery. "
                f"({message})"
            )
        raise WhatsAppError(f"WhatsApp API error {code}: {message}")
    except json.JSONDecodeError:
        raise WhatsAppError(f"WhatsApp API HTTP {r.status_code}: {r.text[:400]}")


def send_text(to: str, text: str) -> dict:
    """Send a plain text message to a WhatsApp phone number (E.164 format)."""
    if not to.startswith("+"):
        to = "+" + to.lstrip("+")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text[:4096]},
    }
    return _post(payload)


def mark_read(message_id: str) -> None:
    """Mark an incoming message as read (sends a read receipt)."""
    try:
        _post({
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        })
    except WhatsAppError:
        pass  # Non-critical


def is_configured() -> bool:
    return bool(settings.whatsapp_access_token and settings.whatsapp_phone_number_id)


# ── Webhook parsing ──────────────────────────────────────────────────────────

def parse_webhook_entry(entry: dict) -> list[dict]:
    """Flatten a webhook entry into a list of individual event dicts.

    Each dict has:
      - type: "message" | "status"
      - phone: sender E.164 (messages only)
      - text: message text (messages only)
      - message_id: WhatsApp message ID
      - media_type: "document" etc (messages only, if any)
      - media_id: Cloud API media ID (messages only, if any)
      - filename: document file name (messages only, if any)
      - mime_type: document MIME type (messages only, if any)
      - status: "read"|"delivered"|"sent"|"failed" (status only)
      - wa_id: phone in status events
    """
    events = []
    for change in entry.get("changes", []):
        value = change.get("value", {})
        for msg in value.get("messages", []):
            phone = msg.get("from", "")
            msg_id = msg.get("id", "")
            msg_type = msg.get("type", "text")
            text = ""
            media_id = None
            filename = None
            mime_type = None
            media_type = None

            if msg_type == "text":
                text = msg.get("text", {}).get("body", "")
            elif msg_type == "document":
                doc = msg.get("document", {})
                media_id = doc.get("id")
                filename = doc.get("filename", "file")
                mime_type = doc.get("mime_type", "")
                media_type = "document"
                # Caption if any
                text = doc.get("caption", "")
            elif msg_type == "interactive":
                # Handle button replies from inline keyboards
                interactive = msg.get("interactive", {})
                if interactive.get("type") == "button_reply":
                    text = interactive["button_reply"].get("title", "")
                elif interactive.get("type") == "list_reply":
                    text = interactive["list_reply"].get("title", "")

            events.append({
                "type": "message",
                "phone": phone,
                "text": clean(text).strip(),
                "message_id": msg_id,
                "media_type": media_type,
                "media_id": media_id,
                "filename": filename,
                "mime_type": mime_type,
            })

        for status_obj in value.get("statuses", []):
            events.append({
                "type": "status",
                "wa_id": status_obj.get("recipient_id", ""),
                "message_id": status_obj.get("id", ""),
                "status": status_obj.get("status", ""),
            })

    return events


# ── Download document from Cloud API ────────────────────────────────────────

def download_media(media_id: str, max_bytes: int = 10_000_000) -> bytes:
    """Download a media file from the Cloud API by media ID."""
    # Step 1: retrieve the download URL
    try:
        r = httpx.get(
            f"{GRAPH_BASE}/{settings.whatsapp_api_version}/{media_id}",
            headers=_headers(),
            timeout=20,
        )
    except httpx.HTTPError as e:
        raise WhatsAppError(f"Could not retrieve media URL: {e}") from e
    if not r.is_success:
        raise WhatsAppError(f"Media metadata error {r.status_code}: {r.text[:200]}")
    url = r.json().get("url")
    if not url:
        raise WhatsAppError("No URL in media metadata response.")

    # Step 2: download the file
    try:
        r2 = httpx.get(url, headers=_headers(), timeout=60, follow_redirects=True)
    except httpx.HTTPError as e:
        raise WhatsAppError(f"Could not download media: {e}") from e
    if not r2.is_success:
        raise WhatsAppError(f"Media download error {r2.status_code}")
    if len(r2.content) > max_bytes:
        raise WhatsAppError(f"Media file too large (>{max_bytes // 1_000_000} MB).")
    return r2.content


# ── Incoming message handler ─────────────────────────────────────────────────

def handle_incoming(event: dict) -> None:
    """Process a single parsed 'message' event from the webhook.

    Runs in a background thread — must open its own DB session.
    """
    phone = event["phone"]
    text = event["text"]
    message_id = event["message_id"]
    media_type = event["media_type"]

    with SessionLocal() as db:
        link = db.query(WhatsAppLink).filter(WhatsAppLink.phone == phone).first()
        if not link:
            # Unknown number — prompt them to link via the dashboard
            try:
                send_text(
                    phone,
                    f"👋 This number isn't linked to a HiveMind account yet.\n\n"
                    f"Sign up at {settings.public_url} and tap *Connect WhatsApp* to link it.",
                )
            except WhatsAppError as e:
                log.warning("Could not reply to unknown number %s: %s", phone, e)
            return

        try:
            mark_read(message_id)
        except Exception:
            pass

        user = db.get(User, link.user_id)
        if not user:
            log.warning("WhatsApp link %s has no matching user", link.id)
            return

        if media_type == "document":
            _handle_document(db, link, user, event)
            return

        if not text:
            try:
                send_text(phone, "I can read text and document files (.txt, .md, .pdf) right now.")
            except WhatsAppError:
                pass
            return

        # Check if the user is confirming a scheduled message
        lower = text.lower().strip()
        if lower in ("confirm", "yes", "✓", "ok", "okay"):
            _try_confirm_pending_schedule(db, link, user, phone)
            return

        if lower in ("cancel", "no", "stop"):
            _try_cancel_pending_schedule(db, link, user, phone)
            return

        agents = brain.agents_of(db, user.id)
        if not agents:
            send_text(phone, "Your crew is empty. Go to the dashboard and hire an agent first.")
            return

        chat_key = f"wa:{phone}"
        mention = brain.match_mention(agents, text)
        pinned = next((a for a in agents if a.id == link.active_agent_id), None)

        if mention:
            agent, text = mention
        elif pinned:
            agent = pinned
        elif settings.whatsapp_multi_agent and len(agents) > 1:
            agent = brain.route(agents, text, link.last_agent_id)
        else:
            agent = agents[0]

        try:
            reply = brain.respond(db, user, agent, chat_key, text, "WhatsApp")
        except llm.LLMError as e:
            send_text(phone, f"⚠️ {e}")
            return

        link.last_agent_id = agent.id
        db.commit()

        prefix = f"*{agent.emoji} {agent.name}*\n" if len(agents) > 1 else ""
        send_text(phone, prefix + reply.text)


def _handle_document(db: "Session", link: WhatsAppLink, user: User, event: dict) -> None:
    from . import knowledge
    phone = event["phone"]
    filename = event.get("filename") or "file"
    mime_type = event.get("mime_type") or ""
    media_id = event.get("media_id")
    caption = event.get("text") or ""

    if mime_type not in SUPPORTED_DOC_MIME_TYPES:
        send_text(phone, f"I can only read .pdf, .txt, and .md files (got: {mime_type}).")
        return

    agents = brain.agents_of(db, user.id)
    if not agents:
        send_text(phone, "Create an agent on the dashboard first, then resend the file.")
        return

    mention = brain.match_mention(agents, caption) if caption else None
    agent = (
        mention[0] if mention
        else next((a for a in agents if a.id == link.active_agent_id), None)
        or next((a for a in agents if a.id == link.last_agent_id), None)
        or agents[0]
    )

    send_text(phone, f"📖 Reading {filename}…")
    try:
        data = download_media(media_id, settings.max_doc_bytes)
        text = knowledge.extract_text(filename, data)
        brain.add_doc(db, agent, filename, text, settings.max_knowledge_chars)
    except (WhatsAppError, knowledge.UnsupportedFile, ValueError) as e:
        db.rollback()
        send_text(phone, f"⚠️ {e}")
        return

    send_text(phone, f"📚 *{agent.emoji} {agent.name}* has read {filename} and can now answer from it.")


# ── Pending schedule flow (WhatsApp-chat confirmation) ────────────────────────

# We store the "pending" schedule (awaiting confirmation) in the Message table
# with a special chat_key prefix so it doesn't pollute conversation history.
_PENDING_KEY_PREFIX = "wa_pending:"


def set_pending_schedule(user_id: int, phone: str, intent: dict) -> None:
    """Store a pending schedule intent in the DB under the user's phone key."""
    with SessionLocal() as db:
        chat_key = f"{_PENDING_KEY_PREFIX}{phone}"
        # Remove any previous pending entry for this user/phone
        db.query(Message).filter(
            Message.user_id == user_id,
            Message.chat_key == chat_key,
        ).delete()
        db.add(Message(
            user_id=user_id,
            chat_key=chat_key,
            role="system",
            content=json.dumps(intent),
        ))
        db.commit()


def get_pending_schedule(user_id: int, phone: str) -> dict | None:
    """Retrieve a pending schedule intent from the DB."""
    with SessionLocal() as db:
        chat_key = f"{_PENDING_KEY_PREFIX}{phone}"
        row = db.query(Message).filter(
            Message.user_id == user_id,
            Message.chat_key == chat_key,
            Message.role == "system",
        ).order_by(Message.id.desc()).first()
        if row:
            try:
                return json.loads(row.content)
            except json.JSONDecodeError:
                return None
    return None


def clear_pending_schedule(user_id: int, phone: str) -> None:
    with SessionLocal() as db:
        chat_key = f"{_PENDING_KEY_PREFIX}{phone}"
        db.query(Message).filter(
            Message.user_id == user_id,
            Message.chat_key == chat_key,
        ).delete()
        db.commit()


def _try_confirm_pending_schedule(db: "Session", link: WhatsAppLink, user: User, phone: str) -> None:
    intent = get_pending_schedule(user.id, phone)
    if not intent:
        send_text(phone, "No pending scheduled message to confirm.")
        return
    from .whatsapp_scheduler import create_schedule
    try:
        schedule = create_schedule(db, user.id, intent)
    except Exception as e:
        log.warning("Could not create schedule for user %s: %s", user.id, e)
        send_text(phone, f"⚠️ Could not schedule: {e}")
        return
    clear_pending_schedule(user.id, phone)
    tz = intent.get("timezone", "Asia/Kolkata")
    try:
        local_dt = schedule.scheduled_at.replace(tzinfo=dt_tz.utc).astimezone(ZoneInfo(tz))
        formatted = local_dt.strftime("%A, %d %b · %I:%M %p")
    except Exception:
        formatted = str(schedule.scheduled_at)
    send_text(phone, f"✅ Scheduled.\n\nNext delivery:\n{formatted} ({tz})")


def _try_cancel_pending_schedule(db: "Session", link: WhatsAppLink, user: User, phone: str) -> None:
    intent = get_pending_schedule(user.id, phone)
    if intent:
        clear_pending_schedule(user.id, phone)
        send_text(phone, "❌ Cancelled. The message was not scheduled.")
    else:
        send_text(phone, "No pending message to cancel.")


# ── Timezone resolution (reused from calendar_agent style) ───────────────────

def resolve_timezone(user: User | None, client_tz: str | None = None) -> str:
    from .calendar_agent import is_valid_timezone
    if user and user.timezone and is_valid_timezone(user.timezone):
        return user.timezone
    if client_tz and is_valid_timezone(client_tz):
        return client_tz
    if settings.default_timezone and is_valid_timezone(settings.default_timezone):
        return settings.default_timezone
    return "Asia/Kolkata"


# ── NL → Schedule Intent via Gemma ──────────────────────────────────────────

SCHEDULE_SYSTEM_PROMPT = """You are the HiveMind WhatsApp Scheduling Agent.

Convert a user's natural-language message into a strict JSON scheduling intent.
DO NOT output anything outside the JSON object.

Current reference time:
- ISO: {current_time_iso}
- Day: {current_weekday}
- Timezone: {timezone}

SCHEMA (output ONLY this):

For scheduling a message:
{{
  "action": "schedule_whatsapp_message",
  "recipient": "<name or phone>",
  "message": "<the message text to send>",
  "scheduled_at": "<ISO 8601 with timezone offset e.g. 2026-10-08T09:00:00+05:30>",
  "timezone": "{timezone}",
  "recurrence": null,
  "needs_clarification": false,
  "clarification_question": null
}}

For recurring messages, set recurrence to "daily" or "weekly".

RULES:
- If the recipient is missing, set needs_clarification=true and ask for it.
- If the message body is missing or unclear, set needs_clarification=true and ask for it.
- If the time is missing or vague ("tomorrow", "evening"), set needs_clarification=true and ask for the exact time.
- If the date is missing ("send Rahul Happy Birthday"), set needs_clarification=true and ask for the date.
- Do NOT guess. Always ask for missing required fields.
- Default duration for recurrence: keep same time each period.
- For "every Monday" → recurrence="weekly", scheduled_at = next Monday at the given time.
- For "every day" → recurrence="daily".

OUTPUT ONLY RAW JSON. No markdown code fences.
"""


def parse_schedule_intent(message: str, timezone_str: str, now_dt: datetime | None = None) -> dict[str, Any]:
    """Use Gemma to parse a natural-language scheduling request into structured intent."""
    if not message.strip():
        return {
            "action": "schedule_whatsapp_message",
            "needs_clarification": True,
            "clarification_question": "What message would you like to schedule?",
        }

    try:
        tz = ZoneInfo(timezone_str)
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")
        timezone_str = "Asia/Kolkata"

    local_now = now_dt or datetime.now(tz)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=tz)

    system = SCHEDULE_SYSTEM_PROMPT.format(
        current_time_iso=local_now.isoformat(),
        current_weekday=local_now.strftime("%A"),
        timezone=timezone_str,
    )

    try:
        raw = llm.chat(system, [{"role": "user", "content": message}], temperature=0.1, max_tokens=600)
    except llm.LLMError as e:
        log.warning("LLM error parsing schedule intent: %s", e)
        return {
            "action": "schedule_whatsapp_message",
            "needs_clarification": True,
            "clarification_question": f"Could not parse your request: {e}",
        }

    intent = _extract_json(raw)
    if intent is None:
        return {
            "action": "schedule_whatsapp_message",
            "needs_clarification": True,
            "clarification_question": "Could you please clarify the recipient, message, and time?",
        }

    # Server-side validation
    if intent.get("needs_clarification"):
        return intent

    recipient = (intent.get("recipient") or "").strip()
    if not recipient:
        intent["needs_clarification"] = True
        intent["clarification_question"] = "Who should I send this message to?"
        return intent

    msg_body = (intent.get("message") or "").strip()
    if not msg_body:
        intent["needs_clarification"] = True
        intent["clarification_question"] = f"What message should I send to {recipient}?"
        return intent

    scheduled_at_str = intent.get("scheduled_at") or ""
    if not scheduled_at_str:
        intent["needs_clarification"] = True
        intent["clarification_question"] = f"What time should I send the message to {recipient}?"
        return intent

    try:
        datetime.fromisoformat(scheduled_at_str)
    except (ValueError, TypeError):
        intent["needs_clarification"] = True
        intent["clarification_question"] = "Could you clarify the exact date and time for the message?"
        return intent

    recurrence = intent.get("recurrence")
    if recurrence and recurrence not in ("daily", "weekly"):
        intent["recurrence"] = None

    intent["action"] = "schedule_whatsapp_message"
    intent["recipient"] = recipient
    intent["message"] = msg_body
    intent["timezone"] = timezone_str
    intent["needs_clarification"] = False
    return intent


def _extract_json(raw: str) -> dict | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ── Format preview card text ─────────────────────────────────────────────────

def format_preview_text(intent: dict) -> str:
    """Format the scheduling preview as a WhatsApp message."""
    tz_str = intent.get("timezone", "Asia/Kolkata")
    try:
        dt = datetime.fromisoformat(intent["scheduled_at"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_tz.utc)
        local_dt = dt.astimezone(ZoneInfo(tz_str))
        time_str = local_dt.strftime("%A, %d %b · %I:%M %p")
    except Exception:
        time_str = intent.get("scheduled_at", "")

    recurrence = intent.get("recurrence")
    recurrence_line = ""
    if recurrence == "daily":
        recurrence_line = "\n🔁 Repeats: Daily"
    elif recurrence == "weekly":
        recurrence_line = "\n🔁 Repeats: Weekly"

    return (
        f"📅 *SCHEDULE WHATSAPP MESSAGE*\n\n"
        f"👤 {intent['recipient']}\n\n"
        f"💬 {intent['message']}\n\n"
        f"🕘 {time_str}\n"
        f"🌍 {tz_str}"
        f"{recurrence_line}\n\n"
        f"Reply *CONFIRM* to send or *CANCEL* to discard."
    )
