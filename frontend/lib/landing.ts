/**
 * Landing page motion: the WebGL orb, the star field and the animated
 * illustrations. Everything here is imperative by nature (canvases, timed
 * sequences), so it lives outside React and is started from one effect.
 * `startLanding` returns a function that stops and cleans up all of it.
 */
import { prefersStill } from "./motion";

const FRAG = `
precision highp float;
uniform vec2 uRes; uniform float uTime; uniform vec2 uMouse;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y);
}
float fbm(vec2 p) { float v = 0.0, a = 0.5; for (int i = 0; i < 4; i++) { v += a * noise(p); p = p * 2.03 + 11.7; a *= 0.5; } return v; }
float smin(float a, float b, float k) { float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0); return mix(b, a, h) - k * h * (1.0 - h); }

// Three drifting lobes fused into one body: a tall main mass, a low left shoulder, a right flank.
float body(vec2 p) {
  float t = uTime;
  float d = length(p - vec2(0.16 + 0.03 * sin(t * 0.31), -0.04 + 0.03 * cos(t * 0.27))) - 0.80;
  d = smin(d, length(p - vec2(-0.66 + 0.05 * sin(t * 0.23), -0.60 + 0.04 * sin(t * 0.37))) - 0.42, 0.50);
  d = smin(d, length(p - vec2(0.62 + 0.04 * cos(t * 0.29), -0.42 + 0.03 * sin(t * 0.21))) - 0.58, 0.30);
  return d + 0.016 * sin(p.x * 5.0 + t * 0.6) * sin(p.y * 4.0 - t * 0.45);
}

void main() {
  vec2 p = (2.0 * gl_FragCoord.xy - uRes) / uRes.y;
  float d = body(p);
  vec2 e = vec2(0.004, 0.0);
  vec2 n = normalize(vec2(body(p + e.xy) - body(p - e.xy), body(p + e.yx) - body(p - e.yx)) + 1e-6);

  vec3 gold = vec3(1.0, 0.74, 0.34), cream = vec3(1.0, 0.95, 0.84), blue = vec3(0.16, 0.36, 1.0);
  vec2 keyDir = normalize(vec2(-0.75, 0.25) + uMouse * 0.35);   // bright key from the left
  vec2 fillDir = normalize(vec2(0.85, 0.5) - uMouse * 0.2);     // warm fill from the right
  float key = pow(max(dot(n, keyDir), 0.0), 1.6);
  float fill = pow(max(dot(n, fillDir), 0.0), 2.2);

  float depth = max(-d, 0.0);
  float flow = fbm(p * 1.7 + vec2(uTime * 0.05, -uTime * 0.035) + n * 0.25);
  float film = 0.5 + 0.5 * sin(flow * 9.0 + depth * 14.0 + uTime * 0.25);   // thin-film bands

  vec3 col = vec3(0.016, 0.018, 0.03);
  col += blue * 0.85 * exp(-length((p - vec2(0.22, -0.42)) * vec2(1.0, 1.3)) * 2.6);          // blue heart
  col += blue * 0.28 * film * exp(-depth * 3.2) * smoothstep(0.5, -0.5, p.y);
  col += gold * (0.10 + 1.05 * fill + 0.75 * key) * exp(-depth * 5.6) * (0.5 + 0.65 * film); // warm body sheen
  col += gold * 0.42 * smoothstep(0.42, 0.78, flow) * exp(-depth * 2.4) * (0.25 + key + fill);    // drifting gold caustics
  col += mix(gold, cream, 0.6) * (0.08 + 1.5 * key + 0.7 * fill) * exp(-depth * 22.0);       // glassy rim
  col += cream * (0.30 + 2.2 * key + 0.6 * fill) * exp(-depth * 80.0);                       // hairline edge

  float aa = smoothstep(0.004, -0.004, d);
  vec2 uv = gl_FragCoord.xy / uRes;
  float frame = smoothstep(0.0, 0.14, uv.x) * smoothstep(1.0, 0.86, uv.x) * smoothstep(1.0, 0.84, uv.y);   // never show the canvas edge
  float halo = ((0.10 + 0.55 * key + 0.22 * fill) * exp(-max(d, 0.0) * 24.0) + 0.05 * exp(-max(d, 0.0) * 5.0)) * frame;
  vec3 outside = mix(gold, cream, 0.6) * halo;
  float alpha = max(aa, clamp(halo, 0.0, 1.0));
  gl_FragColor = vec4(mix(outside, col, aa), alpha);   // premultiplied
}`;

