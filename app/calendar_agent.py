"""Google Calendar AI Agent for HiveMind.

Handles:
- Google Calendar OAuth 2.0 (connecting, storing credentials, refreshing tokens)
- Converting natural language requests to structured calendar intent via Gemma
- Google Calendar API calls (listing events, creating events)
- Strict preview & confirmation safety boundary
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone as dt_tz
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jwt
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from . import llm, security
from .config import settings
from .db import GoogleToken, User, now

log = logging.getLogger("hivemind.calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar"]
PRIMARY_CALENDAR_ID = "primary"


class CalendarError(Exception):
    """User-safe exception for Google Calendar operations."""


def is_valid_timezone(tz_name: str | None) -> bool:
    if not tz_name:
        return False
    try:
        ZoneInfo(tz_name)
        return True
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False


def resolve_timezone(user: User | None, client_timezone: str | None = None) -> str:
    """Resolve user timezone using strict hierarchy:
    1. stored user timezone
    2. browser timezone
    3. configured timezone
    4. Asia/Kolkata only as development fallback
    """
    if user and user.timezone and is_valid_timezone(user.timezone):
        return user.timezone
    if client_timezone and is_valid_timezone(client_timezone):
        return client_timezone
    if settings.default_timezone and is_valid_timezone(settings.default_timezone):
        return settings.default_timezone
    return "Asia/Kolkata"


# --- OAuth 2.0 Flow ---------------------------------------------------------

def _ensure_oauth_insecure_transport(redirect_uri: str) -> None:
    if redirect_uri.startswith("http://"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def get_oauth_flow(redirect_uri: str | None = None) -> Flow:
    if not settings.google_client_id or not settings.google_client_secret:
        raise CalendarError("Google Calendar OAuth is not configured. Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
    uri = redirect_uri or settings.google_redirect_uri
    _ensure_oauth_insecure_transport(uri)
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [uri],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=uri,
    )


def make_oauth_state(user_id: int) -> str:
    """Create a signed, time-limited state parameter to prevent CSRF during OAuth."""
    payload = {
        "sub": str(user_id),
        "purpose": "gcal_oauth",
        "exp": int(datetime.now(dt_tz.utc).timestamp()) + 600,
    }
    return jwt.encode(payload, security.secret(), algorithm="HS256")


def read_oauth_state(state: str) -> int | None:
    try:
        data = jwt.decode(state, security.secret(), algorithms=["HS256"])
        if data.get("purpose") == "gcal_oauth":
            return int(data["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
    return None


def get_authorization_url(user_id: int, redirect_uri: str | None = None) -> str:
    flow = get_oauth_flow(redirect_uri)
    state = make_oauth_state(user_id)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return auth_url


def handle_oauth_callback(code: str, state: str, db: Session, redirect_uri: str | None = None) -> int:
    user_id = read_oauth_state(state)
    if not user_id:
        raise CalendarError("Invalid or expired OAuth state. Please try connecting again.")

    flow = get_oauth_flow(redirect_uri)
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        log.warning("OAuth token exchange failed: %s", e)
        raise CalendarError("Failed to authenticate with Google Calendar. Check credentials and code.")

    credentials = flow.credentials
    creds_json = credentials.to_json()

    record = db.query(GoogleToken).filter(GoogleToken.user_id == user_id).first()
    if record:
        record.credentials = creds_json
        record.updated_at = now()
    else:
        record = GoogleToken(user_id=user_id, credentials=creds_json)
        db.add(record)
    db.commit()
    return user_id


def is_connected(db: Session, user_id: int) -> bool:
    return db.query(GoogleToken).filter(GoogleToken.user_id == user_id).first() is not None


def disconnect_calendar(db: Session, user_id: int) -> bool:
    record = db.query(GoogleToken).filter(GoogleToken.user_id == user_id).first()
    if record:
        db.delete(record)
        db.commit()
        return True
    return False


def get_calendar_service(db: Session, user_id: int):
    record = db.query(GoogleToken).filter(GoogleToken.user_id == user_id).first()
    if not record:
        raise CalendarError("Google Calendar is not connected. Please connect your Google Calendar first.")

    try:
        creds_data = json.loads(record.credentials)
        credentials = Credentials.from_authorized_user_info(creds_data, scopes=SCOPES)
    except Exception as e:
        log.warning("Failed to deserialize credentials for user %s: %s", user_id, e)
        raise CalendarError("Invalid stored credentials. Please reconnect Google Calendar.")

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            record.credentials = credentials.to_json()
            record.updated_at = now()
            db.commit()
        except Exception as e:
            log.warning("Failed to refresh Google token for user %s: %s", user_id, e)
            raise CalendarError("Google Calendar session expired. Please reconnect your account.")

    try:
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)
    except Exception as e:
        log.warning("Failed to build Google Calendar service: %s", e)
        raise CalendarError("Could not connect to Google Calendar service.")


# --- Natural Language Parsing with Gemma ------------------------------------

INTENT_SYSTEM_PROMPT = """You are the HiveMind Google Calendar AI agent.
Your task is to convert natural-language calendar requests into a strict, validated JSON action.
DO NOT execute code. DO NOT output conversational text outside of the JSON object.

