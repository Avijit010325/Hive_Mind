"use client";

import { useEffect, useRef, useState } from "react";
import { api, errorText, type Agent } from "@/lib/api";
import Swap from "./Swap";

/**
 * Builds an agent from a spoken interview. Each question is read aloud, a
 * 4-second countdown runs, then the microphone records until 3 seconds of
 * silence. Nothing advances until the user presses Next, so they can re-record
 * or edit the transcript first. Gemma only sees the finished answers, and
 * typing always works as a fallback.
 */

const QUESTIONS = [
  "What should this agent help you with?",
  "How should it talk to you? For example friendly, strict, or straight to the point.",
  "Is there anything it should always do, or never do?",
  "Last one. Do you want to give it a name?",
];
const COUNTDOWN_SECONDS = 4;
const SILENCE_MS = 3000;
const NO_MIC_SUPPORT = "This browser doesn't support voice input. Type your answer, then press Next.";
const NO_MIC_PERMISSION = "Microphone access was denied. Click \"Allow microphone\" below, or type your answer.";
const NO_MIC_NETWORK = "Voice recognition needs an internet connection. Type your answer instead.";
const NO_MIC_GENERIC = "Couldn't reach the microphone. Type your answer, then press Next.";

type Phase = "idle" | "speaking" | "countdown" | "listening" | "review" | "thinking" | "done" | "failed";
type Turn = { question: string; answer: string };

// The Web Speech API is not in TypeScript's DOM types.
type SpeechResult = { isFinal: boolean; 0: { transcript: string } };
type Recognizer = {
  lang: string; interimResults: boolean; continuous: boolean;
  onstart: (() => void) | null; onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onresult: ((event: { resultIndex: number; results: ArrayLike<SpeechResult> }) => void) | null;
  start(): void; stop(): void; abort(): void;
};
const recognizerClass = (): (new () => Recognizer) | undefined => {
  const w = window as unknown as Record<string, new () => Recognizer>;
  return w.SpeechRecognition || w.webkitSpeechRecognition;
};

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function speak(text: string): Promise<void> {
  if (!("speechSynthesis" in window)) return Promise.resolve();
  return new Promise((resolve) => {
    let finished = false;
    const finish = () => { if (!finished) { finished = true; clearTimeout(guard); resolve(); } };
    // Some browsers never fire `end` (no voices installed, tab in background), so never wait forever.
    const guard = setTimeout(finish, 2500 + text.length * 90);
    try {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.03;
      utterance.onend = finish;
      utterance.onerror = finish;
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
    } catch {
      finish();
    }
  });
}

type Props = { onDone: (agent: Agent) => void; onBack: () => void };

