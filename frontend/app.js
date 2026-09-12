/* Local AI System Pulse — frontend controller
   Polls the local backend API every 2s and updates the HUD. No OS access
   in the browser; everything arrives over the local JSON API. */

const $ = (id) => document.getElementById(id);
const fmt2 = (n) => (n == null ? "--" : n.toFixed ? n.toFixed(1) : n);

const API = {
  system: "/api/system",
  omni: "/api/omniroute",
  hermes: "/api/hermes",
  events: "/api/events",
  score: "/api/health-score",
  summary: "/api/summary",
};

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

function setBar(el, pct) {
  const v = Math.max(0, Math.min(100, pct || 0));
  el.style.width = v + "%";
  // color by threshold
  if (v > 85) el.style.background = "linear-gradient(90deg,#ff7a00,#ff4d5e)";
  else if (v > 70) el.style.background = "linear-gradient(90deg,#ffc233,#ff7a00)";
  else el.style.background = "linear-gradient(90deg,#00e5ff,#00ff9d)";
}

function renderSystem(s) {
  if (!s) return;
  // CPU
  const cpu = s.cpu_percent;
  $("cpu-val").textContent = (cpu == null ? "--" : cpu.toFixed(1)) + "%";
  setBar($("cpu-bar"), cpu);
  $("cpu-cores").textContent =
    (s.cpu_cores_physical || "?") + " physical / " + (s.cpu_cores || "?") + " logical cores";

  // RAM
  const m = s.memory;
  if (m) {
    $("mem-val").textContent = m.percent.toFixed(1) + "%";
    setBar($("mem-bar"), m.percent);
    $("mem-detail").textContent = (m.used_gb||0).toFixed(1) + " / " + (m.total_gb||0).toFixed(1) + " GB";
  } else { $("mem-val").textContent = "--%"; $("mem-detail").textContent = "unavailable"; }

  // Disk
  const d = s.disk;
  if (d) {
    $("disk-val").textContent = d.percent.toFixed(1) + "%";
    setBar($("disk-bar"), d.percent);
    $("disk-detail").textContent = (d.used_gb||0).toFixed(1) + " / " + (d.total_gb||0).toFixed(1) + " GB (" + (d.mount||"") + ")";
  } else { $("disk-val").textContent = "--%"; $("disk-detail").textContent = "unavailable"; }

  // GPU
  const g = s.gpu;
  if (g && g.available) {
    const util = g.utilization_percent;
    $("gpu-val").textContent = (util == null ? "--" : util.toFixed(0)) + "%";
    setBar($("gpu-bar"), util);
    $("gpu-detail").textContent = g.name || "GPU";
    const vram = (g.vram_used_mb != null && g.vram_total_mb != null)
      ? (g.vram_used_mb/1024).toFixed(1) + " / " + (g.vram_total_mb/1024).toFixed(1) + " GB VRAM"
      : "VRAM n/a";
    const temp = g.temperature_c != null ? g.temperature_c.toFixed(0) + "°C" : "temp n/a";
    const power = g.power_w != null ? " · " + g.power_w.toFixed(1) + "W" : "";
    $("gpu-temp").textContent = temp + " | " + vram + power;
  } else {
    $("gpu-val").textContent = "N/A";
    $("gpu-bar").style.width = "0%";
    $("gpu-detail").textContent = "No NVIDIA GPU detected";
    $("gpu-temp").textContent = "Gracefully unavailable";
  }
}

function renderOmni(o) {
  const dot = $("omni-dot");
  const status = $("omni-status");
  const meta = $("omni-meta");
  if (o && o.reachable) {
    dot.className = "status-dot online";
    status.textContent = "ONLINE";
    status.className = "infra-status big online";
    $("omni-detail").textContent =
      "HTTP " + (o.http_status ?? "?") + " · " + (o.latency_ms != null ? o.latency_ms.toFixed(0) : "?") + " ms";
    meta.textContent = o.info && o.info.status ? ("Gateway status: " + o.info.status) : "Reachable · healthy";
  } else {
    dot.className = "status-dot offline";
    status.textContent = "OFFLINE";
    status.className = "infra-status big offline";
    $("omni-detail").textContent = "http://127.0.0.1:20128";
    meta.textContent = o && o.detail ? o.detail : "Gateway unreachable";
  }
}

function renderHermes(h) {
  const dot = $("hermes-dot");
  const status = $("hermes-status");
  const meta = $("hermes-meta");
  if (h && h.hermes_indicator === "online") {
    dot.className = "status-dot online";
    status.textContent = "ONLINE";
    status.className = "infra-status big online";
    meta.textContent = "Local gateway reachable (inferred from OmniRoute)";
  } else {
    dot.className = "status-dot unknown";
    status.textContent = "UNKNOWN";
    status.className = "infra-status big unknown";
    meta.textContent = "Indicated by local OmniRoute reachability";
  }
}

function renderScore(sc) {
  if (!sc) return;
  const overall = sc.overall;
  $("score-num").textContent = Math.round(overall);
  const arc = $("score-arc");
  const R = 52, C = 2 * Math.PI * R;
  arc.style.strokeDasharray = C;
  arc.style.strokeDashoffset = C * (1 - (overall / 100));
  let color = "#ff4d5e";
  if (overall >= 85) color = "#00ff9d";
  else if (overall >= 70) color = "#00e5ff";
  else if (overall >= 50) color = "#ffc233";
  arc.style.stroke = color;
  $("score-num").style.color = color;
  $("verdict").textContent = sc.verdict;
  $("verdict").style.color = color;
  $("criteria").textContent = sc.criteria;
}