Current reference time:
- Reference ISO Datetime: {current_time_iso}
- Current Day of Week: {current_weekday}
- Timezone: {timezone}

You MUST output ONLY a valid JSON object matching one of the three schemas below.

SCHEMA 1: CREATE EVENT
Use when the user wants to schedule an event or block time, AND specific date and time are provided (or clear relative expressions like "tomorrow at 4 PM" or "next Monday from 2 to 3 PM").
If start time is given but duration/end time is omitted, default duration to 1 hour (e.g. "at 4 PM" -> 4:00 PM to 5:00 PM).
Always use the user timezone: "{timezone}" with appropriate ISO offset (e.g. +05:30).

{{
  "action": "create_calendar_event",
  "title": "<concise event title>",
  "description": "<description or empty string>",
  "start": "<ISO 8601 string with timezone offset>",
  "end": "<ISO 8601 string with timezone offset>",
  "timezone": "{timezone}",
  "location": "<location or empty string>",
  "attendees": []
}}

SCHEMA 2: LIST EVENTS
Use when the user asks what events are on their calendar (e.g. "What's on my calendar tomorrow?", "What do I have next Monday?", "List my upcoming meetings"):

{{
  "action": "list_calendar_events",
  "time_min": "<ISO 8601 string with timezone offset or null>",
  "time_max": "<ISO 8601 string with timezone offset or null>"
}}

SCHEMA 3: CLARIFICATION NEEDED
IMPORTANT RULE: Do NOT guess important missing information.
If the request is ambiguous, lacks a specific time, or lacks a date, ask for clarification.
Examples:
- "Schedule a meeting tomorrow evening." -> Ask: "What time tomorrow evening should I use?"
- "Schedule a meeting with Rahul." -> Ask: "What date and time should I schedule the meeting with Rahul?"
- "Block two hours tomorrow." -> Ask: "What time tomorrow should I block the two hours?"

{{
  "action": "clarification_needed",
  "question": "<polite clarification question asking for missing date or time>"
}}

