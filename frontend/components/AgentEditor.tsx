"use client";

import { useEffect, useRef, useState } from "react";
import { api, errorText, type Agent, type AgentDraft, type ChatMessage, type ComputerState, type Doc } from "@/lib/api";
import { useFresh } from "@/lib/hooks";
import { renderReply } from "@/lib/markdown";
import VoiceBuilder from "./VoiceBuilder";

export type Tab = "profile" | "knowledge" | "chat" | "voice";

const TEMPLATES: AgentDraft[] = [
  { tools: true, name: "Coder", emoji: "🧑‍💻", role: "Programming help, debugging and code review",
    instructions: "You are a senior software engineer. Give working code first, then a short explanation. Point out bugs and edge cases plainly. Ask for the language or error message when it is missing. Keep answers tight." },
  { tools: true, name: "Writer", emoji: "✍️", role: "Drafts and polishes emails, posts and messages",
    instructions: "You are a sharp editor and copywriter. Write in clear, natural language and match the tone the user asks for. When editing, return the improved text first, then at most two notes on what changed. Never pad." },
  { tools: true, name: "Tutor", emoji: "🎓", role: "Explains concepts step by step and quizzes the user",
    instructions: "You are a patient tutor. Explain one idea at a time using simple words and a concrete example. After explaining, ask one short question to check understanding. Encourage the user and correct mistakes gently." },
  { tools: true, name: "Coach", emoji: "🏋️", role: "Fitness, habits and daily accountability",
    instructions: "You are an upbeat, practical coach. Give specific, realistic plans and ask about constraints such as time, equipment and injuries. Hold the user accountable for what they said they would do. You are not a doctor; say so for medical questions." },
  { tools: true, name: "Researcher", emoji: "🔎", role: "Summarises documents and answers questions from them",
    instructions: "You are a careful research assistant. Answer from the reference material you have been given and say clearly when it does not cover the question. Prefer short summaries with the key facts and numbers. Never invent sources." },
];

const BLANK: AgentDraft = { name: "", emoji: "🤖", role: "", instructions: "", tools: true };

type Props = {
  agent: Agent | null;              // null while hiring a new one
  tab: Tab;
  onTab: (tab: Tab) => void;
  onClose: () => void;
  onSaved: (agent: Agent, created: boolean) => void;
  onDeleted: (id: number) => void;
};

export default function AgentEditor({ agent, tab, onTab, onClose, onSaved, onDeleted }: Props) {
  const title = agent ? `${agent.emoji} ${agent.name}` : tab === "voice" ? "Build by voice" : "Hire an agent";
  return (
    <>
      <div className="dialog-head">
        <h2>{title}</h2>
        <button className="icon" aria-label="Close" onClick={onClose}>✕</button>
      </div>
      {agent && (
        <nav className="tabs">
          {(["profile", "knowledge", "chat"] as const).map((name) => (
            <button key={name} className={tab === name ? "active" : ""} onClick={() => onTab(name)}>
              {{ profile: "Profile", knowledge: "Knowledge", chat: "Test chat" }[name]}
            </button>
          ))}
        </nav>
      )}
      {tab === "voice" && !agent && <VoiceBuilder onBack={() => onTab("profile")} onDone={(saved) => onSaved(saved, true)} />}
      {tab === "profile" && <Profile agent={agent} onVoice={() => onTab("voice")} onSaved={onSaved} onDeleted={onDeleted} />}
      {tab === "knowledge" && agent && <Knowledge agent={agent} />}
      {tab === "chat" && agent && <Chat agent={agent} />}
    </>
  );
}

// --- profile -----------------------------------------------------------------

