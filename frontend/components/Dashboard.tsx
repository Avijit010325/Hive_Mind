"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type Agent, type Status, type User } from "@/lib/api";
import { useFresh } from "@/lib/hooks";
import { EASE, prefersStill } from "@/lib/motion";
import AgentEditor, { type Tab } from "./AgentEditor";
import CalendarAgent from "./CalendarAgent";
import MemoryCard from "./MemoryCard";
import Modal from "./Modal";
import ScheduledWhatsApp from "./ScheduledWhatsApp";
import TelegramCard from "./TelegramCard";
import WhatsAppAgent from "./WhatsAppAgent";

type Editing = { agent: Agent | null; tab: Tab; key: number };

function statusMessage(status: Status | null): string {
  if (!status) return "";
  if (status.llm.key_missing) return "No GEMINI_API_KEY is set on this server, so agents can't answer yet. Add it to .env and restart.";
  if (!status.llm.reachable) return "The Gemma model server isn't reachable. Agents can't answer until it is.";
  if (!status.llm.model_ready) return `The model ${status.llm.model} isn't available on the model server yet.`;
  return "";
}

export default function Dashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const isNew = useFresh((agents ?? []).map((agent) => agent.id));
  const dialog = useRef<HTMLDialogElement>(null);
  const fromHeight = useRef(0);
  const nextKey = useRef(1);

  const loadAgents = useCallback(() => api<Agent[]>("GET", "/api/agents").then(setAgents, () => {}), []);

  useEffect(() => {
    loadAgents();
    const loadStatus = () => api<Status>("GET", "/api/status").then(setStatus, () => {});
    loadStatus();
    const timer = setInterval(loadStatus, 30_000);
    return () => clearInterval(timer);
  }, [loadAgents]);

  /** Change what the dialog shows, remembering its height so it can glide to the new one. */
  function show(next: Editing | null) {
    fromHeight.current = dialog.current?.open ? dialog.current.getBoundingClientRect().height : 0;
    setEditing(next);
  }
  const open = (agent: Agent | null, tab: Tab = "profile") => show({ agent, tab, key: nextKey.current++ });

  useLayoutEffect(() => {
    const node = dialog.current;
    const before = fromHeight.current;
    fromHeight.current = 0;
    if (!node || !node.open || !before || prefersStill()) return;
    const after = node.getBoundingClientRect().height;
    if (Math.abs(after - before) < 2) return;
    node.style.overflow = "hidden";  // no scrollbar flash while it resizes
    const glide = node.animate({ height: [`${before}px`, `${after}px`] }, { duration: 420, easing: EASE });
    glide.finished.catch(() => {}).then(() => { node.style.overflow = ""; });
    return () => glide.cancel();
  }, [editing]);

  const model = status?.llm;
  const ready = !!model && model.reachable && model.model_ready;
  const banner = statusMessage(status);

  return (
    <div className="app">
      <header className="nav app-nav">
        <span className="brand">HiveMind</span>
        <div className="nav-cta">
          {model && <span className={`pill ${ready ? "" : "bad"}`} title="Model status">{ready ? "● " : "○ "}{model.model}</span>}
          <span className="muted small hide-sm">{user.email}</span>
          <button className="link" onClick={onLogout}>Log out</button>
        </div>
      </header>
      {banner && <div className="banner">{banner}</div>}

      <main className="layout">
        <section>
          <div className="section-head">
            <div>
              <h1 className="page-title">Your crew</h1>
              <p className="muted">Each agent has its own job, instructions and knowledge.</p>
            </div>
            <button className="btn accent" onClick={() => open(null)}>+ Hire an agent</button>
          </div>
          <div className="agents">
            {agents?.length === 0 && <p className="muted">No agents yet. Hire your first one.</p>}
            {agents?.map((agent, i) => (
              <button
                key={agent.id} className={isNew(agent.id) ? "agent new" : "agent"}
                style={{ "--i": i } as React.CSSProperties} onClick={() => open(agent)}
              >
                <span className="emoji">{agent.emoji}</span>
                <span className="name">{agent.name}</span>
                <span className="role">{agent.role || "No role set"}</span>
              </button>
            ))}
          </div>
        </section>
        <aside>
          <CalendarAgent />
          <WhatsAppAgent />
          <ScheduledWhatsApp />
          <TelegramCard />
          <MemoryCard />
        </aside>
      </main>

      <Modal open={editing !== null} onClose={() => setEditing(null)} dialogRef={dialog}>
        {editing && (
          <AgentEditor
            key={editing.key}
            agent={editing.agent}
            tab={editing.tab}
            onTab={(tab) => show({ ...editing, tab })}
            onClose={() => setEditing(null)}
            onDeleted={(id) => {
              // Remove it from view at once; if the server refuses, the refetch brings it back.
              setAgents((all) => all?.filter((agent) => agent.id !== id) ?? null);
              setEditing(null);
              api("DELETE", `/api/agents/${id}`).catch(() => {}).finally(loadAgents);
            }}
            onSaved={(saved, created) => {
              // Show the change at once; the refetch only reconciles.
              setAgents((all) => (all?.some((a) => a.id === saved.id) ? all.map((a) => (a.id === saved.id ? saved : a)) : [...(all ?? []), saved]));
              loadAgents();
              if (created) open(saved, "chat"); else setEditing(null);
            }}
          />
        )}
      </Modal>
    </div>
  );
}
