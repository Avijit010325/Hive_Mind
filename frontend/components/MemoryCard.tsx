"use client";

import { useEffect, useState } from "react";
import { api, errorText, type Memory } from "@/lib/api";
import { useFresh } from "@/lib/hooks";

export default function MemoryCard() {
  const [items, setItems] = useState<Memory[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const isNew = useFresh(items.map((item) => item.id));

  const load = () => api<Memory[]>("GET", "/api/memory").then(setItems, (err) => setError(errorText(err)));
  useEffect(() => { load(); }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    try {
      const created = await api<Memory>("POST", "/api/memory", { text: text.trim() });
      setItems((all) => [...all, created]);
      setText("");
      setError("");
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function remove(id: number) {
    setItems((all) => all.filter((item) => item.id !== id));  // gone at once; restored below if it fails
    await api("DELETE", `/api/memory/${id}`).catch(load);
  }

  return (
    <div className="card">
      <h3>Shared memory</h3>
      <p className="muted small">Facts every agent in your crew knows about you.</p>
      <ul className="list">
        {items.map((item) => (
          <li key={item.id} className={isNew(item.id) ? "new" : ""}>
            <span>{item.text}</span>
            <button title="Forget" aria-label="Forget this" onClick={() => remove(item.id)}>✕</button>
          </li>
        ))}
      </ul>
      {error && <p className="error">{error}</p>}
      <form className="row" onSubmit={add}>
        <input maxLength={400} placeholder="e.g. I'm vegetarian" required value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn ghost" type="submit">Add</button>
      </form>
    </div>
  );
}
