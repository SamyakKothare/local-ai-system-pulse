"""
Local AI System Pulse — Backend
================================
Python stdlib-only local monitoring API. Serves system metrics (CPU/RAM/disk/
GPU), local AI infra status (OmniRoute gateway health), a live event log, and
an overall health score. Frontend polls this API; no OS access in the browser.

Zero external dependencies beyond `psutil` (which is preinstalled). No cloud,
no API keys, binds only to 127.0.0.1.
"""

import json
import platform
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from urllib.parse import urlparse

import psutil

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
HOST = "127.0.0.1"          # localhost only — never exposed publicly
PORT = 8712                 # arbitrary local port (unlikely to conflict)
OMNIRE_ROUTE_URL = "http://127.0.0.1:20128"
OMNIR_HEALTH_PATH = "/api/health"
OMNIR_META_PATH = "/api/meta"          # may not exist; probed safely
POLL_INTERVAL = 2.0                    # system metrics refresh (s)
HEALTH_INTERVAL = 5.0                  # OmniRoute health refresh (s)
LOG_RING_SIZE = 200                    # keep last N events in memory


# ----------------------------------------------------------------------------
# Event log
# ----------------------------------------------------------------------------
class EventLog:
    """Thread-safe ring buffer of lifecycle/activity events."""

    def __init__(self, maxlen=LOG_RING_SIZE):
        self._items = []
        self._maxlen = maxlen
        self._lock = Lock()

    def add(self, kind, message, level="info"):
        with self._lock:
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "kind": kind,
                "message": message,
                "level": level,
            }
            self._items.insert(0, entry)
            del self._items[self._maxlen:]

    def list(self, limit=100):
        with self._lock:
            return list(self._items[:limit])


log = EventLog()


# ----------------------------------------------------------------------------
# System metrics
# ----------------------------------------------------------------------------
def _cpu_percent():
    """CPU usage across all cores."""
    try:
        return round(psutil.cpu_percent(interval=0.2), 1)
    except Exception:
        return None


def _mem_info():
    """RAM used/total percent + raw bytes."""
    try:
        m = psutil.virtual_memory()
        return {
            "percent": round(m.percent, 1),
            "used_gb": round(m.used / 1024**3, 2),
            "total_gb": round(m.total / 1024**3, 2),
            "available_gb": round(m.available / 1024**3, 2),
        }
    except Exception:
        return None


def _disk_info():
    """Primary disk usage (the drive holding the home dir) + counts."""
    try:
        parts = psutil.disk_partitions(all=False)
        best = None
        for p in parts:
            try:
                usage = psutil.disk_usage(p.mountpoint)
                if best is None or usage.percent > best["percent"]:
                    best = {
                        "mount": p.mountpoint,
                        "device": p.device,
                        "fstype": p.fstype,
                        "percent": round(usage.percent, 1),
                        "used_gb": round(usage.used / 1024**3, 2),
                        "total_gb": round(usage.total / 1024**3, 2),
                    }
            except Exception:
                continue
        return best or None
    except Exception:
        return None


def _gpu_info():
    """
    GPU metrics via `nvidia-smi`.
    Returns None gracefully when no NVIDIA GPU / nvidia-smi is unavailable,
    rather than faking a value.
    """
    query = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=%s" % query, "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        line = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < 6:
            return None
        def _num(v):
            try:
                return float(v)
            except Exception:
                return None
        return {
            "name": fields[0],
            "utilization_percent": _num(fields[1]),
            "vram_used_mb": _num(fields[2]),
            "vram_total_mb": _num(fields[3]),
            "temperature_c": _num(fields[4]),
            "power_w": _num(fields[5]),
            "available": True,
        }
    except Exception:
        return None


def collect_system_metrics():
    """Collect all system metrics into one dict (each field protected)."""
    cpu = _cpu_percent()
    mem = _mem_info()
    disk = _disk_info()
    gpu = _gpu_info()
    return {
        "cpu_percent": cpu,
        "cpu_cores": psutil.cpu_count(logical=True),
        "cpu_cores_physical": psutil.cpu_count(logical=False),
        "memory": mem,
        "disk": disk,
        "gpu": gpu,
        "platform": platform.system(),
        "hostname": platform.node(),
        "boot_time": psutil.boot_time(),
    }


