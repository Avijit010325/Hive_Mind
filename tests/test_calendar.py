import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError
import httpx

from app import calendar_agent, main
from app.config import settings
from app.db import GoogleToken, SessionLocal, User


def create_mock_service():
    service = MagicMock()
    mock_events = MagicMock()
    service.events.return_value = mock_events

    # Mock insert
    insert_req = MagicMock()
    insert_req.execute.return_value = {
        "id": "event_12345",
        "htmlLink": "https://calendar.google.com/event?eid=12345",
        "summary": "Project meeting",
        "start": {"dateTime": "2026-10-08T16:00:00+05:30"},
        "end": {"dateTime": "2026-10-08T17:00:00+05:30"},
    }
    mock_events.insert.return_value = insert_req

    # Mock list
    list_req = MagicMock()
    list_req.execute.return_value = {
        "items": [
            {
                "id": "event_12345",
                "summary": "Project meeting",
                "start": {"dateTime": "2026-10-08T16:00:00+05:30"},
                "end": {"dateTime": "2026-10-08T17:00:00+05:30"},
                "htmlLink": "https://calendar.google.com/event?eid=12345",
            }
        ]
    }
    mock_events.list.return_value = list_req
    return service


def store_test_token(user_id: int):
    with SessionLocal() as db:
        token = GoogleToken(
            user_id=user_id,
            credentials=json.dumps({
                "token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "scopes": ["https://www.googleapis.com/auth/calendar"],
            }),
        )
        db.add(token)
        db.commit()


# --- Test 1: Natural language -> calendar intent -----------------------------

def test_nl_to_calendar_intent(gemma):
    gemma.answers = [
        json.dumps({
            "action": "create_calendar_event",
            "title": "Meeting with Rahul",
            "description": "Discuss project updates",
            "start": "2026-10-08T16:00:00+05:30",
            "end": "2026-10-08T17:00:00+05:30",
            "timezone": "Asia/Kolkata",
            "location": "Google Meet",
            "attendees": ["Rahul"],
        })
    ]

    intent = calendar_agent.parse_intent(
        "Schedule a meeting with Rahul tomorrow at 4 PM.",
        timezone_str="Asia/Kolkata",
    )

    assert intent["action"] == "create_calendar_event"
    assert intent["title"] == "Meeting with Rahul"
    assert intent["start"] == "2026-10-08T16:00:00+05:30"
    assert intent["end"] == "2026-10-08T17:00:00+05:30"
    assert intent["timezone"] == "Asia/Kolkata"
    assert intent["attendees"] == ["Rahul"]


# --- Test 2: Missing time ---------------------------------------------------

def test_missing_time(gemma):
    # Model detects missing time and asks clarification
    gemma.answers = [
        json.dumps({
            "action": "clarification_needed",
            "question": "What time tomorrow evening should I use?",
        })
    ]

    intent = calendar_agent.parse_intent(
        "Schedule a meeting tomorrow evening.",
        timezone_str="Asia/Kolkata",
    )

    assert intent["action"] == "clarification_needed"
    assert "time" in intent["question"].lower()

    # Also test Python validation if model erroneously returns create_calendar_event without start time
    gemma.answers = [
        json.dumps({
            "action": "create_calendar_event",
            "title": "Evening meeting",
            "start": "",
            "end": "",
            "timezone": "Asia/Kolkata",
        })
    ]
    intent2 = calendar_agent.parse_intent(
        "Schedule a meeting tomorrow evening.",
        timezone_str="Asia/Kolkata",
    )
    assert intent2["action"] == "clarification_needed"


# --- Test 3: Missing date ---------------------------------------------------

def test_missing_date(gemma):
    gemma.answers = [
        json.dumps({
            "action": "clarification_needed",
            "question": "What date and time should I schedule the meeting with Rahul?",
        })
    ]

    intent = calendar_agent.parse_intent(
        "Schedule a meeting with Rahul.",
        timezone_str="Asia/Kolkata",
    )

    assert intent["action"] == "clarification_needed"
    assert "date" in intent["question"].lower()


# --- Test 4: Timezone resolution hierarchy -----------------------------------

def test_timezone_resolution():
    # 1. Stored user timezone
    user = User(email="tz@example.com", timezone="America/New_York")
    assert calendar_agent.resolve_timezone(user, client_timezone="Europe/London") == "America/New_York"

    # 2. Browser timezone when user has no stored tz
    user_no_tz = User(email="notz@example.com", timezone=None)
    assert calendar_agent.resolve_timezone(user_no_tz, client_timezone="Europe/London") == "Europe/London"

    # 3. Configured timezone when no user or client tz
    with patch.object(settings, "default_timezone", "Asia/Tokyo"):
        assert calendar_agent.resolve_timezone(None, client_timezone=None) == "Asia/Tokyo"

    # 4. Fallback to Asia/Kolkata when configured tz is invalid
    with patch.object(settings, "default_timezone", "Invalid/Timezone"):
        assert calendar_agent.resolve_timezone(None, client_timezone=None) == "Asia/Kolkata"


