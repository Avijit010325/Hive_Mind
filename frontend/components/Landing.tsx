"use client";

import { memo, useEffect, useRef } from "react";
import { startLanding } from "@/lib/landing";

type Props = { onAuth: (mode: "signup" | "login") => void };

/**
 * The marketing page. Its markup is static; all motion is driven by
 * `startLanding`, which animates these nodes directly. The component is
 * memoised so React never re-renders over that live DOM.
 */
function Landing({ onAuth }: Props) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => (root.current ? startLanding(root.current) : undefined), []);

  return (
    <div ref={root}>
      <nav className="nav">
        <a className="brand" href="#top">HiveMind</a>
        <div className="nav-links">
          <a href="#routing">Dispatcher</a>
          <a href="#features">Crew</a>
        </div>
        <div className="nav-cta">
          <button className="btn dark" onClick={() => onAuth("login")}>Login</button>
          <button className="btn accent" onClick={() => onAuth("signup")}>Sign up</button>
        </div>
      </nav>

      <header className="hero" id="top">
        <canvas id="stars" className="stars" aria-hidden="true" />
        <div className="haze" aria-hidden="true" />

        <div className="hero-copy">
          <h1 className="rise" style={{ "--d": 0 } as React.CSSProperties}>One Chat.<br />A Whole Crew.</h1>
          <p className="lede rise" style={{ "--d": 1 } as React.CSSProperties}>
            Hire AI agents in a sentence and talk to all of them from a single Telegram chat, powered by Gemma.
          </p>
          <button className="btn accent big rise" style={{ "--d": 2 } as React.CSSProperties} onClick={() => onAuth("signup")}>
            Hire your crew
          </button>
        </div>

        <div className="orb-wrap rise" style={{ "--d": 3 } as React.CSSProperties} id="orb" aria-hidden="true">
          <canvas id="orb-gl" />
          <div className="orb"><i className="orb-hue" /><i className="orb-blue" /><i className="orb-core" /><i className="orb-rim" /></div>
        </div>

        <div className="float float-left rise" style={{ "--d": 5 } as React.CSSProperties} aria-hidden="true">
          <div className="float-head"><span>Dispatcher</span><i className="go">↗</i></div>
          <div className="float-body"><b>Routing to<br /><span id="float-agent">🍳 Chef</span></b><small>auto</small></div>
        </div>
        <div className="float float-right rise" style={{ "--d": 6 } as React.CSSProperties} aria-hidden="true">
          <div className="float-head"><span>New agent, hired in</span><i className="go">↗</i></div>
          <div className="float-big">1 sentence</div>
          <div className="float-bar"><i /></div>
        </div>
      </header>

      <section className="section" id="routing">
        <p className="eyebrow reveal">The dispatcher</p>
        <h2 className="reveal">Every message finds<br />the right mind.</h2>
        <div className="stage reveal" id="stage" aria-hidden="true">
          <svg viewBox="0 0 1000 620" preserveAspectRatio="none">
            <path className="wire in" id="wire-in" pathLength={1} d="M500 96 L500 246" />
            <path className="wire" pathLength={1} d="M500 300 C 500 440, 120 380, 120 506" />
            <path className="wire" pathLength={1} d="M500 300 C 500 430, 310 400, 310 506" />
            <path className="wire" pathLength={1} d="M500 300 L 500 506" />
            <path className="wire" pathLength={1} d="M500 300 C 500 430, 690 400, 690 506" />
            <path className="wire" pathLength={1} d="M500 300 C 500 440, 880 380, 880 506" />
          </svg>
          <div className="stage-msg"><span id="stage-text" /><i className="caret" /></div>
          <div className="core" id="core">
            <i className="ring r1" /><i className="ring r2" /><i className="ring r3" /><i className="sweep" /><i className="dot" />
          </div>
          <div className="node" style={{ left: "12%" }}><span>✨</span><b>Sidekick</b></div>
          <div className="node" style={{ left: "31%" }}><span>🍳</span><b>Chef</b></div>
          <div className="node" style={{ left: "50%" }}><span>🧑‍💻</span><b>Coder</b></div>
          <div className="node" style={{ left: "69%" }}><span>🎓</span><b>Tutor</b></div>
          <div className="node" style={{ left: "88%" }}><span>✍️</span><b>Writer</b></div>
          <i className="pulse" id="pulse" />
        </div>
      </section>

      <section className="section" id="features">
        <p className="eyebrow reveal">The crew</p>
        <h2 className="reveal">Built around the chat.</h2>
        <div className="bento">
          <article className="glass tile t-hire reveal">
            <div className="art hire-art" aria-hidden="true">
              <div className="hire-input"><b>/hire</b><span id="hire-typed" /><i className="caret" /></div>
              <div className="hire-beam"><i /></div>
              <div className="hire-card" id="hire-card">
                <span className="hire-emoji" id="hire-emoji">🇪🇸</span>
                <div>
                  <b id="hire-name">Profe</b><small id="hire-role">Strict Spanish tutor</small>
                  <i className="sk" /><i className="sk short" />
                </div>
              </div>
            </div>
            <h3>Hire in a sentence</h3>
          </article>

          <article className="glass tile t-file reveal">
            <div className="art file-art" aria-hidden="true">
              <div className="doc"><i /><i /><i /><i /><i /><i /><span className="scanline" /></div>
              <div className="stream"><i /><i /><i /><i /><i /></div>
              <div className="brain"><span>🎓</span></div>
            </div>
            <h3>Teach it with a file</h3>
          </article>

          <article className="glass tile t-mem reveal">
            <div className="art mem-art" aria-hidden="true">
              <svg viewBox="0 0 300 190" preserveAspectRatio="none">
                <path d="M150 95 L46 38" /><path d="M150 95 L254 38" /><path d="M150 95 L46 152" /><path d="M150 95 L254 152" />
              </svg>
              <span className="mem-core">🧠</span>
              <span className="mem-node" style={{ left: "15.3%", top: "20%" }}>✨</span>
              <span className="mem-node" style={{ left: "84.7%", top: "20%" }}>🍳</span>
              <span className="mem-node" style={{ left: "15.3%", top: "80%" }}>🧑‍💻</span>
              <span className="mem-node" style={{ left: "84.7%", top: "80%" }}>✍️</span>
            </div>
            <h3>One shared memory</h3>
          </article>

          <article className="glass tile t-chat reveal" aria-label="Animated example of a Telegram conversation with several agents">
            <div className="demo">
              <div className="demo-bar">
                <span className="demo-avatar">🛟</span>
                <div><b>HiveMind</b><small id="demo-status">bot</small></div>
              </div>
              <div className="demo-crew" id="demo-crew" />
              <div className="demo-log" id="demo-log" />
              <div className="demo-input"><span id="demo-typed" /><span className="caret" /></div>
            </div>
            <h3>All in one Telegram chat</h3>
          </article>
        </div>
      </section>

      <section className="cta reveal">
        <div className="cta-glow" aria-hidden="true" />
        <h2>Your crew is<br />one sentence away.</h2>
        <button className="btn accent big" onClick={() => onAuth("signup")}>Hire your crew</button>
      </section>

      <footer className="foot">
        <span className="brand">HiveMind</span>
        <span>Open source under MIT · Self-host with Docker Compose · Powered only by Google&apos;s Gemma</span>
      </footer>
    </div>
  );
}

export default memo(Landing);