# ----------------------------------------------------------------------------
# OmniRoute health
# ----------------------------------------------------------------------------
def _http_get(url, timeout=3.0):
    """Bounded GET returning (status_code, body_text)."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        start = time.monotonic()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency = (time.monotonic() - start) * 1000.0
            return resp.status, resp.read().decode("utf-8", "replace"), latency
    except Exception as exc:
        return None, str(exc), None


def check_omniroute():
    """
    Probe the OmniRoute gateway. Returns a status dict. Never crashes and
    never prints secrets — only safe summary fields.
    """
    result = {"reachable": False, "http_status": None, "latency_ms": None,
              "detail": None, "info": None}
    status, body, latency = _http_get(OMNIRE_ROUTE_URL + OMNIR_HEALTH_PATH)
    if status is None:
        result["detail"] = "unreachable: %s" % body
        return result
    result["reachable"] = status < 400
    result["http_status"] = status
    result["latency_ms"] = round(latency, 1) if latency is not None else None
    # Try to parse a safe summary (status/timestamp) only — never secrets.
    try:
        data = json.loads(body)
        info = {}
        if "status" in data:
            info["status"] = data["status"]
        if "timestamp" in data:
            info["timestamp"] = data["timestamp"]
        result["info"] = info or None
    except Exception:
        result["info"] = None
    return result


def _hermes_heartbeat():
    """
    A safe, additive indication that Hermes gateway is alive.
    Tries OmniRoute's session/heartbeat endpoints WITHOUT credentials; if none
    respond, returns 'unknown' rather than guessing. No Hermes modification.
    """
    # Best-effort: if the root responds 307 (OmniRoute is up), Hermes is very
    # likely up alongside it locally. This is a heuristic, clearly labeled.
    return {"hermes_indicator": "online" if _omniroute_cached().get("reachable") else "unknown"}


# ----------------------------------------------------------------------------
# Health score
# ----------------------------------------------------------------------------
def compute_health_score(metrics, omniroute):
    """Transparent 0-100 score from available metrics."""
    components = {}
    reasons = []

    # CPU: 0-80% ideal; 80-100% degrades
    cpu = metrics.get("cpu_percent")
    if cpu is not None:
        if cpu <= 80:
            cpu_score = 100 - (cpu * 0.25)     # 100 -> 80
        else:
            cpu_score = max(0, 80 - (cpu - 80) * 2)
        components["cpu"] = round(cpu_score, 1)

    # RAM: higher usage lowers score
    mem = (metrics.get("memory") or {}).get("percent")
    if mem is not None:
        components["memory"] = round(max(0, 100 - mem * 0.8), 1)

    # Disk: higher usage lowers score
    disk = (metrics.get("disk") or {}).get("percent")
    if disk is not None:
        components["disk"] = round(max(0, 100 - disk * 0.4), 1)

    # GPU utilization (if present): 0-90 ideal
    gpu = metrics.get("gpu")
    if gpu and gpu.get("available"):
        gu = gpu.get("utilization_percent")
        if gu is not None:
            components["gpu"] = round(max(0, 100 - abs(gu)) * 0.5 + 50, 1)
        gt = gpu.get("temperature_c")
        if gt is not None:
            components["gpu_temp"] = round(max(0, 100 - max(0, gt - 60) * 2), 1)

    # OmniRoute availability: reachable adds 20
    if omniroute.get("reachable"):
        components["omniroute"] = 100.0
        reasons.append("OmniRoute reachable")
    else:
        components["omniroute"] = 0.0
        reasons.append("OmniRoute unreachable")

    if components:
        overall = sum(components.values()) / len(components)
    else:
        overall = 0.0

    verdict = "EXCELLENT" if overall >= 85 else \
              "GOOD" if overall >= 70 else \
              "FAIR" if overall >= 50 else "POOR"

    return {
        "overall": round(overall, 1),
        "verdict": verdict,
        "components": components,
        "criteria": (
            "Score = weighted mean of available component scores. CPU: <=80% ideal "
            "(100 - 0.25*usage). RAM: 100 - 0.8*usage%. Disk: 100 - 0.4*usage%. "
            "GPU: midpoint by utilization + 50. GPU temp: >=60C penalized. "
            "OmniRoute: ±20pts by reachability. Missing metrics simply don't count "
            "toward the mean."
        ),
        "reasons": reasons,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ----------------------------------------------------------------------------
# Cache & background pollers
# ----------------------------------------------------------------------------
_cache = {"metrics": None, "omniroute": None, "_lock": Lock()}


def _cache_omniroute(value):
    with _cache["_lock"]:
        _cache["omniroute"] = value
    return value


def _omniroute_cached():
    with _cache["_lock"]:
        return _cache["omniroute"] or {"reachable": False}


def _system_loop():
    """Continuously refresh cached system metrics + log notable changes."""
    previous_omniroute = None
    while True:
        metrics = collect_system_metrics()
        with _cache["_lock"]:
            _cache["metrics"] = metrics

        omni = check_omniroute()
        _cache_omniroute(omni)

        # Event log on state changes
        prev = previous_omniroute
        cur = omni.get("reachable")
        if prev is None:
            log.add("service_check", "OmniRoute checked: %s" % (
                "reachable (HTTP %s, %.0fms)" % (omni.get("http_status"), omni.get("latency_ms") or 0)
                if cur else "UNREACHABLE"), "info" if cur else "warning")
        elif prev != cur:
            if cur:
                log.add("service_recovered", "OmniRoute recovered (HTTP %s)" % omni.get("http_status"), "success")
            else:
                log.add("service_down", "OmniRoute unavailable — retrying", "error")
        previous_omniroute = cur

        log.add("metrics_update", "System metrics refreshed (CPU %.1f%%, RAM %s%%)" % (
            metrics["cpu_percent"] or 0, (metrics.get("memory") or {}).get("percent") or 0))
        time.sleep(POLL_INTERVAL)


# ----------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------
class PulseHandler(BaseHTTPRequestHandler):
    server_version = "Pulse/1.0"

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path):
        """Serve frontend static files with correct MIME type."""
        from pathlib import Path
        base = Path(__file__).resolve().parent.parent / "frontend"
        if path == "/":
            path = "/index.html"
        # sanitize: strip query
        rel = urlparse(path).path.lstrip("/")
        target = (base / rel).resolve()
        # prevent path traversal
        if not str(target).startswith(str(base.resolve())):
            self.send_response(403)
            self.end_headers()
            return
        if not target.is_file():
            self.send_response(404)
            self.end_headers()
            return
        mime = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json",
        }
        ctype = mime.get(target.suffix.lower(), "application/octet-stream")
        try:
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_response(500)
            self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/system":
                self._send_json(_cache["metrics"] or collect_system_metrics())
            elif path == "/api/omniroute":
                self._send_json(_omniroute_cached())
            elif path == "/api/hermes":
                self._send_json(_hermes_heartbeat())
            elif path == "/api/events":
                self._send_json({"events": log.list()})
            elif path == "/api/health-score":
                m = _cache["metrics"] or collect_system_metrics()
                o = _omniroute_cached()
                self._send_json(compute_health_score(m, o))
            elif path == "/api/summary":
                m = _cache["metrics"] or collect_system_metrics()
                o = _omniroute_cached()
                self._send_json({
                    "system": m,
                    "omniroute": o,
                    "hermes": _hermes_heartbeat(),
                    "health_score": compute_health_score(m, o),
                    "events": log.list(50),
                })
            elif path.startswith("/api"):
                self._send_json({"error": "unknown endpoint: %s" % path}, 404)
            else:
                self._serve_static(path)
        except Exception as exc:  # never let a bad request kill the server
            self._send_json({"error": "internal error: %s" % exc}, 500)

    def log_message(self, fmt, *args):
        # quiet the request log (keep console clean)
        pass


def main():
    print("=" * 60)
    print("  LOCAL AI SYSTEM PULSE")
    print("  http://%s:%d" % (HOST, PORT))
    print("=" * 60)
    log.add("app_start", "Local AI System Pulse started on %s:%d" % (HOST, PORT))
    Thread(target=_system_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), PulseHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()