# --- Test 5: Preview does not create event ----------------------------------

def test_preview_does_not_create_event(client, user, gemma):
    store_test_token(user["id"])
    mock_service = create_mock_service()

    gemma.answers = [
        json.dumps({
            "action": "create_calendar_event",
            "title": "Project meeting",
            "start": "2026-10-08T16:00:00+05:30",
            "end": "2026-10-08T17:00:00+05:30",
            "timezone": "Asia/Kolkata",
        })
    ]

    with patch("app.calendar_agent.get_calendar_service", return_value=mock_service):
        r = client.post("/api/calendar/preview", json={"message": "Schedule project meeting tomorrow at 4 PM"})
        assert r.status_code == 200
        data = r.json()
        assert data["action"] == "create_calendar_event"
        assert data["event"]["title"] == "Project meeting"

        # Verify Google Calendar API was NEVER called during preview
        mock_service.events().insert.assert_not_called()


# --- Test 6: Confirmation creates event -------------------------------------

def test_confirmation_creates_event(client, user):
    store_test_token(user["id"])
    mock_service = create_mock_service()

    with patch("app.calendar_agent.get_calendar_service", return_value=mock_service):
        r = client.post(
            "/api/calendar/confirm",
            json={
                "event": {
                    "title": "Project meeting",
                    "start": "2026-10-08T16:00:00+05:30",
                    "end": "2026-10-08T17:00:00+05:30",
                    "timezone": "Asia/Kolkata",
                    "description": "Weekly sync",
                    "location": "Room 101",
                    "attendees": ["test@example.com"],
                }
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["event_id"] == "event_12345"
        assert data["html_link"] == "https://calendar.google.com/event?eid=12345"

        # Verify Google Calendar insert execute was called
        mock_service.events().insert().execute.assert_called_once()


# --- Test 7: Cancel does not create event -----------------------------------

def test_cancel_does_not_create_event(client, user, gemma):
    store_test_token(user["id"])
    mock_service = create_mock_service()

    gemma.answers = [
        json.dumps({
            "action": "create_calendar_event",
            "title": "Sprint planning",
            "start": "2026-10-08T10:00:00+05:30",
            "end": "2026-10-08T11:00:00+05:30",
            "timezone": "Asia/Kolkata",
        })
    ]

    with patch("app.calendar_agent.get_calendar_service", return_value=mock_service):
        # 1. Preview
        r = client.post("/api/calendar/preview", json={"message": "Sprint planning tomorrow at 10 AM"})
        assert r.status_code == 200
        assert r.json()["action"] == "create_calendar_event"

        # 2. Cancel: user cancels on frontend; no POST /api/calendar/confirm is sent
        # Verify no insert was executed
        mock_service.events().insert.assert_not_called()


# --- Test 8: Calendar API failure -------------------------------------------

def test_calendar_api_failure(client, user):
    store_test_token(user["id"])
    mock_service = create_mock_service()

    # Simulate HttpError from Google API (httplib2 response structure)
    class MockHttpResp:
        status = 500
        reason = "Google Calendar backend error"

    http_error = HttpError(resp=MockHttpResp(), content=b"Google Calendar backend error")
    mock_service.events().insert().execute.side_effect = http_error

    with patch("app.calendar_agent.get_calendar_service", return_value=mock_service):
        r = client.post(
            "/api/calendar/confirm",
            json={
                "event": {
                    "title": "Failing meeting",
                    "start": "2026-10-08T16:00:00+05:30",
                    "end": "2026-10-08T17:00:00+05:30",
                    "timezone": "Asia/Kolkata",
                }
            },
        )
        assert r.status_code == 400
        assert "Google Calendar" in r.json()["detail"]


# --- Test 9: OAuth failure ---------------------------------------------------

def test_oauth_failure(client, user):
    # Missing OAuth configuration on server
    with patch.object(settings, "google_client_id", ""):
        r = client.get("/api/calendar/auth")
        assert r.status_code == 200
        assert r.json()["configured"] is False

    # Invalid OAuth callback state (follow_redirects=False because /dashboard is handled by Next.js)
    r = client.get("/api/calendar/callback?code=badcode&state=invalidstate", follow_redirects=False)
    assert r.status_code == 307
    assert "calendar_error" in r.headers["location"]


# --- Test 10: Unauthorized access -------------------------------------------

def test_unauthorized_access(client):
    # Preview without auth
    r = client.post("/api/calendar/preview", json={"message": "Schedule meeting"})
    assert r.status_code == 401

    # Confirm without auth
    r = client.post("/api/calendar/confirm", json={"event": {"title": "X", "start": "Y", "end": "Z", "timezone": "UTC"}})
    assert r.status_code == 401

    # Events list without auth
    r = client.get("/api/calendar/events")
    assert r.status_code == 401

    # Status without auth
    r = client.get("/api/calendar/status")
    assert r.status_code == 401
