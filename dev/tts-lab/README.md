# TTS Dev Lab

Local web UI to test OpenAI TTS without Discord. Binds **127.0.0.1:8765** only.

## Prerequisites

- Python 3.10+ with project dependencies: `pip install -r requirements.txt`
- `OPENAI_API_KEY` in repo root `config.env` (same as the bot)

## Start

From the repository root:

```bash
python dev/tts-lab/app.py
```

Then open: **http://127.0.0.1:8765/**

## If the browser shows ERR_CONNECTION_REFUSED

Nothing is listening on port 8765. Confirm:

```bash
ss -tlnp | grep 8765
curl -v --connect-timeout 2 http://127.0.0.1:8765/
```

Start `app.py` on the **same machine** as the browser. Remote VMs need SSH port forwarding.

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | UI |
| `/api/health` | GET | `openai_configured` flag |
| `/api/voices` | GET | Voice list |
| `/api/tts` | POST | JSON `{"text","voice"}` → `audio/mpeg` |
| `/api/logs` | GET | Last log lines JSON |
| `/api/logs/stream` | GET | SSE log stream |

## Security

- Dev tool only; do not expose port 8765 to the internet.
- Do not put the API key in `index.html` or publish this UI to GitHub Pages without a backend.
