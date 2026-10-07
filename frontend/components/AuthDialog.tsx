"use client";

import { useEffect, useState } from "react";
import { api, errorText, type User } from "@/lib/api";
import Modal from "./Modal";

type Mode = "signup" | "login";
type Props = { mode: Mode | null; onMode: (mode: Mode | null) => void; onDone: (user: User) => void };

export default function AuthDialog({ mode, onMode, onDone }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const signup = mode === "signup";

  useEffect(() => setError(""), [mode]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onDone(await api<User>("POST", signup ? "/api/auth/signup" : "/api/auth/login", { email, password }));
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Modal open={mode !== null} onClose={() => onMode(null)} className="narrow">
      <form onSubmit={submit}>
        <div className="dialog-head">
          <h2>{signup ? "Create your account" : "Welcome back"}</h2>
          <button className="icon" type="button" aria-label="Close" onClick={() => onMode(null)}>✕</button>
        </div>
        <label>
          Email
          <input type="email" autoComplete="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <input
            type="password" minLength={8} required placeholder="At least 8 characters"
            autoComplete={signup ? "new-password" : "current-password"}
            value={password} onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="btn accent big full" type="submit" disabled={busy}>{signup ? "Sign up" : "Log in"}</button>
        <p className="switch">
          {signup ? "Already have an account?" : "New here?"}{" "}
          <a href="#" onClick={(e) => { e.preventDefault(); onMode(signup ? "login" : "signup"); }}>
            {signup ? "Log in" : "Sign up"}
          </a>
        </p>
      </form>
    </Modal>
  );
}
