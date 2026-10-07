"""Tests for WhatsApp agent, scheduler, and API endpoints."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone as dt_tz
from unittest.mock import MagicMock, patch

import pytest

from app import whatsapp_agent, whatsapp_scheduler, main
from app.config import settings
from app.db import SessionLocal, WhatsAppLink, WhatsAppSchedule, User


# ── helpers ------------------------------------------------------------------

def _link_phone(user_id: int, phone: str = "+919876543210", display: str = "Test") -> WhatsAppLink:
    with SessionLocal() as db:
        link = WhatsAppLink(user_id=user_id, phone=phone, display=display)
        db.add(link)
        db.commit()
        db.refresh(link)
        return link


def _create_schedule(
    user_id: int,
    recipient: str = "Rahul",
    message: str = "Hello",
    scheduled_at: datetime | None = None,
    recurrence: str | None = None,
    status: str = "scheduled",
) -> WhatsAppSchedule:
    """Create a scheduled message directly in the DB (bypasses NL parsing)."""
    dt = scheduled_at or (datetime.now(dt_tz.utc) + timedelta(hours=1))
    with SessionLocal() as db:
        row = WhatsAppSchedule(
            user_id=user_id,
            recipient=recipient,
            message=message,
            scheduled_at=dt.replace(tzinfo=None),
            timezone="Asia/Kolkata",
            recurrence=recurrence,
            status=status,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


# ── Test 1: Natural-language extraction ---------------------------------------

def test_nl_extraction(gemma):
    gemma.answers = [
        json.dumps({
            "action": "schedule_whatsapp_message",
            "recipient": "Rahul",
            "message": "Happy Birthday 🎉",
            "scheduled_at": "2026-10-08T09:00:00+05:30",
            "timezone": "Asia/Kolkata",
            "recurrence": None,
            "needs_clarification": False,
            "clarification_question": None,
        })
    ]
    intent = whatsapp_agent.parse_schedule_intent(
        "Send Rahul 'Happy Birthday 🎉' tomorrow at 9 AM.",
        "Asia/Kolkata",
    )
    assert intent["action"] == "schedule_whatsapp_message"
    assert intent["recipient"] == "Rahul"
    assert intent["message"] == "Happy Birthday 🎉"
    assert intent["needs_clarification"] is False


# ── Test 2: Missing recipient -------------------------------------------------

def test_missing_recipient(gemma):
    gemma.answers = [
        json.dumps({
            "action": "schedule_whatsapp_message",
            "recipient": "",
            "message": "Hello",
            "scheduled_at": "2026-10-08T09:00:00+05:30",
            "timezone": "Asia/Kolkata",
            "recurrence": None,
            "needs_clarification": False,
            "clarification_question": None,
        })
    ]
    intent = whatsapp_agent.parse_schedule_intent("Send a message tomorrow at 9 AM.", "Asia/Kolkata")
    assert intent["needs_clarification"] is True
    assert "who" in intent["clarification_question"].lower() or "recipient" in intent["clarification_question"].lower()


# ── Test 3: Missing message ---------------------------------------------------

def test_missing_message(gemma):
    gemma.answers = [
        json.dumps({
            "action": "schedule_whatsapp_message",
            "recipient": "Rahul",
            "message": "",
            "scheduled_at": "2026-10-08T09:00:00+05:30",
            "timezone": "Asia/Kolkata",
            "recurrence": None,
            "needs_clarification": False,
            "clarification_question": None,
        })
    ]
    intent = whatsapp_agent.parse_schedule_intent("Send Rahul a message tomorrow.", "Asia/Kolkata")
    assert intent["needs_clarification"] is True
    question = intent["clarification_question"].lower()
    assert "message" in question or "what" in question


# ── Test 4: Missing time ------------------------------------------------------

def test_missing_time(gemma):
    gemma.answers = [
        json.dumps({
            "action": "schedule_whatsapp_message",
            "recipient": "Rahul",
            "message": "Happy Birthday",
            "scheduled_at": "",
            "timezone": "Asia/Kolkata",
            "recurrence": None,
            "needs_clarification": False,
            "clarification_question": None,
        })
    ]
    intent = whatsapp_agent.parse_schedule_intent("Send Rahul Happy Birthday tomorrow.", "Asia/Kolkata")
    assert intent["needs_clarification"] is True
    assert "time" in intent["clarification_question"].lower() or "when" in intent["clarification_question"].lower()


# ── Test 5: Timezone resolution -----------------------------------------------

def test_timezone_resolution():
    from app.calendar_agent import is_valid_timezone

    user = User(email="tz@test.com", timezone="America/New_York")
    result = whatsapp_agent.resolve_timezone(user, "Europe/London")
    assert result == "America/New_York"

    user_no_tz = User(email="notz@test.com", timezone=None)
    result2 = whatsapp_agent.resolve_timezone(user_no_tz, "Europe/London")
    assert result2 == "Europe/London"

    with patch.object(settings, "default_timezone", "Asia/Tokyo"):
        result3 = whatsapp_agent.resolve_timezone(None, None)
        assert result3 == "Asia/Tokyo"

    with patch.object(settings, "default_timezone", "Invalid/TZ"):
        result4 = whatsapp_agent.resolve_timezone(None, None)
        assert result4 == "Asia/Kolkata"


# ── Test 6: Preview endpoint does NOT create schedule -------------------------

def test_preview_does_not_save(client, user, gemma):
    gemma.answers = [
        json.dumps({
            "action": "schedule_whatsapp_message",
            "recipient": "Rahul",
            "message": "Happy Birthday 🎉",
            "scheduled_at": "2026-10-08T09:00:00+05:30",
            "timezone": "Asia/Kolkata",
            "recurrence": None,
            "needs_clarification": False,
            "clarification_question": None,
        })
    ]
    r = client.post("/api/whatsapp/scheduled/preview", json={
        "message": "Send Rahul Happy Birthday tomorrow at 9 AM"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["action"] == "schedule_whatsapp_message"

    # DB must still be empty
    with SessionLocal() as db:
        count = db.query(WhatsAppSchedule).filter(WhatsAppSchedule.user_id == user["id"]).count()
    assert count == 0, "Preview must not persist a schedule to the DB"


# ── Test 7: Confirm creates schedule -----------------------------------------

def test_confirm_creates_schedule(client, user):
    r = client.post("/api/whatsapp/scheduled/confirm", json={
        "recipient": "Rahul",
        "message": "Happy Birthday 🎉",
        "scheduled_at": "2026-10-08T09:00:00+05:30",
        "timezone": "Asia/Kolkata",
        "recurrence": None,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["recipient"] == "Rahul"
    assert data["status"] == "scheduled"
    assert data["id"] > 0


# ── Test 8: Cancellation -------------------------------------------------------

def test_cancellation(client, user):
    # Create a schedule directly
    row = _create_schedule(user["id"], recipient="Mom", message="I'll be late")

    r = client.delete(f"/api/whatsapp/scheduled/{row.id}")
    assert r.status_code == 200

    # Verify in DB
    with SessionLocal() as db:
        refreshed = db.get(WhatsAppSchedule, row.id)
    assert refreshed.status == "cancelled"


# ── Test 9: One-time message send (scheduler) ---------------------------------

def test_one_time_message_send(user):
    """Scheduler must send a due one-time message and mark it sent."""
    past_dt = datetime.now(dt_tz.utc) - timedelta(minutes=5)
    row = _create_schedule(user["id"], recipient="Rahul", message="Test msg", scheduled_at=past_dt)

    phone = "+919876543210"
    # Fake recipient phone lookup:
    with patch("app.whatsapp_scheduler._resolve_phone", return_value=phone), \
         patch("app.whatsapp_agent.send_text") as mock_send, \
         patch("app.whatsapp_agent.send_text"):  # suppress success notification

        mock_send.return_value = {"messages": [{"id": "waid_test"}]}

        with SessionLocal() as db:
            whatsapp_scheduler._send_due(db)

    with SessionLocal() as db:
        updated = db.get(WhatsAppSchedule, row.id)
    assert updated.status == "sent"
    assert updated.sent_at is not None


# ── Test 10: Duplicate prevention (idempotency) --------------------------------

def test_no_duplicate_send(user):
    """A message already in 'sending' state must not be sent again."""
    past_dt = datetime.now(dt_tz.utc) - timedelta(minutes=2)
    row = _create_schedule(user["id"], scheduled_at=past_dt, status="sending")

    with patch("app.whatsapp_agent.send_text") as mock_send, \
         patch("app.whatsapp_scheduler._resolve_phone", return_value="+919999999999"):
        with SessionLocal() as db:
            whatsapp_scheduler._send_due(db)

    # send_text must NOT be called because status was 'sending', not 'scheduled'
    mock_send.assert_not_called()


# ── Test 11: Daily recurrence -------------------------------------------------

def test_daily_recurrence(user):
    past_dt = datetime.now(dt_tz.utc) - timedelta(minutes=1)
    row = _create_schedule(user["id"], scheduled_at=past_dt, recurrence="daily")

    phone = "+919876543210"
    with patch("app.whatsapp_scheduler._resolve_phone", return_value=phone), \
         patch("app.whatsapp_agent.send_text"):
        with SessionLocal() as db:
            whatsapp_scheduler._send_due(db)

    with SessionLocal() as db:
        original = db.get(WhatsAppSchedule, row.id)
        new_rows = db.query(WhatsAppSchedule).filter(
            WhatsAppSchedule.user_id == user["id"],
            WhatsAppSchedule.status == "scheduled",
            WhatsAppSchedule.recurrence == "daily",
        ).all()

    assert original.status == "sent"
    assert len(new_rows) == 1
    expected_next = (past_dt + timedelta(days=1)).replace(tzinfo=None)
    diff = abs((new_rows[0].scheduled_at - expected_next).total_seconds())
    assert diff < 5  # within 5 seconds of expected


# ── Test 12: Weekly recurrence ------------------------------------------------

def test_weekly_recurrence(user):
    past_dt = datetime.now(dt_tz.utc) - timedelta(minutes=1)
    row = _create_schedule(user["id"], scheduled_at=past_dt, recurrence="weekly")

    with patch("app.whatsapp_scheduler._resolve_phone", return_value="+919876543210"), \
         patch("app.whatsapp_agent.send_text"):
        with SessionLocal() as db:
            whatsapp_scheduler._send_due(db)

    with SessionLocal() as db:
        new_rows = db.query(WhatsAppSchedule).filter(
            WhatsAppSchedule.user_id == user["id"],
            WhatsAppSchedule.status == "scheduled",
            WhatsAppSchedule.recurrence == "weekly",
        ).all()

    assert len(new_rows) == 1
    expected_next = (past_dt + timedelta(weeks=1)).replace(tzinfo=None)
    diff = abs((new_rows[0].scheduled_at - expected_next).total_seconds())
    assert diff < 5


# ── Test 13: Server restart recovery ----------------------------------------

def test_server_restart_recovery(user):
    """Schedules with status='scheduled' must be picked up after restart."""
    past_dt = datetime.now(dt_tz.utc) - timedelta(minutes=30)
    row = _create_schedule(user["id"], scheduled_at=past_dt)

    # Simulate "server restart" by simply calling _send_due again on a fresh DB session
    with patch("app.whatsapp_scheduler._resolve_phone", return_value="+919876543210"), \
         patch("app.whatsapp_agent.send_text"):
        with SessionLocal() as db:
            whatsapp_scheduler._send_due(db)

    with SessionLocal() as db:
        updated = db.get(WhatsAppSchedule, row.id)
    assert updated.status == "sent"


# ── Test 14: WhatsApp API failure ---------------------------------------------

def test_whatsapp_api_failure(user):
    past_dt = datetime.now(dt_tz.utc) - timedelta(minutes=1)
    row = _create_schedule(user["id"], scheduled_at=past_dt)

    with patch("app.whatsapp_scheduler._resolve_phone", return_value="+919876543210"), \
         patch("app.whatsapp_scheduler.whatsapp_agent.send_text",
               side_effect=whatsapp_agent.WhatsAppError("Network error")):
        with SessionLocal() as db:
            whatsapp_scheduler._send_due(db)

    with SessionLocal() as db:
        updated = db.get(WhatsAppSchedule, row.id)
    assert updated.status == "failed"
    assert updated.error is not None


# ── Test 15: Unauthorized cancellation ----------------------------------------

def test_unauthorized_cancellation(client, user):
    # Create a second user directly in the DB (not via signup, which would
    # overwrite the session cookie and log out the first user).
    from app.security import hash_password
    with SessionLocal() as db:
        other = User(email="other@test.com", password_hash=hash_password("password123"))
        db.add(other)
        db.commit()
        other_user_id = other.id

    # Create a schedule belonging to the other user
    row = _create_schedule(other_user_id, recipient="Eve", message="Secret")

    # The first (logged-in) user must not be able to cancel it
    r = client.delete(f"/api/whatsapp/scheduled/{row.id}")
    assert r.status_code == 404

    # Verify it is still scheduled
    with SessionLocal() as db:
        still_there = db.get(WhatsAppSchedule, row.id)
    assert still_there.status == "scheduled"


# ── Test: Webhook verification ------------------------------------------------

def test_webhook_verification(client):
    # Meta sends query params with dots (hub.mode, hub.challenge, hub.verify_token)
    r = client.get(
        "/api/whatsapp/webhook?hub.mode=subscribe"
        f"&hub.challenge=42&hub.verify_token={settings.whatsapp_verify_token}"
    )
    assert r.status_code == 200
    assert r.json() == 42


def test_webhook_verification_bad_token(client):
    r = client.get(
        "/api/whatsapp/webhook?hub.mode=subscribe&hub.challenge=99&hub.verify_token=wrong_token"
    )
    assert r.status_code == 403


# ── Test: Status endpoint requires auth ----------------------------------------

def test_status_requires_auth(client):
    r = client.get("/api/whatsapp/status")
    assert r.status_code == 401

    r = client.get("/api/whatsapp/scheduled")
    assert r.status_code == 401

    r = client.post("/api/whatsapp/scheduled/preview", json={"message": "test"})
    assert r.status_code == 401

    r = client.post("/api/whatsapp/scheduled/confirm", json={
        "recipient": "X", "message": "Y", "scheduled_at": "2026-10-08T09:00:00+05:30"
    })
    assert r.status_code == 401
