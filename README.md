# Local AI System Pulse

A polished, **100% local**, free & open-source health/monitoring dashboard for your laptop and your local AI infrastructure. Think of it as a small, self-contained JARVIS-style "system pulse" — it shows live CPU / RAM / disk / GPU metrics, the status of your local **OmniRoute** AI gateway, a live activity event log, and a transparent overall **health score**.

![local-only] 100% local · ![foss] FOSS · ![no-cloud] no cloud · ![no-api] no paid APIs

---

## Features

- **System monitoring** — live CPU, RAM, disk, and (if present) GPU usage / temperature / VRAM / power via `nvidia-smi`.
- **AI infrastructure status** — probes your local OmniRoute gateway at `http://127.0.0.1:20128` and shows reachability, HTTP health status, and response latency. (Safely — no API keys, no secrets.)
- **Hermes indicator** — a labeled, conservative "is the local agent gateway reachable" indication inferred from OmniRoute reachability (never modifies Hermes).
- **Live event log** — a small in-memory stream of lifecycle events (app started, OmniRoute checked, metrics refreshed, service down/recovered).
- **Health score** — a 0–100 overall score computed from the available metrics. The criteria are transparent in the UI (see *Health score criteria* below).
- **Futuristic UI** — dark, JARVIS-inspired, responsive, with lightweight status indicators and very little animation (deliberately no resource-heavy effects).

---

## Architecture

```
Browser (frontend/ static files)
        │  HTTP (127.0.0.1:8712)
        ▼
Python backend (backend/server.py)  ─── stdlib http.server ─── presents JSON API only
        │
        ├── psutil        → CPU / RAM / disk
        ├── nvidia-smi    → GPU (subprocess, graceful if absent)
        └── urllib        → OmniRoute HTTP health probe (127.0.0.1:20128)
```

- **Backend** (`backend/server.py`): a Python standard-library HTTP server (`http.server` + `ThreadingHTTPServer`). It exposes a small JSON API and does **all** OS/GPU/gateway access server-side. The browser never touches the OS directly.
- **Frontend** (`frontend/`): static HTML/CSS/JS served by the same backend. It polls the JSON API on a 2-second interval and renders the HUD. Pure browser JS, no build step, no framework, no dependencies.
- **Pollers**: a background thread refreshes cached system metrics every 2s and probes OmniRoute every 5s. Endpoints read the cached values, so the API is fast and consistent.
- **Bind**: the server binds to `127.0.0.1` only — it is never exposed to the public internet.

### Why stdlib + psutil?

Only `psutil` (already installed on this machine) is required. Everything else — HTTP server, JSON, threading, subprocess, urllib — comes from the Python standard library. This keeps the project tiny, has essentially no install surface, and needs no cloud or paid service.

---

## Requirements

- **OS:** Windows (developed/tested on Windows 11; should work on macOS/Linux with minor tweaks).
- **Python 3.9+** (developed on 3.11).
- **`psutil`** for system metrics — install via `pip install -r requirements.txt`.
- **Optional:** an NVIDIA GPU + `nvidia-smi` in PATH to show GPU metrics. If absent, the GPU panel shows "unavailable" gracefully rather than faking a value.
- **Optional (for AI panel):** a local OmniRoute gateway at `http://127.0.0.1:20128`. If not running, it shows OFFLINE — the dashboard still works.

---

## Installation

