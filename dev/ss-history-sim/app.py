#!/usr/bin/env python3
"""
Local Secret Santa history simulator — browser UI only, no Discord.

    python dev/ss-history-sim/app.py
    python start_ss_history_sim.py --open-browser
"""
from __future__ import annotations

import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from load_data import load_history, load_year_detail

HERE = Path(__file__).resolve().parent
DEFAULT_PORT = 8770


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[ss-history-sim] {self.address_string()} - {fmt % args}")

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send_file(HERE / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/api/history":
            self._send_json({"years": load_history(include_sim_year=True)})
            return
        if parsed.path == "/api/year":
            qs = parse_qs(parsed.query)
            raw = (qs.get("year") or [""])[0]
            try:
                year = int(raw)
            except ValueError:
                self._send_json({"error": "invalid year"}, status=400)
                return
            detail = load_year_detail(year)
            if detail is None:
                self._send_json({"error": "not found"}, status=404)
                return
            self._send_json(detail)
            return
        self.send_error(404)


def main() -> None:
    parser = argparse.ArgumentParser(description="Secret Santa history simulator (local browser UI)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print("=" * 60)
    print("  Secret Santa history simulator (local only — not the Discord bot)")
    print(f"  Open: {url}")
    print("  Year 3000 is a mock row built from everyone in the archive files.")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)

    if args.open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