function Profile({ agent, onVoice, onSaved, onDeleted }: Pick<Props, "agent" | "onSaved" | "onDeleted"> & { onVoice: () => void }) {
  const [form, setForm] = useState<AgentDraft>(agent ?? BLANK);
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [saving, setSaving] = useState(false);
  const set = (patch: Partial<AgentDraft>) => setForm((current) => ({ ...current, ...patch }));

  async function draft() {
    if (description.trim().length < 4) return setError("Describe the agent in a few words first.");
    setError("");
    setDrafting(true);
    try {
      const drafted = await api<Omit<AgentDraft, "tools">>("POST", "/api/agents/draft", { description: description.trim() });
      setForm((current) => ({ ...drafted, tools: current.tools }));
    } catch (err) {
      setError(errorText(err));
    }
    setDrafting(false);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    const body = { ...form, emoji: form.emoji || "🤖" };
    try {
      const saved = agent
        ? await api<Agent>("PATCH", `/api/agents/${agent.id}`, body)
        : await api<Agent>("POST", "/api/agents", body);
      onSaved(saved, !agent);
    } catch (err) {
      setError(errorText(err));
      setSaving(false);
    }
  }

  function remove() {
    if (agent && confirm(`Delete ${agent.name}? Its knowledge and test chat are removed too.`)) onDeleted(agent.id);
  }

  return (
    <div className="panel">
      {!agent && (
        <div className="draft">
          <label>
            Describe the agent in one sentence
            <div className="row">
              <input
                maxLength={600} placeholder="a patient maths tutor for my 10-year-old" value={description}
                onChange={(e) => setDescription(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); draft(); } }}
              />
              <button className="btn dark" type="button" disabled={drafting} onClick={draft}>
                {drafting ? "Writing…" : "✨ Write it for me"}
              </button>
            </div>
          </label>
          <div className="chips">
            {TEMPLATES.map((template) => (
              <button key={template.name} type="button" onClick={() => setForm(template)}>
                {template.emoji} {template.name}
              </button>
            ))}
          </div>
          <button className="voice-cta" type="button" onClick={onVoice}>
            <span className="voice-cta-orb"><i />🎙</span>
            <span><b>Build it by voice</b><small>I&apos;ll ask a few questions out loud. Just answer.</small></span>
            <span className="go">↗</span>
          </button>
        </div>
      )}
      <form onSubmit={save}>
        <div className="row">
          <label className="emoji-field">
            Emoji<input maxLength={8} value={form.emoji} onChange={(e) => set({ emoji: e.target.value })} />
          </label>
          <label className="grow">
            Name<input maxLength={32} required placeholder="Coder" value={form.name} onChange={(e) => set({ name: e.target.value })} />
          </label>
        </div>
        <label>
          Role <span className="muted small">one line; the dispatcher routes by this</span>
          <input
            maxLength={160} placeholder="Helps with programming questions and code review"
            value={form.role} onChange={(e) => set({ role: e.target.value })}
          />
        </label>
        <label>
          Instructions
          <textarea
            rows={7} maxLength={4000} placeholder="You are…"
            value={form.instructions} onChange={(e) => set({ instructions: e.target.value })}
          />
        </label>
        <label className="toggle">
          <input type="checkbox" checked={form.tools} onChange={(e) => set({ tools: e.target.checked })} />
          <span>
            <b>Give it a computer and the internet</b>
            <small>It can search the web, run commands in a Linux sandbox, and build and host web pages.</small>
          </span>
        </label>
        {agent && form.tools && <Computer agent={agent} />}
        {error && <p className="error">{error}</p>}
        <div className="row between">
          {agent ? <button className="btn ghost danger" type="button" onClick={remove}>Delete agent</button> : <span />}
          <button className="btn accent" type="submit" disabled={saving}>
            {saving ? (agent ? "Saving…" : "Hiring…") : agent ? "Save" : "Hire"}
          </button>
        </div>
      </form>
    </div>
  );
}

/** The state of an agent's sandbox workspace: its live preview link and a way to wipe it. */
function Computer({ agent }: { agent: Agent }) {
  const [state, setState] = useState<ComputerState | null>(null);
  const load = () => api<ComputerState>("GET", `/api/agents/${agent.id}/sandbox`).then(setState, () => {});
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [agent.id]);

  async function wipe() {
    if (!confirm(`Erase all of ${agent.name}'s files and stop anything it is hosting?`)) return;
    await api("DELETE", `/api/agents/${agent.id}/sandbox`).catch(() => {});
    load();
  }

  if (!state) return null;
  if (!state.available) return <p className="muted small computer">The sandbox isn&apos;t running on this server, so only web search is available.</p>;
  if (!state.exists) return <p className="muted small computer">Its workspace is empty. Ask it to build something.</p>;
  return (
    <p className="muted small computer">
      {state.preview
        ? <>Hosting a page: <a href={state.preview} target="_blank" rel="noopener noreferrer">open preview</a>. </>
        : "It has files in its workspace. "}
      <button type="button" className="link small" onClick={wipe}>Erase its files</button>
    </p>
  );
}

// --- knowledge ---------------------------------------------------------------

