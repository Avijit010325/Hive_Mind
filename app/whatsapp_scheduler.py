"""WhatsApp Message Scheduler for HiveMind.

Responsibilities
----------------
1.  Persist scheduled messages in the ``whatsapp_schedules`` table (DB-backed,
    survives restarts).
2.  Run a background ticker that:
      - Picks up ``scheduled`` tasks whose ``scheduled_at <= now(UTC)``.
      - Marks each task ``sending`` (idempotency guard) before attempting.
      - Calls whatsapp_agent.send_text.
      - Marks successful tasks ``sent`` and calculates the next occurrence
        for recurring tasks.
      - Marks failed tasks ``failed`` (or ``template_required`` when Meta
        rejects with a 24-h window error).
3.  Expose helpers used by main.py (CRUD + preview).
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone as dt_tz
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import whatsapp_agent
from .config import settings
from .db import SessionLocal, User, WhatsAppLink, WhatsAppSchedule, now

log = logging.getLogger("hivemind.whatsapp_scheduler")

# ── CRUD helpers ─────────────────────────────────────────────────────────────

def create_schedule(db: Session, user_id: int, intent: dict) -> WhatsAppSchedule:
    """Persist a confirmed scheduling intent.

    ``scheduled_at`` is stored as naive UTC (DB convention).
    """
    scheduled_at_str = intent["scheduled_at"]
    dt = datetime.fromisoformat(scheduled_at_str)
    if dt.tzinfo is not None:
        dt = dt.astimezone(dt_tz.utc).replace(tzinfo=None)

    row = WhatsAppSchedule(
        user_id=user_id,
        recipient=intent["recipient"],
        recipient_phone=intent.get("recipient_phone"),
        message=intent["message"],
        scheduled_at=dt,
        timezone=intent.get("timezone", "Asia/Kolkata"),
        recurrence=intent.get("recurrence"),
        status="scheduled",
    )
    db.add(row)
    db.commit()
    return row


def list_schedules(db: Session, user_id: int) -> list[WhatsAppSchedule]:
    """Return all non-cancelled, non-sent schedules for a user."""
    return list(
        db.scalars(
            select(WhatsAppSchedule)
            .where(
                WhatsAppSchedule.user_id == user_id,
                WhatsAppSchedule.status.in_(["scheduled", "sending", "failed"]),
            )
            .order_by(WhatsAppSchedule.scheduled_at)
        )
    )


def cancel_schedule(db: Session, schedule_id: int, user_id: int) -> bool:
    """Cancel a schedule. Returns True if found and owned, False otherwise."""
    row = db.get(WhatsAppSchedule, schedule_id)
    if not row or row.user_id != user_id:
        return False
    if row.status in ("sent", "cancelled"):
        return False
    row.status = "cancelled"
    db.commit()
    return True


def schedule_to_dict(row: WhatsAppSchedule) -> dict:
    """Serialize a WhatsAppSchedule to a JSON-safe dict."""
    tz_str = row.timezone or "Asia/Kolkata"
    try:
        local_dt = row.scheduled_at.replace(tzinfo=dt_tz.utc).astimezone(ZoneInfo(tz_str))
        formatted = local_dt.strftime("%A, %d %b · %I:%M %p")
    except Exception:
        formatted = str(row.scheduled_at)
    return {
        "id": row.id,
        "recipient": row.recipient,
        "message": row.message,
        "scheduled_at": row.scheduled_at.isoformat() + "Z",
        "scheduled_at_local": formatted,
        "timezone": tz_str,
        "recurrence": row.recurrence,
        "status": row.status,
        "sent_at": row.sent_at.isoformat() + "Z" if row.sent_at else None,
        "created_at": row.created_at.isoformat() + "Z",
    }


# ── Next-occurrence calculation ───────────────────────────────────────────────

def _next_occurrence(current_utc: datetime, recurrence: str | None) -> datetime | None:
    if recurrence == "daily":
        return current_utc + timedelta(days=1)
    if recurrence == "weekly":
        return current_utc + timedelta(weeks=1)
    return None


# ── Phone resolution ──────────────────────────────────────────────────────────

def _resolve_phone(db: Session, user_id: int, recipient: str) -> str | None:
    """Try to find a phone number for ``recipient``.

    Lookup order:
    1. Already stored on the row (recipient_phone column).
    2. WhatsApp links for this user whose ``display`` matches the recipient name.
    3. If recipient looks like a phone number itself, use it directly.
    """
    # Direct phone in E.164 or numeric
    cleaned = re.sub(r"[^0-9+]", "", recipient)
    if len(cleaned) >= 7:
        return cleaned if cleaned.startswith("+") else "+" + cleaned

    # Look in whatsapp_links display names
    links = db.scalars(select(WhatsAppLink).where(WhatsAppLink.user_id == user_id))
    for link in links:
        if link.display.lower() == recipient.lower():
            return link.phone

    return None


# ── Tick: find and send due messages ─────────────────────────────────────────

def _send_due(db: Session) -> None:
    """Find all due scheduled messages and attempt to send them."""
    utc_now = datetime.now(dt_tz.utc).replace(tzinfo=None)
    due = list(
        db.scalars(
            select(WhatsAppSchedule).where(
                WhatsAppSchedule.status == "scheduled",
                WhatsAppSchedule.scheduled_at <= utc_now,
            )
        )
    )
    for row in due:
        _attempt_send(db, row)


def _attempt_send(db: Session, row: WhatsAppSchedule) -> None:
    """Attempt a single scheduled send with idempotency guard."""
    # Idempotency: mark as 'sending' before attempting
    row.status = "sending"
    row.last_attempt_at = now()
    db.commit()

    # Resolve phone
    phone = row.recipient_phone or _resolve_phone(db, row.user_id, row.recipient)
    if not phone:
        log.warning(
            "Schedule %d: cannot resolve phone for recipient %r", row.id, row.recipient
        )
        row.status = "failed"
        row.error = f"No phone number found for recipient: {row.recipient!r}"
        db.commit()
        return

    try:
        whatsapp_agent.send_text(phone, row.message)
    except whatsapp_agent.WhatsAppError as e:
        log.warning("Schedule %d send failed: %s", row.id, e)
        err_str = str(e)
        if "template_required" in err_str:
            row.status = "template_required"
        else:
            row.status = "failed"
        row.error = err_str[:1000]
        db.commit()
        # Notify the user that the send failed via their WhatsApp number (if available)
        _notify_user_failure(db, row, err_str)
        return

    row.status = "sent"
    row.sent_at = now()
    row.error = None
    log.info("Schedule %d sent to %s (%s)", row.id, row.recipient, phone)

    # Reschedule recurring task
    if row.recurrence:
        next_dt = _next_occurrence(
            row.scheduled_at.replace(tzinfo=dt_tz.utc), row.recurrence
        )
        if next_dt:
            new_row = WhatsAppSchedule(
                user_id=row.user_id,
                recipient=row.recipient,
                recipient_phone=row.recipient_phone,
                message=row.message,
                scheduled_at=next_dt.replace(tzinfo=None),
                timezone=row.timezone,
                recurrence=row.recurrence,
                status="scheduled",
            )
            db.add(new_row)
    db.commit()

    # Notify user that message was sent
    _notify_user_success(db, row, phone)


def _notify_user_success(db: Session, row: WhatsAppSchedule, phone: str) -> None:
    """Send a confirmation to the user's own WhatsApp that the scheduled message went out."""
    try:
        link = db.query(WhatsAppLink).filter(WhatsAppLink.user_id == row.user_id).first()
        if link:
            whatsapp_agent.send_text(
                link.phone,
                f"✅ Scheduled message sent to *{row.recipient}* ({phone}).",
            )
    except Exception as e:
        log.debug("Could not notify user of success: %s", e)