const ROUTES: [string, number][] = [
  ["what can I cook with paneer tonight?", 1],
  ["why is my build failing?", 2],
  ["explain photosynthesis like I'm ten", 3],
  ["make this email sound friendlier", 4],
  ["plan my Saturday", 0],
];

const HIRES: [string, string, string, string][] = [
  ["a strict Spanish tutor", "🇪🇸", "Profe", "Strict Spanish tutor"],
  ["a calm meditation guide", "🧘", "Serene", "Gentle mindfulness guide"],
  ["a ruthless code reviewer", "🧑‍💻", "Lint", "Finds every bug in your PR"],
  ["a chef for busy weeknights", "🍳", "Tadka", "Fifteen-minute dinners"],
];

const CREW: [string, string][] = [["✨", "Sidekick"], ["🍳", "Chef"], ["🧑‍💻", "Coder"]];

type ChatStep = { say: string; reply: string; who?: string; emoji?: string; hire?: [string, string] };
const CHAT: ChatStep[] = [
  { say: "what can I cook with paneer tonight?", who: "Chef", emoji: "🍳", reply: "Paneer bhurji. One pan, fifteen minutes. Want the steps?" },
  { say: "why is this Python loop so slow?", who: "Coder", emoji: "🧑‍💻", reply: "You're searching a list inside the loop. Turn it into a set and lookups become instant." },
  { say: "/hire a strict Spanish tutor", hire: ["🇪🇸", "Profe"], reply: "🎉 Hired 🇪🇸 Profe: strict Spanish tutor who corrects your grammar" },
  { say: "how do I say “I'm running late”?", who: "Profe", emoji: "🇪🇸", reply: "«Voy con retraso.» Say it back to me, and roll that double r." },
];

class Stopped extends Error {}

