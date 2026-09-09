from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from twinloop.dashboard.live import provider_status, run_stream

PAGE = ROOT / "review" / "index.html"

BOUNDS = {
    "seed": (0, 9999),
    "ticks": (20, 300),
    "interval": (5, 60),
    "horizon": (5, 60),
    "retry_cap": (0, 5),
    "harm_threshold": (0, 20),
    "speed": (1, 200),
}


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _clamp(name, value, fallback):
    lo, hi = BOUNDS[name]
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, v))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "twinloop-live"

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/run"):
            return
        sys.stderr.write("  %s %s\n" % (self.command, self.path))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _page(self):
        if not PAGE.exists():
            self._json({"error": "review/index.html not found. Build it first."}, 404)
            return
        body = PAGE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path)
        if route.path in ("/", "/index.html", "/review", "/review/"):
            self._page()
        elif route.path == "/api/health":
            self._json(
                {
                    "ok": True,
                    "service": "twinloop-live",
                    "version": 1,
                    "providers": provider_status(ROOT),
                    "defaults": {
                        "agent": "llm",
                        "provider": "scripted",
                        "gate": True,
                        "fidelity": 1.0,
                        "seed": 15,
                        "ticks": 120,
                        "interval": 10,
                        "horizon": 20,
                        "speed": 25,
                    },
                    "bounds": {k: list(v) for k, v in BOUNDS.items()},
                }
            )
        elif route.path == "/api/run":
            self._run(parse_qs(route.query))
        else:
            self._json({"error": "not found"}, 404)

    def _run(self, q):
        def one(name, default=None):
            v = q.get(name)
            return v[0] if v else default

        agent = one("agent", "llm")
        if agent not in ("null", "rule", "llm"):
            agent = "llm"
        provider = one("provider", "scripted")
        if provider not in ("scripted", "cached", "gemini", "local"):
            provider = "scripted"
        gate = one("gate", "1") not in ("0", "false", "False")
        try:
            fidelity = max(0.0, min(1.0, float(one("fidelity", "1.0"))))
        except ValueError:
            fidelity = 1.0
        speed = _clamp("speed", one("speed", "25"), 25)
        opts = dict(
            agent_kind=agent,
            provider_name=provider,
            gate=gate,
            fidelity=fidelity,
            seed=_clamp("seed", one("seed", "15"), 15),
            ticks=_clamp("ticks", one("ticks", "120"), 120),
            interval=_clamp("interval", one("interval", "10"), 10),
            horizon=_clamp("horizon", one("horizon", "20"), 20),
            retry_cap=_clamp("retry_cap", one("retry_cap", "2"), 2),
            harm_threshold=_clamp("harm_threshold", one("harm_threshold", "3"), 3),
            counterfactual=one("counterfactual", "1") not in ("0", "false", "False"),
        )

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        self.close_connection = True

        label = f"{agent}" + (f"/{provider}" if agent == "llm" else "") + (f" +twin@{fidelity:.2f}" if gate else "")
        sys.stderr.write(f"  run: {label} seed={opts['seed']} ticks={opts['ticks']} speed={speed}/s\n")

        delay = 1.0 / speed
        sent = 0
        started = time.perf_counter()
        try:
            self._emit({"type": "hello", "options": {**opts, "speed": speed}})
            for event in run_stream(**opts):
                self._emit(event)
                sent += 1
                if event.get("type") == "tick":
                    time.sleep(delay)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            sys.stderr.write(f"  client disconnected after {sent} events\n")
            return
        except Exception:
            traceback.print_exc()
            try:
                self._emit({"type": "error", "message": "the run crashed on the server; see the server console"})
            except Exception:
                pass
            return
        sys.stderr.write(f"  done: {sent} events in {time.perf_counter() - started:.1f}s\n")

    def _emit(self, event):
        self.wfile.write(b"data: " + json.dumps(event).encode("utf-8") + b"\n\n")
        self.wfile.flush()


def main():
    parser = argparse.ArgumentParser(description="Live episode server for the Twin-in-the-Loop review page.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    _load_dotenv()
    status = provider_status(ROOT)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"

    print("Twin-in-the-Loop — live server")
    print(f"  open {url}")
    print(f"  page {'found' if PAGE.exists() else 'MISSING — build review/index.html first'}")
    print("  brains:")
    for key, info in status.items():
        print(f"    {'ok ' if info['available'] else '-- '} {key:9s} {info['label']}")
    print("  ctrl-c to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        server.server_close()


if __name__ == "__main__":
    main()