function renderEvents(events) {
  const body = $("log-body");
  if (!events || !events.length) {
    body.innerHTML = '<div class="log-empty">Waiting for events…</div>';
    return;
  }
  let html = "";
  const levelIcon = { info: "●", success: "✔", warning: "▲", error: "✖" };
  for (const e of events.slice(0, 50)) {
    html += '<div class="log-row ' + (e.level||"info") + '">'
      + '<span class="log-time">' + e.ts + '</span>'
      + '<span class="log-kind">' + (levelIcon[e.level] || "●") + '</span>'
      + '<span class="log-msg"></span></div>';
  }
  body.innerHTML = html;
  // fill message text after building rows (safe innerText)
  const rows = body.querySelectorAll(".log-row");
  events.slice(0, 50).forEach((e, i) => {
    if (rows[i]) rows[i].querySelector(".log-msg").textContent = e.message;
  });
  body.scrollTop = 0;
}

function clock() {
  const now = new Date();
  const p = (n) => String(n).padStart(2, "0");
  $("clock").textContent = p(now.getHours()) + ":" + p(now.getMinutes()) + ":" + p(now.getSeconds());
}

async function pollAll() {
  // Use single summary endpoint for most data, then independent ones as needed.
  try {
    const s = await getJSON(API.summary);
    renderSystem(s.system);
    renderOmni(s.omniroute);
    renderHermes(s.hermes);
    renderScore(s.health_score);
    renderEvents(s.events);
    $("glob-status").innerHTML = '<span class="dot" style="background:#00ff9d;box-shadow:0 0 8px #00ff9d"></span><span>ONLINE</span>';
    $("footer-host").textContent = s.system ? (s.system.hostname || "--") : "--";
  } catch (err) {
    $("glob-status").innerHTML = '<span class="dot" style="background:#ff4d5e;box-shadow:0 0 8px #ff4d5e"></span><span>API ERROR</span>';
    console.error("Poll error:", err);
  }
}

// start
clock();
setInterval(clock, 1000);
pollAll();
setInterval(pollAll, 2000);

/* ============================================================
   VISUAL EFFECTS (additive, frontend-only)
   - Animated beams background (canvas)
   - Border-trail stagger across cards
   Lightweight: reduced beams, pauses when hidden, respects
   prefers-reduced-motion. Does not touch any monitoring logic.
   ============================================================ */

(function () {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* ---------- Border-trail stagger ---------- */
  // Assign each card a subtly different start angle via CSS var.
  const trailCards = document.querySelectorAll(".card");
  trailCards.forEach((c, i) => {
    c.style.setProperty("--trail-delay", ((i * 0.62) % 1).toFixed(2) + "s");
  });

  /* ---------- Animated beams background ---------- */
  const canvas = document.getElementById("beams-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let W = 0, H = 0, dpr = 1, raf = 0, running = false;

  // Beam definition
  const BEAMS = 6;
  const PALETTE = [
    [0, 229, 255],  // cyan
    [90, 120, 255], // blue
    [150, 80, 255], // purple
  ];

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 1.5); // cap DPR for perf
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function draw(t) {
    ctx.clearRect(0, 0, W, H);
    const w = W, h = H;
    for (let i = 0; i < BEAMS; i++) {
      const col = PALETTE[i % PALETTE.length];
      // slowly drifting vertical beams
      const xBase = (i + 0.5) * (w / BEAMS);
      const sway = Math.sin(t * 0.0002 + i * 1.3) * w * 0.06;
      const x = xBase + sway;
      const bw = Math.min(w * 0.16, 220);  // beam width, bounded for perf
      const rise = ((t * 0.00003) + i * 0.33) % 1;
      const yTop = -h * 0.2 + rise * (h * 1.4);

      // vertical gradient beam
      const grad = ctx.createLinearGradient(0, yTop - h * 0.5, 0, yTop + h * 0.5);
      grad.addColorStop(0, "rgba(" + col.join(",") + ",0)");
      grad.addColorStop(0.5, "rgba(" + col.join(",") + ",0.10)");
      grad.addColorStop(1, "rgba(" + col.join(",") + ",0)");

      ctx.save();
      ctx.translate(x, yTop);
      ctx.globalAlpha = 0.35;
      ctx.fillStyle = grad;
      ctx.scale(1, 1.6);
      ctx.fillRect(-bw / 2, -h * 0.5, bw, h);
      ctx.restore();
    }

    // soft top ambient glow
    const glow = ctx.createLinearGradient(0, 0, 0, h * 0.5);
    glow.addColorStop(0, "rgba(0,229,255,0.04)");
    glow.addColorStop(1, "rgba(0,229,255,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h * 0.5);
  }

  function loop(t) {
    if (!running) return;
    draw(t);
    raf = requestAnimationFrame(loop);
  }

  function start() {
    if (reduced.matches) return;           // respect reduced motion
    if (running) return;
    running = true;
    raf = requestAnimationFrame(loop);
  }

  function stop() {
    running = false;
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
  }

  function onVisibility() {
    if (document.hidden) stop();
    else start();
  }

  function onLayout() {
    resize();
  }

  resize();
  window.addEventListener("resize", onLayout);
  document.addEventListener("visibilitychange", onVisibility);

  reduced.addEventListener("change", (e) => { if (e.matches) stop(); else start(); });

  start();
})();