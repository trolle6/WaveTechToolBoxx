#!/usr/bin/env python3
"""
Local TTS dev lab — http://127.0.0.1:8765/

Serves static UI and proxies OpenAI TTS (API key stays server-side).
Dev only: binds loopback.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import sys
import webbrowser
from pathlib import Path
from threading import Timer

from aiohttp import web
from dotenv import load_dotenv

LAB_DIR = Path(__file__).resolve().parent
ROOT = LAB_DIR.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / "config.env", override=True)

sys.path.insert(0, str(LAB_DIR))
import log_buffer  # noqa: E402
import tts_client  # noqa: E402

RingBufferHandler = log_buffer.RingBufferHandler

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
HOST = DEFAULT_HOST
PORT = DEFAULT_PORT

log_handler = RingBufferHandler(capacity=500)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
root_logger = logging.getLogger()
root_logger.addHandler(log_handler)
logger = logging.getLogger("tts-lab.app")


def _json_response(data: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        status=status,
        content_type="application/json",
    )


async def handle_index(_request: web.Request) -> web.Response:
    html_path = LAB_DIR / "index.html"
    return web.FileResponse(html_path)


async def handle_voices(_request: web.Request) -> web.Response:
    return _json_response({"voices": tts_client.AVAILABLE_VOICES, "default": tts_client.DEFAULT_VOICE})


async def handle_logs(request: web.Request) -> web.Response:
    try:
        limit = int(request.query.get("limit", "200"))
    except ValueError:
        limit = 200
    return _json_response({"lines": log_handler.snapshot(limit)})


async def handle_logs_stream(request: web.Request) -> web.StreamResponse:
    """SSE: push new log lines every second."""
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await response.prepare(request)
    seen = len(log_handler.snapshot(0))
    try:
        while True:
            lines = log_handler.snapshot(500)
            if len(lines) > seen:
                for line in lines[seen:]:
                    payload = json.dumps({"line": line})
                    await response.write(f"data: {payload}\n\n".encode())
                seen = len(lines)
            await asyncio.sleep(1)
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    return response


async def handle_health(_request: web.Request) -> web.Response:
    import os

    has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
    return _json_response({"ok": True, "openai_configured": has_key, "port": PORT})


async def handle_tts(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json_response({"error": "invalid JSON"}, status=400)

    text = body.get("text", "")
    voice = body.get("voice")
    audio, meta = await tts_client.generate_tts(text, voice)
    if audio is None:
        logger.error("TTS failed: %s", meta.get("error", "unknown"))
        return _json_response(meta, status=502)

    headers = {
        "X-TTS-Cache-Hit": "1" if meta.get("cache_hit") else "0",
        "X-TTS-Api-Ms": str(meta.get("api_ms", 0)),
        "X-TTS-Bytes": str(meta.get("bytes", len(audio))),
        "X-TTS-Voice": meta.get("voice", tts_client.DEFAULT_VOICE),
    }
    logger.info(
        "TTS ok: bytes=%s cache=%s api_ms=%s",
        meta.get("bytes"),
        meta.get("cache_hit"),
        meta.get("api_ms"),
    )
    return web.Response(body=audio, content_type="audio/mpeg", headers=headers)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/index.html", handle_index)
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/voices", handle_voices)
    app.router.add_get("/api/logs", handle_logs)
    app.router.add_get("/api/logs/stream", handle_logs_stream)
    app.router.add_post("/api/tts", handle_tts)
    return app


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local TTS dev lab — keep this process running while using the browser.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("TTS_LAB_HOST", DEFAULT_HOST),
        help=f"Bind address (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("TTS_LAB_PORT", str(DEFAULT_PORT))),
        help=f"Port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open http://host:port/ once the server is up",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    global HOST, PORT
    args = _parse_args(argv)
    HOST = args.host
    PORT = args.port
    url = f"http://{HOST}:{PORT}/"

    if not _port_is_free(HOST, PORT):
        print(
            f"\nERROR: Port {PORT} on {HOST} is already in use.\n"
            f"  • Another TTS lab may already be running — try {url}\n"
            f"  • Or pick another port: python start_tts_lab.py --port 8766\n",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.getenv("OPENAI_API_KEY", "").strip():
        print(
            "WARNING: OPENAI_API_KEY not set in config.env — UI loads but TTS will fail.\n",
            file=sys.stderr,
        )

    banner = (
        f"\n{'=' * 60}\n"
        f"  TTS Dev Lab running\n"
        f"  URL: {url}\n"
        f"  Leave this terminal OPEN. Ctrl+C to stop.\n"
        f"  ERR_CONNECTION_REFUSED in the browser means this process is not running.\n"
        f"{'=' * 60}\n"
    )
    print(banner, flush=True)
    logger.info("Starting TTS lab at %s (dev only)", url)

    if args.open_browser:
        Timer(1.2, lambda: webbrowser.open(url)).start()

    web.run_app(create_app(), host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