export function startLanding(root: HTMLElement): () => void {
  const still = prefersStill();
  let alive = true;
  const cleanups: (() => void)[] = [];
  const get = <T extends Element = HTMLElement>(id: string) => root.querySelector<T>(`#${id}`);
  const on = <K extends keyof WindowEventMap>(type: K, handler: (e: WindowEventMap[K]) => void) => {
    window.addEventListener(type, handler, { passive: true });
    cleanups.push(() => window.removeEventListener(type, handler));
  };
  const observe = <T extends { disconnect(): void }>(observer: T): T => { cleanups.push(() => observer.disconnect()); return observer; };

  /** Sleep that aborts the calling sequence once the page is torn down. */
  const wait = (ms: number) =>
    new Promise<void>((resolve, reject) => {
      setTimeout(() => (alive ? resolve() : reject(new Stopped())), ms);
    });
  const sequence = (run: () => Promise<void>) => {
    run().catch((err) => { if (!(err instanceof Stopped)) console.error(err); });
  };
  /** Resolves once `node` is on screen and the tab is visible. */
  const watch = (node: Element) => {
    let inView = false;
    observe(new IntersectionObserver(([entry]) => { inView = entry.isIntersecting; }, { threshold: 0.15 })).observe(node);
    return async () => { while (!(inView && !document.hidden)) await wait(400); };
  };
  const type = async (node: Element, text: string, speed = 30) => {
    node.textContent = "";
    for (const ch of text) { node.textContent += ch; await wait(speed + Math.random() * speed); }
  };
  const frames = (draw: (time: number) => void) => {
    let id = 0;
    const loop = (time: number) => { draw(time); if (alive && !still) id = requestAnimationFrame(loop); };
    id = requestAnimationFrame(loop);
    cleanups.push(() => cancelAnimationFrame(id));
  };

  // --- reveal on scroll, nav, tile spotlight --------------------------------
  const targets = root.querySelectorAll<HTMLElement>(".reveal");
  if (still) {
    targets.forEach((t) => t.classList.add("in"));
  } else {
    const seen = new IntersectionObserver((entries) => {
      entries.forEach((entry, i) => {
        if (!entry.isIntersecting) return;
        (entry.target as HTMLElement).style.transitionDelay = `${Math.min(i, 4) * 80}ms`;
        entry.target.classList.add("in");
        seen.unobserve(entry.target);
      });
    }, { threshold: 0.12 });
    observe(seen);
    targets.forEach((t) => seen.observe(t));
  }

  const nav = root.querySelector(".nav");
  const onScroll = () => nav?.classList.toggle("scrolled", window.scrollY > 24);
  on("scroll", onScroll);
  onScroll();

  root.querySelectorAll<HTMLElement>(".tile").forEach((tile) => {
    const move = (e: PointerEvent) => {
      const box = tile.getBoundingClientRect();
      tile.style.setProperty("--mx", `${e.clientX - box.left}px`);
      tile.style.setProperty("--my", `${e.clientY - box.top}px`);
    };
    tile.addEventListener("pointermove", move);
    cleanups.push(() => tile.removeEventListener("pointermove", move));
  });

  // --- star field ------------------------------------------------------------
  const sky = get<HTMLCanvasElement>("stars");
  const ctx = sky?.getContext("2d");
  if (sky && ctx) {
    let stars: { x: number; y: number; r: number; phase: number; speed: number }[] = [];
    const draw = (time: number) => {
      if (document.hidden && !still) return;
      ctx.clearRect(0, 0, sky.clientWidth, sky.clientHeight);
      for (const star of stars) {
        ctx.globalAlpha = still ? 0.6 : 0.25 + 0.6 * Math.abs(Math.sin(star.phase + (time / 1000) * star.speed));
        ctx.beginPath();
        ctx.arc(star.x, star.y, star.r, 0, 6.28);
        ctx.fillStyle = "#fff";
        ctx.fill();
      }
    };
    const size = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      sky.width = sky.clientWidth * ratio;
      sky.height = sky.clientHeight * ratio;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      const count = Math.round((sky.clientWidth * sky.clientHeight) / 9000);
      stars = Array.from({ length: count }, () => ({
        x: Math.random() * sky.clientWidth, y: Math.random() * sky.clientHeight,
        r: Math.random() * 1.1 + 0.3, phase: Math.random() * 6.28, speed: Math.random() * 0.9 + 0.3,
      }));
      if (still) draw(0);
    };
    observe(new ResizeObserver(size)).observe(sky);
    frames(draw);
  }

  // --- the orb: a liquid, iridescent blob rendered in a fragment shader ------
  const orb = get("orb");
  const canvas = get<HTMLCanvasElement>("orb-gl");
  const startOrb = () => {
    if (!orb || !canvas) return;
    const gl = canvas.getContext("webgl", { premultipliedAlpha: true, antialias: false, alpha: true });
    if (!gl) return;
    const compile = (kind: number, source: string) => {
      const shader = gl.createShader(kind);
      if (!shader) return null;
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
    };
    const vert = compile(gl.VERTEX_SHADER, "attribute vec2 a; void main() { gl_Position = vec4(a, 0.0, 1.0); }");
    const frag = compile(gl.FRAGMENT_SHADER, FRAG);
    const program = gl.createProgram();
    if (!vert || !frag || !program) return;
    gl.attachShader(program, vert);
    gl.attachShader(program, frag);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const attr = gl.getAttribLocation(program, "a");
    gl.enableVertexAttribArray(attr);
    gl.vertexAttribPointer(attr, 2, gl.FLOAT, false, 0, 0);
    const uRes = gl.getUniformLocation(program, "uRes");
    const uTime = gl.getUniformLocation(program, "uTime");
    const uMouse = gl.getUniformLocation(program, "uMouse");
    orb.classList.add("gl");   // hide the CSS fallback only once the shader really works
    cleanups.push(() => orb.classList.remove("gl"));

    const mouse = { x: 0, y: 0, tx: 0, ty: 0 };
    on("pointermove", (e) => {
      mouse.tx = (e.clientX / window.innerWidth - 0.5) * 2;
      mouse.ty = -(e.clientY / window.innerHeight - 0.5) * 2;
    });
    let onScreen = true;
    observe(new IntersectionObserver(([entry]) => { onScreen = entry.isIntersecting; })).observe(canvas);
    const draw = (time: number) => {
      if (!still && (!onScreen || document.hidden)) return;
      mouse.x += (mouse.tx - mouse.x) * 0.04;
      mouse.y += (mouse.ty - mouse.y) * 0.04;
      gl.uniform2f(uRes, canvas.width, canvas.height);
      gl.uniform1f(uTime, still ? 12 : time / 1000);
      gl.uniform2f(uMouse, mouse.x, mouse.y);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };
    const size = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas.width = Math.max(1, Math.round(canvas.clientWidth * ratio));
      canvas.height = Math.max(1, Math.round(canvas.clientHeight * ratio));
      gl.viewport(0, 0, canvas.width, canvas.height);
      if (still) draw(0);
    };
    observe(new ResizeObserver(size)).observe(canvas);
    frames(draw);
  };
  try { startOrb(); } catch (err) { console.warn("Orb shader unavailable, using the CSS orb.", err); }

  if (orb && !still && window.matchMedia("(pointer: fine)").matches) {
    on("pointermove", (e) => {
      orb.style.setProperty("--px", `${(e.clientX / window.innerWidth - 0.5) * 30}px`);
      orb.style.setProperty("--py", `${(e.clientY / window.innerHeight - 0.5) * 18}px`);
    });
  }

  const floatAgent = get("float-agent");
  if (floatAgent && !still) {
    const names = ["🍳 Chef", "🧑‍💻 Coder", "🎓 Tutor", "✍️ Writer", "✨ Sidekick"];
    sequence(async () => {
      for (let i = 1; ; i = (i + 1) % names.length) {
        await wait(2600);
        floatAgent.classList.add("swap");
        await wait(350);
        floatAgent.textContent = names[i];
        floatAgent.classList.remove("swap");
      }
    });
  }

  // --- the dispatcher stage --------------------------------------------------
  const stage = get("stage");
  const stageText = get("stage-text");
  const core = get("core");
  const pulse = get("pulse");
  const wireIn = get<SVGPathElement>("wire-in");
  if (stage && stageText && core && pulse && wireIn) {
    const wires = [...stage.querySelectorAll<SVGPathElement>(".wire:not(.in)")];
    const nodes = [...stage.querySelectorAll<HTMLElement>(".node")];
    const clear = () => {
      [wireIn, ...wires].forEach((w) => w.classList.remove("lit"));
      nodes.forEach((n) => n.classList.remove("on"));
      core.classList.remove("think");
      pulse.style.opacity = "0";
      stageText.textContent = "";
    };
    cleanups.push(clear);

    /** Run the glowing pulse along an SVG path (viewBox 1000 x 620). */
    const travel = (path: SVGPathElement, ms: number) => {
      path.classList.add("lit");
      const length = path.getTotalLength();
      return new Promise<void>((done, stop) => {
        const started = performance.now();
        const step = (now: number) => {
          if (!alive) return stop(new Stopped());
          const k = Math.min((now - started) / ms, 1);
          const eased = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
          const point = path.getPointAtLength(length * eased);
          pulse.style.left = `${point.x / 10}%`;
          pulse.style.top = `${point.y / 6.2}%`;
          pulse.style.opacity = k < 1 ? "1" : "0";
          if (k < 1) requestAnimationFrame(step); else done();
        };
        requestAnimationFrame(step);
      });
    };

    if (still) {
      stageText.textContent = ROUTES[1][0];
      wireIn.classList.add("lit");
      wires[2].classList.add("lit");
      nodes[2].classList.add("on");
    } else {
      const ready = watch(stage);
      sequence(async () => {
        for (let i = 0; ; i = (i + 1) % ROUTES.length) {
          await ready();
          const [message, target] = ROUTES[i];
          await type(stageText, message);
          await wait(300);
          await travel(wireIn, 450);
          core.classList.add("think");
          await wait(1000);
          core.classList.remove("think");
          await travel(wires[target], 800);
          nodes[target].classList.add("on");
          await wait(1900);
          clear();
          await wait(500);
        }
      });
    }
  }

  // --- "hire in a sentence" tile ---------------------------------------------
  const hireCard = get("hire-card");
  const hireTyped = get("hire-typed");
  const hireArt = hireCard?.parentElement;
  if (hireCard && hireTyped && hireArt) {
    const fill = ([, emoji, name, role]: (typeof HIRES)[number]) => {
      get("hire-emoji")!.textContent = emoji;
      get("hire-name")!.textContent = name;
      get("hire-role")!.textContent = role;
    };
    cleanups.push(() => { hireCard.classList.remove("show"); hireArt.classList.remove("go"); hireTyped.textContent = ""; });
    if (still) {
      hireTyped.textContent = HIRES[0][0];
      hireCard.classList.add("show");
    } else {
      const ready = watch(hireArt);
      sequence(async () => {
        for (let i = 0; ; i = (i + 1) % HIRES.length) {
          await ready();
          await type(hireTyped, HIRES[i][0], 34);
          await wait(250);
          hireArt.classList.add("go");
          await wait(550);
          fill(HIRES[i]);
          hireCard.classList.add("show");
          await wait(2600);
          hireCard.classList.remove("show");
          hireArt.classList.remove("go");
          await wait(600);
          hireTyped.textContent = "";
        }
      });
    }
  }

  // --- the Telegram conversation tile ----------------------------------------
  const crewBox = get("demo-crew");
  const log = get("demo-log");
  const chatTyped = get("demo-typed");
  const chatStatus = get("demo-status");
  if (crewBox && log && chatTyped && chatStatus) {
    const chip = ([emoji, name]: [string, string]) => {
      const node = document.createElement("span");
      node.className = "chip";
      node.dataset.name = name;
      node.textContent = `${emoji} ${name}`;
      crewBox.append(node);
      return node;
    };
    const bubble = (kind: string, content: string, label?: string) => {
      const node = document.createElement("div");
      node.className = `bubble ${kind}`;
      if (label) {
        const tag = document.createElement("b");
        tag.textContent = label;
        node.append(tag);
      }
      node.append(content);
      log.append(node);
      log.scrollTo({ top: log.scrollHeight, behavior: still ? "auto" : "smooth" });
      return node;
    };
    const reset = () => {
      crewBox.replaceChildren();
      log.replaceChildren();
      chatTyped.textContent = "";
      chatStatus.textContent = "bot";
    };
    cleanups.push(reset);

    if (still) {  // no motion: show a finished conversation
      reset();
      CREW.forEach(chip);
      for (const step of CHAT.slice(0, 2)) {
        bubble("me", step.say);
        bubble("them", step.reply, `${step.emoji} ${step.who}`);
      }
    } else {
      const ready = watch(log);
      sequence(async () => {
        for (;;) {
          reset();
          CREW.forEach(chip);
          await wait(900);
          for (const step of CHAT) {
            await ready();
            await type(chatTyped, step.say, 26);
            await wait(350);
            chatTyped.textContent = "";
            bubble("me", step.say);

            const chips = [...crewBox.children] as HTMLElement[];
            if (step.hire) {
              chatStatus.textContent = "writing a new agent…";
              await wait(1300);
              chip(step.hire).classList.add("new");
            } else {
              chatStatus.textContent = "choosing an agent…";
              for (let i = 0; i < chips.length * 2; i++) {  // the dispatcher scans the crew
                chips.forEach((c, j) => c.classList.toggle("scan", j === i % chips.length));
                await wait(110);
              }
              chips.forEach((c) => { c.classList.remove("scan"); c.classList.toggle("on", c.dataset.name === step.who); });
              chatStatus.textContent = `${step.who} is typing…`;
            }
            const dots = bubble("them dots", "");
            dots.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
            await wait(1100);
            dots.remove();
            bubble("them", step.reply, step.hire ? undefined : `${step.emoji} ${step.who}`);
            chatStatus.textContent = "bot";
            await wait(1900);
            [...crewBox.children].forEach((c) => c.classList.remove("on"));
          }
          await wait(2200);
        }
      });
    }
  }

  return () => {
    alive = false;
    cleanups.forEach((stop) => stop());
  };
}