export default function VoiceBuilder({ onDone, onBack }: Props) {
  const [step, setStep] = useState(0);
  const [phase, setPhaseState] = useState<Phase>("idle");
  const [caption, setCaption] = useState("");
  const [answer, setAnswerState] = useState("");
  const [count, setCount] = useState(COUNTDOWN_SECONDS);
  const [ringKey, setRingKey] = useState(0);
  const [headline, setHeadline] = useState<string | null>(null);
  const [permDenied, setPermDenied] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);

  // Live control state. `take` is bumped whenever the current speak/countdown/
  // recording is abandoned, so any async step still in flight sees it is stale.
  const ctl = useRef({ take: 0, rec: null as Recognizer | null, answers: [] as Turn[], typedOnly: false, step: 0, answer: "", phase: "idle" as Phase });
  const c = ctl.current;

  const setPhase = (next: Phase, text?: string) => {
    c.phase = next;
    setPhaseState(next);
    if (text !== undefined) setCaption(text);
  };
  const setAnswer = (value: string) => { c.answer = value; setAnswerState(value); };
  const isLast = () => c.step >= QUESTIONS.length - 1;

  /** Stop speaking, counting down and recording, without triggering any follow-up step. */
  function halt() {
    c.take += 1;
    const rec = c.rec;
    c.rec = null;
    if (rec) { rec.onend = null; try { rec.abort(); } catch { /* already stopped */ } }
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  }

  async function listen() {
    const Rec = recognizerClass();
    if (!Rec) {
      c.typedOnly = true;
      setPermDenied(false);
      setPhase("idle", NO_MIC_SUPPORT);
      field.current?.focus();
      return;
    }

    // Capture take BEFORE any await so we detect if halt() was called during the dialog
    const take = c.take;

    // Explicitly request mic permission before starting the recognizer.
    // This ensures the browser shows its permission popup properly.
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // Stop the tracks immediately — we only needed the permission grant.
      stream.getTracks().forEach((t) => t.stop());
      setPermDenied(false);
      console.log("[VoiceBuilder] Mic permission granted");
    } catch (err) {
      const name = (err as { name?: string })?.name ?? "";
      console.warn("[VoiceBuilder] getUserMedia failed:", name, err);
      c.typedOnly = true;
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        setPermDenied(true);
        setPhase("idle", NO_MIC_PERMISSION);
      } else {
        setPhase("idle", NO_MIC_GENERIC);
      }
      field.current?.focus();
      return;
    }

    // If halt() was called while we waited for permission, abort
    if (take !== c.take) return;

    const rec = new Rec();
    c.rec = rec;
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true;
    rec.continuous = true;
    let finalText = "";
    let quiet: ReturnType<typeof setTimeout> | undefined;
    const settle = () => {
      clearTimeout(quiet);
      quiet = setTimeout(() => { try { rec.stop(); } catch { /* ended */ } }, SILENCE_MS);
    };
    rec.onstart = () => {
      setAnswer("");
      setPhase("listening", "Recording. I'll stop after 3 seconds of silence, or tap the orb.");
      settle();
    };
    rec.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const piece = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += `${piece} `; else interim += piece;
      }
      setAnswer((finalText + interim).trim());
      settle();
    };
    rec.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        c.typedOnly = true;
        setPermDenied(true);
        setPhase("idle", NO_MIC_PERMISSION);
      } else if (event.error === "network") {
        // Network errors are transient — don't permanently disable mic
        setPhase("idle", NO_MIC_NETWORK);
      } else if (event.error === "audio-capture") {
        c.typedOnly = true;
        setPhase("idle", NO_MIC_GENERIC);
      }
    };
    rec.onend = () => {
      clearTimeout(quiet);
      if (c.rec !== rec || take !== c.take) return;
      c.rec = null;
      if (c.answer.trim()) {
        setPhase("review", `Edit it if I misheard, or tap the orb to record again. Press ${isLast() ? "Build agent" : "Next"} when you're happy.`);
      } else if (c.typedOnly) {
        setPhase("idle", permDenied ? NO_MIC_PERMISSION : NO_MIC_GENERIC);
      } else {
        setPhase("idle", "I didn't hear anything. Tap the orb to try again, or type your answer.");
      }
    };
    try {
      rec.start();
    } catch {
      c.typedOnly = true;
      c.rec = null;
      setPhase("idle", NO_MIC_GENERIC);
    }
  }

  async function countdown() {
    halt();
    const take = c.take;
    setPhase("countdown", "Get ready. Recording starts when the ring completes.");
    setRingKey((key) => key + 1);  // remounts the ring so its animation restarts from full
    for (let left = COUNTDOWN_SECONDS; left > 0; left--) {
      setCount(left);
      await pause(1000);
      if (take !== c.take) return;
    }
    await listen();
  }

  async function ask(index: number) {
    halt();
    const take = c.take;
    c.step = index;
    setStep(index);
    setAnswer("");
    setHeadline(null);
    setPhase("speaking", "");
    await speak(QUESTIONS[index]);
    if (take !== c.take) return;
    if (c.typedOnly) {
      setPhase("idle", recognizerClass() ? (permDenied ? NO_MIC_PERMISSION : NO_MIC_GENERIC) : NO_MIC_SUPPORT);
      field.current?.focus();
    } else {
      countdown();
    }
  }

  async function build() {
    halt();
    const take = c.take;
    setHeadline("Building your agent…");
    setPhase("thinking", "Gemma is writing its name, role and instructions.");
    let saved: Agent;
    try {
      saved = await api<Agent>("POST", "/api/agents/interview", { answers: c.answers });
    } catch (err) {
      if (take !== c.take) return;
      setHeadline("That didn't work.");
      setPhase("failed", errorText(err));
      return;
    }
    if (take !== c.take) return;
    setHeadline(`Meet ${saved.emoji} ${saved.name}`);
    setPhase("done", saved.role);
    await speak(`Meet ${saved.name}. ${saved.role.replace(/[.!?\s]+$/, "")}. It's in your crew now.`);
    if (take !== c.take) return;
    onDone(saved);
  }

  function commit(text: string) {
    c.answers.push({ question: QUESTIONS[c.step], answer: text.slice(0, 1000) });
    if (c.step + 1 < QUESTIONS.length) ask(c.step + 1); else build();
  }

  function tapOrb() {
    if (["thinking", "done", "failed"].includes(c.phase)) return;
    if (c.phase === "listening" && c.rec) { try { c.rec.stop(); } catch { /* ended */ } return; }
    if (c.phase === "countdown") { halt(); setPhase("idle", "Paused. Tap the orb to record, or type your answer."); return; }
    if (c.typedOnly) { halt(); setPhase("idle", permDenied ? NO_MIC_PERMISSION : NO_MIC_GENERIC); field.current?.focus(); return; }
    countdown();  // from speaking, idle or review: (re)record this answer
  }

  function edit(value: string) {
    // Typing takes over from the microphone so the transcript never overwrites the user's edits.
    if (["speaking", "countdown", "listening"].includes(c.phase)) {
      halt();
      setPhase("review", `Press ${isLast() ? "Build agent" : "Next"} when you're happy, or tap the orb to record instead.`);
    }
    setAnswer(value);
  }

  // When permDenied changes to false (user re-granted), reset typedOnly so mic works again.
  useEffect(() => {
    if (!permDenied) c.typedOnly = !recognizerClass();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [permDenied]);

  useEffect(() => {
    c.typedOnly = !recognizerClass();
    c.answers = [];
    ask(0);
    return halt;
    // Runs once per mount: the interview owns its own state from here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const busy = phase === "thinking" || phase === "done";
  const icon = phase === "countdown" ? String(count) : ({ speaking: "🔊", thinking: "✨", done: "✓" } as Record<string, string>)[phase] || "🎙";
  const last = step >= QUESTIONS.length - 1;
  const hasSpeechAPI = !!recognizerClass();
  const hasMediaDevices = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

  return (
    <div className="voice panel">
      <button className="vorb" data-state={phase} type="button" aria-label="Start or stop recording" onClick={tapOrb}>
        <span className="vorb-core"><i className="l-listen" /><i className="l-think" /></span>
        <span className="vorb-rings"><i /><i /><i /></span>
        <svg className="vorb-count" viewBox="0 0 100 100" aria-hidden="true">
          <circle className="track" cx="50" cy="50" r="47" />
          <circle key={ringKey} className={phase === "countdown" ? "bar run" : "bar"} cx="50" cy="50" r="47" />
        </svg>
        <Swap className="vorb-icon" text={icon} />
      </button>
      <div className="voice-dots" aria-hidden="true">
        {QUESTIONS.map((_, i) => (
          <i key={i} className={busy || phase === "failed" || i < step ? "done" : i === step ? "now" : ""} />
        ))}
      </div>
      <Swap as="h3" id="voice-question" text={headline ?? QUESTIONS[step]} />
      <textarea
        ref={field} id="voice-answer" rows={3} maxLength={1000} disabled={busy} value={answer}
        placeholder="Your answer appears here. You can also type or edit it."
        onChange={(e) => edit(e.target.value)}
      />
      <Swap as="p" id="voice-caption" className="muted small" text={caption} />


      {/* Diagnostic panel — shown when voice isn't working */}
      {(permDenied || !hasSpeechAPI || !hasMediaDevices) && (
        <div className="mic-diag">
          <p className="mic-diag-title">🔍 Voice Check</p>
          <ul className="mic-diag-list">
            <li data-ok={hasSpeechAPI}>
              {hasSpeechAPI ? "✅" : "❌"} Web Speech API
              {!hasSpeechAPI && <span> — use Chrome or Edge (not Firefox/Safari)</span>}
            </li>
            <li data-ok={hasMediaDevices}>
              {hasMediaDevices ? "✅" : "❌"} Microphone API available
            </li>
            <li data-ok={!permDenied}>
              {!permDenied ? "✅" : "❌"} Microphone permission
              {permDenied && <span> — blocked by browser or macOS</span>}
            </li>
          </ul>
          {permDenied && (
            <div className="mic-diag-steps">
              <b>Fix steps:</b>
              <ol>
                <li>Click the <b>🔒 lock icon</b> in your browser address bar</li>
                <li>Set <b>Microphone → Allow</b></li>
                <li>On macOS: open <b>System Settings → Privacy &amp; Security → Microphone</b> and enable your browser</li>
                <li>Then click the button below</li>
              </ol>
              <button
                type="button"
                className="btn ghost mic-retry"
                onClick={async () => {
                  try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    stream.getTracks().forEach((t) => t.stop());
                    c.typedOnly = false;
                    setPermDenied(false);
                    setCaption("Microphone access granted! Tap the orb to record.");
                    countdown();
                  } catch {
                    setCaption(NO_MIC_PERMISSION);
                  }
                }}
              >
                🎙 Retry microphone access
              </button>
            </div>
          )}
        </div>
      )}

      <div className="row between">
        <button className="btn ghost" type="button" onClick={onBack}>Back</button>
        <div className="row">
          <button
            id="voice-skip" type="button"
            className={`btn ghost ${busy || phase === "failed" || step === 0 ? "gone" : ""}`}
            onClick={() => commit("skip")}
          >
            Skip
          </button>
          <button
            id="voice-next" type="button" className="btn accent"
            disabled={busy || (phase !== "failed" && !answer.trim())}
            onClick={() => (phase === "failed" ? build() : answer.trim() && commit(answer.trim()))}
          >
            {phase === "failed" ? "Try again" : last ? "Build agent" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