def _notify_user_failure(db: Session, row: WhatsAppSchedule, error: str) -> None:
    """Notify the user that their scheduled message failed."""
    try:
        link = db.query(WhatsAppLink).filter(WhatsAppLink.user_id == row.user_id).first()
        if link:
            if "template_required" in error:
                msg = (
                    f"⚠️ Scheduled message to *{row.recipient}* could not be sent.\n"
                    "WhatsApp requires an approved message template for messages outside the 24-hour window."
                )
            else:
                msg = f"⚠️ Scheduled message to *{row.recipient}* failed: {error[:200]}"
            whatsapp_agent.send_text(link.phone, msg)
    except Exception as e:
        log.debug("Could not notify user of failure: %s", e)


# ── Background scheduler thread ───────────────────────────────────────────────

import re  # noqa: E402  (placed here to avoid module-level ordering with whatsapp_agent)

_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()


def start_scheduler() -> None:
    """Start the background scheduler thread (call once at app startup)."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _stop_event.clear()
    _scheduler_thread = threading.Thread(target=_run_scheduler, daemon=True, name="wa-scheduler")
    _scheduler_thread.start()
    log.info("WhatsApp scheduler started (interval=%ds)", settings.whatsapp_scheduler_interval)


def stop_scheduler() -> None:
    """Signal the scheduler to stop (for clean shutdown)."""
    _stop_event.set()


def _run_scheduler() -> None:
    interval = max(10, settings.whatsapp_scheduler_interval)
    while not _stop_event.is_set():
        try:
            with SessionLocal() as db:
                _send_due(db)
        except Exception:
            log.exception("WhatsApp scheduler tick error")
        _stop_event.wait(interval)