function Knowledge({ agent }: { agent: Agent }) {
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const isNew = useFresh((docs ?? []).map((d) => d.id));

  const load = () => api<Doc[]>("GET", `/api/agents/${agent.id}/docs`).then(setDocs, (err) => setError(errorText(err)));
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [agent.id]);

  /** Run a change and apply its result locally; only refetch if it failed. */
  async function run(work: () => Promise<(docs: Doc[]) => Doc[]>) {
    try {
      const apply = await work();
      setDocs((current) => apply(current ?? []));
      setError("");
    } catch (err) {
      setError(errorText(err));
      load();
    }
  }

  function addText(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return setError("Paste some text first, or upload a file.");
    run(async () => {
      const doc = await api<Doc>("POST", `/api/agents/${agent.id}/docs`, { title, text: text.trim() });
      setTitle("");
      setText("");
      return (docs) => [...docs, doc];
    });
  }

  function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    run(async () => {
      const doc = await api<Doc>("POST", `/api/agents/${agent.id}/docs/upload`, form);
      return (docs) => [...docs, doc];
    });
  }

  return (
    <div className="panel">
      <p className="muted small">
        Add notes, docs or PDFs. The agent pulls in the relevant parts when answering. You can also send a file straight to the Telegram bot.
      </p>
      <ul className="list">
        {docs?.length === 0 && <li className="muted">Nothing here yet.</li>}
        {docs?.map((doc) => (
          <li key={doc.id} className={isNew(doc.id) ? "new" : ""}>
            <span>{doc.title} · {doc.chars.toLocaleString()} chars</span>
            <button title="Remove" aria-label={`Remove ${doc.title}`} onClick={() => run(async () => {
              await api("DELETE", `/api/docs/${doc.id}`);
              return (docs) => docs.filter((d) => d.id !== doc.id);
            })}>✕</button>
          </li>
        ))}
      </ul>
      <form id="doc-form" onSubmit={addText}>
        <input maxLength={160} placeholder="Title (optional)" value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea rows={5} placeholder="Paste text here…" value={text} onChange={(e) => setText(e.target.value)} />
        {error && <p className="error">{error}</p>}
        <div className="row between">
          <label className="btn ghost file">
            Upload .txt / .md / .pdf
            <input type="file" hidden accept=".txt,.md,.markdown,.pdf,.csv,.json,text/*,application/pdf" onChange={upload} />
          </label>
          <button className="btn accent" type="submit">Add text</button>
        </div>
      </form>
    </div>
  );
}

// --- test chat ---------------------------------------------------------------

function Chat({ agent }: { agent: Agent }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [waiting, setWaiting] = useState(false);
  const log = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let current = true;
    api<ChatMessage[]>("GET", `/api/agents/${agent.id}/chat`).then((history) => { if (current) setMessages(history); }, () => {});
    input.current?.focus();
    return () => { current = false; };
  }, [agent.id]);

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  async function send(e?: React.FormEvent) {
    e?.preventDefault();
    const message = draft.trim();
    if (!message || waiting) return;
    setDraft("");
    // Reset textarea height after clearing
    if (input.current) { input.current.style.height = "auto"; }
    setWaiting(true);
    setMessages((all) => [...all, { role: "user", content: message }, { role: "thinking", content: agent.tools ? `${agent.name} is working… this can take a minute or two` : `${agent.name} is thinking…` }]);
    let reply: ChatMessage[];
    try {
      const data = await api<{ reply: string; steps: string[] }>("POST", `/api/agents/${agent.id}/chat`, { message });
      // Show what it did with its tools, then what it said.
      reply = [...data.steps.map((step): ChatMessage => ({ role: "note", content: step })), { role: "assistant", content: data.reply }];
    } catch (err) {
      reply = [{ role: "note", content: `⚠️ ${errorText(err)}` }];
    }
    setMessages((all) => [...all.slice(0, -1), ...reply]);
    setWaiting(false);
    input.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Shift+Enter → insert newline (default textarea behaviour)
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function handleDraftChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setDraft(e.target.value);
    // Auto-grow: reset then grow to fit content, capped at 200px
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }

  async function reset() {
    await api("DELETE", `/api/agents/${agent.id}/chat`).catch(() => {});
    setMessages([]);
  }

  return (
    <div className="panel">
      <div className="chat-log" ref={log}>
        {messages.length === 0 && <div className="msg note">Say hi to {agent.name}. This is a private test chat.</div>}
        {messages.map((message, i) =>
          message.role === "assistant" ? (
            <div key={i} className="msg assistant" dangerouslySetInnerHTML={{ __html: renderReply(message.content) }} />
          ) : (
            <div key={i} className={`msg ${message.role === "thinking" ? "assistant thinking" : message.role}`}>{message.content}</div>
          ),
        )}
      </div>
      <form className="chat-form" onSubmit={send}>
        <textarea
          ref={input}
          className="chat-input"
          maxLength={6000}
          placeholder="Say something… (Shift+Enter for new line)"
          autoComplete="off"
          rows={1}
          value={draft}
          onChange={handleDraftChange}
          onKeyDown={handleKeyDown}
        />
        <div className="chat-actions">
          <button className="btn ghost" type="button" title="Clear conversation" onClick={reset}>Reset</button>
          <button className="btn accent" type="submit" disabled={waiting}>Send</button>
        </div>
      </form>
    </div>
  );
}