CRITICAL INSTRUCTION: Output ONLY raw JSON. No markdown ticks, no explanations.
"""


def _clean_json_str(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    # Search for first { and last }
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        return m.group(0)
    return cleaned


def parse_intent(message: str, timezone_str: str, now_dt: datetime | None = None) -> dict[str, Any]:
    """Uses Gemma to parse a natural language message into a calendar intent dictionary."""
    if not message.strip():
        return {
            "action": "clarification_needed",
            "question": "What would you like me to schedule or check on your calendar?",
        }

    try:
        tz = ZoneInfo(timezone_str)
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")
        timezone_str = "Asia/Kolkata"

    current_local = now_dt if now_dt else datetime.now(tz)
    if current_local.tzinfo is None:
        current_local = current_local.replace(tzinfo=tz)

    system_prompt = INTENT_SYSTEM_PROMPT.format(
        current_time_iso=current_local.isoformat(),
        current_weekday=current_local.strftime("%A"),
        timezone=timezone_str,
    )

    try:
        llm_reply = llm.chat(
            system=system_prompt,
            messages=[{"role": "user", "content": message}],
            temperature=0.1,
            max_tokens=600,
        )
    except llm.LLMError as e:
        log.warning("LLM call failed during intent parsing: %s", e)
        raise CalendarError(f"Could not parse calendar request: {e}")

    try:
        parsed = json.loads(_clean_json_str(llm_reply))
    except Exception as e:
        log.warning("Failed to parse JSON from Gemma reply: %r (%s)", llm_reply, e)
        return {
            "action": "clarification_needed",
            "question": "Could you please specify the exact date and time for the meeting?",
        }

    action = parsed.get("action")

    if action == "create_calendar_event":
        title = (parsed.get("title") or "").strip()
        start_str = parsed.get("start")
        end_str = parsed.get("end")

        if not title:
            title = "Meeting"

        # Validate start and end are valid ISO datetimes
        if not start_str or not end_str:
            return {
                "action": "clarification_needed",
                "question": "What time should I schedule this meeting?",
            }

        try:
            start_dt = datetime.fromisoformat(start_str)
            end_dt = datetime.fromisoformat(end_str)
        except Exception:
            return {
                "action": "clarification_needed",
                "question": "Could you please clarify the date and time for this meeting?",
            }

        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=1)
            end_str = end_dt.isoformat()

        attendees = parsed.get("attendees") or []
        if isinstance(attendees, str):
            attendees = [attendees]

        return {
            "action": "create_calendar_event",
            "title": title,
            "description": parsed.get("description") or "",
            "start": start_str,
            "end": end_str,
            "timezone": timezone_str,
            "location": parsed.get("location") or "",
            "attendees": attendees,
        }

    elif action == "list_calendar_events":
        return {
            "action": "list_calendar_events",
            "time_min": parsed.get("time_min"),
            "time_max": parsed.get("time_max"),
        }

    elif action == "clarification_needed":
        return {
            "action": "clarification_needed",
            "question": parsed.get("question") or "Could you provide more details on the date and time?",
        }

    # Fallback if unknown action
    return {
        "action": "clarification_needed",
        "question": "Could you please clarify what you would like to schedule or view?",
    }


# --- Calendar API Execution -------------------------------------------------

def create_calendar_event(
    service: Any,
    title: str,
    start: str,
    end: str,
    timezone: str,
    description: str = "",
    location: str = "",
    attendees: list[str] | None = None,
) -> dict[str, Any]:
    """Creates an event on the user's primary calendar.
    Requires explicit user confirmation prior to execution.
    """
    body: dict[str, Any] = {
        "summary": title,
        "description": description or "",
        "location": location or "",
        "start": {
            "dateTime": start,
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end,
            "timeZone": timezone,
        },
    }

    if attendees:
        formatted_attendees = []
        for att in attendees:
            if not att:
                continue
            if "@" in att:
                formatted_attendees.append({"email": att.strip()})
            else:
                formatted_attendees.append({"displayName": att.strip()})
        if formatted_attendees:
            body["attendees"] = formatted_attendees

    try:
        created = service.events().insert(
            calendarId=PRIMARY_CALENDAR_ID,
            body=body,
        ).execute()
    except HttpError as e:
        log.warning("Google Calendar API HttpError in insert: %s", e)
        raise CalendarError(f"Google Calendar API error: {e.reason or 'Failed to create event'}")
    except Exception as e:
        log.warning("Google Calendar API unexpected error in insert: %s", e)
        raise CalendarError("Failed to communicate with Google Calendar.")

    return {
        "success": True,
        "event_id": created.get("id", ""),
        "html_link": created.get("htmlLink", ""),
        "title": created.get("summary", title),
        "start": created.get("start", {}).get("dateTime", start),
        "end": created.get("end", {}).get("dateTime", end),
    }


def list_upcoming_events(
    service: Any,
    time_min: str | None = None,
    time_max: str | None = None,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """Fetches upcoming events from the user's primary calendar."""
    if not time_min:
        time_min = datetime.now(dt_tz.utc).isoformat()

    try:
        req = service.events().list(
            calendarId=PRIMARY_CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        events_result = req.execute()
    except HttpError as e:
        log.warning("Google Calendar API HttpError in list: %s", e)
        raise CalendarError(f"Google Calendar API error: {e.reason or 'Failed to fetch events'}")
    except Exception as e:
        log.warning("Google Calendar API error in list: %s", e)
        raise CalendarError("Failed to communicate with Google Calendar.")

    items = events_result.get("items", [])
    events: list[dict[str, Any]] = []
    for item in items:
        start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date") or ""
        end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date") or ""
        events.append({
            "id": item.get("id", ""),
            "title": item.get("summary", "(No title)"),
            "start": start,
            "end": end,
            "html_link": item.get("htmlLink", ""),
            "location": item.get("location", ""),
        })

    return events
