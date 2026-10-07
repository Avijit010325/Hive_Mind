"use client";

import { useEffect, useState } from "react";
import {
  api,
  errorText,
  type CalendarAuth,
  type CalendarConfirmResponse,
  type CalendarEventIntent,
  type CalendarEventItem,
  type CalendarPreviewResponse,
  type CalendarStatus,
} from "@/lib/api";

function formatDate(isoStr: string, tz?: string): string {
  try {
    const d = new Date(isoStr);
    return d.toLocaleDateString("en-US", {
      weekday: "long",
      month: "short",
      day: "numeric",
      timeZone: tz || undefined,
    });
  } catch {
    return isoStr;
  }
}

function formatTime(isoStr: string, tz?: string): string {
  try {
    const d = new Date(isoStr);
    return d.toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      timeZone: tz || undefined,
    });
  } catch {
    return isoStr;
  }
}

function formatTimeRange(startIso: string, endIso: string, tz?: string): string {
  return `${formatTime(startIso, tz)} – ${formatTime(endIso, tz)}`;
}

export default function CalendarAgent() {
  const [status, setStatus] = useState<CalendarStatus | null>(null);
  const [events, setEvents] = useState<CalendarEventItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [clarification, setClarification] = useState("");
  const [pendingEvent, setPendingEvent] = useState<CalendarEventIntent | null>(null);
  const [createdEvent, setCreatedEvent] = useState<CalendarConfirmResponse | null>(null);

  const loadStatus = async () => {
    try {
      const data = await api<CalendarStatus>("GET", "/api/calendar/status");
      setStatus(data);
      if (data.connected) {
        loadEvents();
      }
    } catch (err) {
      setError(errorText(err));
    }
  };

  const loadEvents = async () => {
    try {
      const data = await api<{ connected: boolean; events: CalendarEventItem[] }>("GET", "/api/calendar/events");
      if (data.events) {
        setEvents(data.events);
      }
    } catch {
      // Ignore background load error
    }
  };

  useEffect(() => {
    loadStatus();
    // Check if returned from OAuth redirect
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      if (params.get("calendar") === "connected") {
        window.history.replaceState({}, document.title, window.location.pathname);
        loadStatus();
      } else if (params.get("calendar_error")) {
        setError(`OAuth Error: ${params.get("calendar_error")}`);
        window.history.replaceState({}, document.title, window.location.pathname);
      }
    }
  }, []);

  const handleConnect = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await api<CalendarAuth>("GET", "/api/calendar/auth");
      if (res.configured && res.url) {
        window.location.href = res.url;
      } else {
        setError(res.error || "Google Calendar OAuth is not configured on this server.");
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const handleDisconnect = async () => {
    if (!confirm("Disconnect Google Calendar?")) return;
    setBusy(true);
    setError("");
    try {
      await api("POST", "/api/calendar/disconnect");
      setStatus((prev) => (prev ? { ...prev, connected: false } : null));
      setEvents([]);
      setPendingEvent(null);
      setCreatedEvent(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const handlePlan = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || busy) return;

    setBusy(true);
    setError("");
    setClarification("");
    setPendingEvent(null);
    setCreatedEvent(null);

    const clientTz = typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : "Asia/Kolkata";

    try {
      const res = await api<CalendarPreviewResponse>("POST", "/api/calendar/preview", {
        message: input.trim(),
        client_timezone: clientTz,
      });

      if (res.action === "create_calendar_event" && res.event) {
        setPendingEvent(res.event);
      } else if (res.action === "clarification_needed") {
        setClarification(res.question || "Could you please specify the date and time?");
      } else if (res.action === "list_calendar_events") {
        if (res.events) {
          setEvents(res.events);
        }
        if (res.message) {
          setClarification(res.message);
        }
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async () => {
    if (!pendingEvent || busy) return;
    setBusy(true);
    setError("");
    try {
      const res = await api<CalendarConfirmResponse>("POST", "/api/calendar/confirm", {
        event: pendingEvent,
      });
      setCreatedEvent(res);
      setPendingEvent(null);
      setInput("");
      loadEvents();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = () => {
    setPendingEvent(null);
  };

  return (
    <div className="card">
      <div className="section-head" style={{ marginBottom: "14px" }}>
        <h3 style={{ margin: 0, textTransform: "uppercase", letterSpacing: "0.04em" }}>HIVEMIND CALENDAR</h3>
      </div>

      <div className="stack">
        {/* Connection status */}
        <div className="cal-connection">
          <span className="small muted">Connection:</span>
          {status?.connected ? (
            <div className="cal-status-row">
              <span className="pill" title="Connected">
                ● Google Calendar Connected
              </span>
              <button className="link small" onClick={handleDisconnect} disabled={busy} title="Disconnect calendar">
                Disconnect
              </button>
            </div>
          ) : (
            <div className="cal-status-row">
              <span className="pill bad">○ Not Connected</span>
              <button
                className="btn small accent"
                onClick={handleConnect}
                disabled={busy || !status?.configured}
              >
                Connect Google Calendar
              </button>
            </div>
          )}
          {!status?.configured && (
            <p className="muted small">
              Google Calendar OAuth is not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to environment.
            </p>
          )}
        </div>

        {/* Input Form */}
        <form onSubmit={handlePlan} className="cal-input-form">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="What should I schedule?"
            disabled={busy}
          />
          <button type="submit" className="btn accent full" disabled={busy || !input.trim()}>
            {busy ? "Planning…" : "Plan Event"}
          </button>
        </form>

        {/* Clarification prompt if any */}
        {clarification && (
          <div className="cal-clarification">
            <p className="small">
              <strong>Calendar Agent:</strong> {clarification}
            </p>
          </div>
        )}

        {/* Pending Action Preview Card */}
        {pendingEvent && (
          <div className="cal-action-card">
            <div className="cal-action-badge">📅 CALENDAR ACTION</div>
            <div className="cal-action-title">{pendingEvent.title}</div>
            <div className="cal-action-date">{formatDate(pendingEvent.start, pendingEvent.timezone)}</div>
            <div className="cal-action-time">
              {formatTimeRange(pendingEvent.start, pendingEvent.end, pendingEvent.timezone)}
            </div>
            <div className="cal-action-tz">{pendingEvent.timezone}</div>
            {pendingEvent.location && <div className="cal-action-meta">📍 {pendingEvent.location}</div>}
            {pendingEvent.attendees && pendingEvent.attendees.length > 0 && (
              <div className="cal-action-meta">👥 {pendingEvent.attendees.join(", ")}</div>
            )}

            {/* Auth gate: show Connect button if not connected yet */}
            {!status?.connected ? (
              <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
                <p className="small" style={{ color: "var(--warn-ink)", margin: 0 }}>
                  ⚠️ Connect Google Calendar to save this event.
                </p>
                <div className="cal-action-buttons">
                  <button
                    className="btn accent"
                    onClick={handleConnect}
                    disabled={busy || !status?.configured}
                    title={!status?.configured ? "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env first" : ""}
                  >
                    {!status?.configured ? "OAuth Not Configured" : "Connect Google Calendar"}
                  </button>
                  <button className="btn ghost" onClick={handleCancel} disabled={busy}>
                    CANCEL
                  </button>
                </div>
                {!status?.configured && (
                  <p className="muted small">
                    Add <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> to your <code>.env</code>.
                  </p>
                )}
              </div>
            ) : (
              <div className="cal-action-buttons">
                <button className="btn accent" onClick={handleConfirm} disabled={busy}>
                  {busy ? "Creating…" : "CONFIRM"}
                </button>
                <button className="btn ghost" onClick={handleCancel} disabled={busy}>
                  CANCEL
                </button>
              </div>
            )}
          </div>
        )}

        {/* After Success Card */}
        {createdEvent && (
          <div className="cal-success-card">
            <p className="small">✓ Event added to Google Calendar</p>
            {createdEvent.html_link ? (
              <a className="btn accent full" href={createdEvent.html_link} target="_blank" rel="noopener">
                Open Calendar
              </a>
            ) : (
              <p className="small muted">Event ID: {createdEvent.event_id}</p>
            )}
          </div>
        )}

        {error && <p className="error small">{error}</p>}

        {/* Upcoming Events Section */}
        <div className="cal-upcoming">
          <h4>Upcoming Events</h4>
          {events.length === 0 ? (
            <p className="muted small">
              {status?.connected ? "No upcoming events found." : "Connect Google Calendar to see your schedule."}
            </p>
          ) : (
            <ul className="list">
              {events.map((ev) => (
                <li key={ev.id} className="cal-event-item">
                  <div className="cal-event-info">
                    <span className="cal-event-title">{ev.title}</span>
                    <span className="cal-event-datetime muted small">
                      {formatDate(ev.start, status?.timezone)} • {formatTime(ev.start, status?.timezone)}
                    </span>
                  </div>
                  {ev.html_link && (
                    <a href={ev.html_link} target="_blank" rel="noopener" className="small link" title="Open event">
                      View
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
