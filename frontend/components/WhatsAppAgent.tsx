"use client";

import { useEffect, useState } from "react";
import { api, errorText, type WhatsAppLinkedPhone, type WhatsAppStatus } from "@/lib/api";

export default function WhatsAppAgent() {
  const [status, setStatus] = useState<WhatsAppStatus | null>(null);
  const [phone, setPhone] = useState("");
  const [display, setDisplay] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [linkSuccess, setLinkSuccess] = useState("");

  const load = () =>
    api<WhatsAppStatus>("GET", "/api/whatsapp/status")
      .then(setStatus)
      .catch((e) => setError(errorText(e)));

  useEffect(() => {
    load();
  }, []);

  const handleLink = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!phone.trim() || busy) return;
    setBusy(true);
    setError("");
    setLinkSuccess("");

    // Normalise to E.164 — strip spaces/dashes, ensure +
    let normalised = phone.trim().replace(/[\s\-().]/g, "");
    if (!normalised.startsWith("+")) normalised = "+" + normalised;

    try {
      await api(
        "POST",
        `/api/whatsapp/link?phone=${encodeURIComponent(normalised)}&display=${encodeURIComponent(
          display.trim() || normalised
        )}`
      );
      setLinkSuccess(
        `✅ ${display.trim() || normalised} linked! Messages from this number will be handled by your Hivemind agents.`
      );
      setPhone("");
      setDisplay("");
      load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const handleUnlink = async (id: number, name: string) => {
    if (!confirm(`Unlink ${name}?`)) return;
    setError("");
    try {
      await api("DELETE", `/api/whatsapp/link/${id}`);
      setLinkSuccess("");
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <div className="card">
      <h3 style={{ textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 4 }}>
        WhatsApp
      </h3>
      <p className="muted small" style={{ marginBottom: 12 }}>
        Link your phone number so Hivemind can respond to your WhatsApp messages.
      </p>

      <div className="stack">
        {/* API status */}
        {status?.configured ? (
          <span className="pill">● WhatsApp API Connected</span>
        ) : (
          <>
            <span className="pill bad">○ WhatsApp API Not Configured</span>
            <p className="muted small">
              Add <code>WHATSAPP_ACCESS_TOKEN</code> and <code>WHATSAPP_PHONE_NUMBER_ID</code> to
              your <code>.env</code>.
            </p>
          </>
        )}

        {/* Linked numbers */}
        {(status?.linked_phones?.length ?? 0) > 0 && (
          <>
            <p className="small" style={{ fontWeight: 600 }}>
              Linked numbers:
            </p>
            <ul className="list">
              {status!.linked_phones.map((lp: WhatsAppLinkedPhone) => (
                <li key={lp.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span>
                    📱 <strong>{lp.display}</strong>
                    <span className="muted small" style={{ marginLeft: 6 }}>
                      {lp.phone}
                    </span>
                  </span>
                  <button
                    title="Unlink"
                    aria-label={`Unlink ${lp.display}`}
                    onClick={() => handleUnlink(lp.id, lp.display || lp.phone)}
                    style={{
                      padding: "2px 10px",
                      border: "none",
                      background: "none",
                      color: "var(--dim)",
                      cursor: "pointer",
                    }}
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        {/* Link success message */}
        {linkSuccess && (
          <div className="wa-success">
            <p className="small">{linkSuccess}</p>
          </div>
        )}

        {/* Link form */}
        {status?.configured && (
          <form onSubmit={handleLink} className="stack">
            <p className="muted small">Add a phone number to link:</p>
            <input
              type="tel"
              placeholder="+91 98765 43210"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              disabled={busy}
            />
            <input
              type="text"
              placeholder="Your name (optional)"
              value={display}
              onChange={(e) => setDisplay(e.target.value)}
              disabled={busy}
            />
            <button
              className="btn accent full"
              type="submit"
              disabled={busy || !phone.trim()}
            >
              {busy ? "Linking…" : "Link Number"}
            </button>
          </form>
        )}

        {error && <p className="error small">{error}</p>}
      </div>
    </div>
  );
}