```bash
cd "D:/HERMES files/Projects/LocalAISystemPulse"
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

(If `psutil` is already installed globally you can skip the venv and install.)

---

## How to run it

```bash
python backend/server.py
```

Then open your browser to:

```
http://127.0.0.1:8712
```

You should see the HUD immediately; metrics refresh every ~2 seconds.

---

## Available endpoints

All return JSON. The browser hits these over `127.0.0.1:8712`.

| Endpoint | Description |
|---|---|
| `GET /` | The dashboard UI (index.html) |
| `GET /api/system` | CPU, memory, disk, GPU, platform, hostname |
| `GET /api/omniroute` | OmniRoute reachability, HTTP status, latency, safe info |
| `GET /api/hermes` | Hermes reachability indicator (conservative heuristic) |
| `GET /api/events` | Recent activity events |
| `GET /api/health-score` | Overall 0–100 health score + component breakdown + criteria |
| `GET /api/summary` | Combined system + omniroute + hermes + score + events in one call |

---

## How system metrics are collected

- **CPU:** `psutil.cpu_percent(interval=0.2)` averaged across all cores; core counts via `psutil.cpu_count()`.
- **RAM:** `psutil.virtual_memory()` → percent, used/total/available in GB.
- **Disk:** `psutil.disk_usage()` on the first/secondary partitions; shows the most-utilized local volume.
- **GPU:** `nvidia-smi --query-gpu=...,--format=csv` subprocess → name, utilization %, VRAM used/total (MB), temperature (°C), power draw (W). Parsed defensively.
- **Network/connectivity:** represented by the OmniRoute gateway reachability probe (no raw bandwidth metering, which is flaky cross-platform; see *Limitations*).
- Every metric collector is wrapped in try/except so any single failure (e.g. no GPU) degrades to `null`/"unavailable" rather than crashing or returning a fake number.

---

## OmniRoute integration

The backend probes `http://127.0.0.1:20128/api/health` with a short timeout:

- **Reachable** → shows ONLINE, HTTP status, latency (ms), and any safe `status`/`timestamp` fields.
- **Unreachable** → shows OFFLINE (the probe never reads or logs secret material; only summary fields).

The **Hermes panel** is a conservative local indication: when OmniRoute is reachable, Hermes is inferred online (they run together locally); otherwise it shows **UNKNOWN**, never a false "online".

---

## Health score criteria

The score is the **mean of the component scores** that are actually available (missing metrics simply don't count):

| Component | Rule |
|---|---|
| **CPU** | ≤80% → `100 − 0.25·usage` (so 0% = 100, 80% = 80); >80% → `max(0, 80 − (usage−80)·2)` |
| **RAM** | `max(0, 100 − usage%·0.8)` |
| **Disk** | `max(0, 100 − usage%·0.4)` |
| **GPU util** | `50 + 0.5·(100 − utilization)` |
| **GPU temp** | `max(0, 100 − max(0, temp−60)·2)` |
| **OmniRoute** | ±20 pts via reachability (100 when reachable, 0 when not) |

**Verdict:** ≥85 = EXCELLENT, ≥70 = GOOD, ≥50 = FAIR, else POOR. The exact formula string is also shown in the dashboard UI (`criteria`).

---

## Known limitations

- **GPU metrics** require an NVIDIA GPU + `nvidia-smi`; AMD/Intel iGPUs are not detected (shown as N/A, never faked).
- **No per-interface raw network bandwidth metering** (cross-platform inconsistency). Network state is reflected through the OmniRoute reachability probe.
- **Hermes status** is an *inference* (OmniRoute reachable ⇒ Hermes likely live), not a direct IPC check — read-only, no Hermes modification.
- **In-memory event log** — events are lost on restart (by design; no persistence for a monitoring panel). Ring buffer capped at 200.
- **Single-user local tool** — no authentication (only bound to loopback, so that's acceptable locally).
- **OmniRoute info** is intentionally limited to safe summary fields to avoid exposing secrets.

---

## Future improvements

- Persist event history to a small SQLite file so the log survives restarts.
- Add historical sparkline charts for CPU/RAM/GPU over time (local, Canvas).
- Add per-interface bandwidth metering where the OS exposes it cleanly.
- Optional local LLM integration to summarize health anomalies (keep it local/Ollama, no cloud).
- WebSocket push instead of 2s polling (lower overhead, smoother updates).

---

## GitHub Pages deployment (static demo)

> **Architectural note:** This project is designed as a **local** tool. The live
> HUD depends on a Python backend (`backend/server.py`) that reads real system/GPU
> metrics via `psutil`/`nvidia-smi` and probes the local OmniRoute gateway. GitHub
> Pages can only serve **static** files and cannot run a Python process, so the
> *live* dashboard cannot be hosted on Pages.

To make the repo presentable on GitHub Pages without faking live data, a
**static demo** page is published to the `gh-pages` branch. It ships the same
frontend styling (`style.css`) and renders the HUD with **placeholder** values and
a clear banner stating that live metrics require the local backend. No telemetry,
no fake system readings — it is explicitly a UI demo.

Run the real thing locally:

```bash
python backend/server.py
# open http://127.0.0.1:8712
```

---

## License

MIT — free to use, modify, and redistribute. No cloud, no tracking, no telemetry.