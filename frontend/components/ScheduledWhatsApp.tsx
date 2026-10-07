"use client";

import { useEffect, useRef, useState } from "react";
import {
  api,
  errorText,
  type WhatsAppScheduleIntent,
  type WhatsAppScheduleItem,
  type WhatsAppSchedulePreviewResponse,
  type WhatsAppStatus,
} from "@/lib/api";

function fmtStatus(status: string) {
  const map: Record<string, string> = {
    scheduled: "📅 Scheduled",
    sending: "⏳ Sending…",
    sent: "✅ Sent",
    failed: "❌ Failed",
    cancelled: "🚫 Cancelled",
    template_required: "⚠️ Template required",
  };
  return map[status] ?? status;
}

export default function ScheduledWhatsApp() {
  const [status, setStatus] = useState<WhatsAppStatus | null>(null);
  const [schedules, setSchedules] = useState<WhatsAppScheduleItem[]>([]);

  // The current input the user types
  const [input, setInput] = useState("");
  // Accumulated full context sent to Gemma (original + clarification answers appended)
  const accumulatedRef = useRef("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [clarificationQuestion, setClarificationQuestion] = useState("");
  const [awaitingClarification, setAwaitingClarification] = useState(false);
  const [preview, setPreview] = useState<WhatsAppScheduleIntent | null>(null);
  const [successMsg, setSuccessMsg] = useState("");

  const loadStatus = async () => {
    try {
      const data = await api<WhatsAppStatus>("GET", "/api/whatsapp/status");
      setStatus(data);
    } catch (err) {
      setError(errorText(err));
    }
  };

  const loadSchedules = async () => {
    try {
      const data = await api<{ schedules: WhatsAppScheduleItem[] }>("GET", "/api/whatsapp/scheduled");
      setSchedules(data.schedules ?? []);
    } catch { /* ignore */ }
  };

  useEffect(() => {
    loadStatus();
    loadSchedules();
    const t = setInterval(loadSchedules, 30_000);
    return () => clearInterval(t);
  }, []);

  const runPreview = async (fullQuery: string) => {
    setBusy(true);
    setError("");
    const clientTz =
      typeof Intl !== "undefined"
        ? Intl.DateTimeFormat().resolvedOptions().timeZone
        : "Asia/Kolkata";

    try {
      const res = await api<WhatsAppSchedulePreviewResponse>(
        "POST",
        "/api/whatsapp/scheduled/preview",
        { message: fullQuery, client_timezone: clientTz }
      );

      if (res.action === "schedule_whatsapp_message" && res.preview) {
        // All info collected — show confirmation card
        setPreview(res.preview);
        setClarificationQuestion("");
        setAwaitingClarification(false);
        setInput("");
      } else if (res.action === "clarification_needed") {
        // Still missing info — ask the user and wait for their reply
        setClarificationQuestion(res.question ?? "Could you clarify the details?");
        setAwaitingClarification(true);
        setInput(""); // clear so user types the answer
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || busy) return;

    setSuccessMsg("");
    setPreview(null);

    if (awaitingClarification) {
      // Append the clarification answer to the accumulated context
      // e.g. original: "send hi to bipin 3.33pm"
      //      answer:   "today"
      //      merged:   "send hi to bipin 3.33pm. Date: today"
      const merged = accumulatedRef.current + ". " + trimmed;
      accumulatedRef.current = merged;
      await runPreview(merged);
    } else {
      // Fresh request
      accumulatedRef.current = trimmed;
      await runPreview(trimmed);
    }
  };

  const handleConfirm = async () => {
    if (!preview || busy) return;
    setBusy(true);
    setError("");
    try {
      const res = await api<WhatsAppScheduleItem>(
        "POST",
        "/api/whatsapp/scheduled/confirm",
        {
          recipient: preview.recipient,
          message: preview.message,
          scheduled_at: preview.scheduled_at,
          timezone: preview.timezone,
          recurrence: preview.recurrence ?? null,
        }
      );
      setSuccessMsg(`✅ Scheduled for ${res.scheduled_at_local}`);
      setPreview(null);
      setInput("");
      accumulatedRef.current = "";
      setAwaitingClarification(false);
      setClarificationQuestion("");
      loadSchedules();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = () => {
    setPreview(null);
    setClarificationQuestion("");
    setAwaitingClarification(false);
    setSuccessMsg("");
    setInput("");
    accumulatedRef.current = "";
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Cancel this scheduled message?")) return;
    try {
      await api("DELETE", `/api/whatsapp/scheduled/${id}`);
      setSchedules((prev) => prev.filter((s) => s.id !== id));
    } catch (err) {
      setError(errorText(err));
    }
  };

  const placeholder = awaitingClarification
    ? "Type your answer here…"
    : 'e.g. "Send hi to Bipin today at 3:33 PM"';

  return (
    <div className="card">
      <div style={{ marginBottom: "14px" }}>
        <h3 style={{ margin: 0, textTransform: "uppercase", letterSpacing: "0.04em" }}>
          WhatsApp Automation
        </h3>
        <p className="muted small" style={{ marginTop: 4 }}>
          Schedule messages via WhatsApp
        </p>
      </div>

      <div className="stack">
        {/* Status */}
        {status?.configured ? (
          <span className="pill">● WhatsApp Connected</span>
        ) : (
          <span className="pill bad">○ Not Configured — add WHATSAPP_ACCESS_TOKEN</span>
        )}

        {/* Clarification bubble from agent */}
        {clarificationQuestion && (
          <div className="wa-clarification">
            <p className="small">
              <strong>WhatsApp Agent:</strong> {clarificationQuestion}
            </p>
          </div>
        )}

        {/* Input form */}
        {!preview && (
          <form onSubmit={handleSubmit} className="wa-input-form">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={placeholder}
              disabled={busy}
              autoFocus={awaitingClarification}
            />
            <button
              type="submit"
              className="btn accent full"
              disabled={busy || !input.trim()}
            >
              {busy
                ? "Planning…"
                : awaitingClarification
                ? "Submit Answer"
                : "Schedule a Message"}
            </button>
            {awaitingClarification && (
              <button
                type="button"
                className="btn ghost full"
                onClick={handleCancel}
                disabled={busy}
              >
                Start over
              </button>
            )}
          </form>
        )}

        {/* Preview confirmation card */}
        {preview && (
          <div className="wa-preview-card">
            <div className="wa-preview-badge">📅 SCHEDULED WHATSAPP</div>
            <div className="wa-preview-field">
              <span className="wa-preview-label">Recipient</span>
              <span className="wa-preview-value">👤 {preview.recipient}</span>
            </div>
            <div className="wa-preview-field">
              <span className="wa-preview-label">Message</span>
              <span className="wa-preview-message">💬 {preview.message}</span>
            </div>
            <div className="wa-preview-field">
              <span className="wa-preview-label">When</span>
              <span className="wa-preview-value">
                🕘{" "}
                {(() => {
                  try {
                    const d = new Date(preview.scheduled_at);
                    return d.toLocaleString("en-IN", {
                      weekday: "short",
                      month: "short",
                      day: "numeric",
                      hour: "numeric",
                      minute: "2-digit",
                      timeZone: preview.timezone,
                    });
                  } catch {
                    return preview.scheduled_at;
                  }
                })()}
              </span>
            </div>
            <div className="wa-preview-field">
              <span className="wa-preview-label">Timezone</span>
              <span className="wa-preview-value">🌍 {preview.timezone}</span>
            </div>
            {preview.recurrence && (
              <div className="wa-preview-field">
                <span className="wa-preview-label">Repeats</span>
                <span className="wa-preview-value">
                  🔁 {preview.recurrence === "daily" ? "Daily" : "Weekly"}
                </span>
              </div>
            )}
            <div className="wa-preview-actions">
              <button className="btn accent" onClick={handleConfirm} disabled={busy}>
                {busy ? "Scheduling…" : "CONFIRM"}
              </button>
              <button className="btn ghost" onClick={handleCancel} disabled={busy}>
                CANCEL
              </button>
            </div>
          </div>
        )}

        {/* Success */}
        {successMsg && (
          <div className="wa-success">
            <p className="small">{successMsg}</p>
          </div>
        )}

        {error && <p className="error small">{error}</p>}

        {/* Scheduled messages list */}
        <div className="wa-schedules">
          <h4>Scheduled Messages</h4>
          {schedules.length === 0 ? (
            <p className="muted small">No scheduled messages.</p>
          ) : (
            <ul className="list">
              {schedules.map((s) => (
                <li key={s.id} className="wa-schedule-item">
                  <div className="wa-schedule-info">
                    <span className="wa-schedule-recipient">👤 {s.recipient}</span>
                    <span className="wa-schedule-time">{s.scheduled_at_local}</span>
                    <span className="wa-schedule-msg">💬 {s.message}</span>
                    <span className={`wa-status-badge ${s.status.replace("_", "-")}`}>
                      {fmtStatus(s.status)}
                    </span>
                  </div>
                  {s.status === "scheduled" && (
                    <button
                      title="Cancel scheduled message"
                      aria-label="Cancel"
                      onClick={() => handleDelete(s.id)}
                      style={{
                        padding: "2px 10px",
                        border: "none",
                        background: "none",
                        color: "var(--dim)",
                        cursor: "pointer",
                        fontSize: "1rem",
                      }}
                    >
                      ✕
                    </button>
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
