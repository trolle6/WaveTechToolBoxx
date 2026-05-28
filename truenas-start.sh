#!/bin/sh
# TrueNAS: git sync + deps in docker-entrypoint.sh; optional TTS dev lab on :8765
set -eu

if [ "${TTS_LAB_ENABLED:-false}" = "true" ]; then
  if [ -f /app/start_tts_lab.py ]; then
    TTS_LAB_HOST="${TTS_LAB_HOST:-0.0.0.0}"
    TTS_LAB_PORT="${TTS_LAB_PORT:-8765}"
    echo "TTS dev lab: starting at http://${TTS_LAB_HOST}:${TTS_LAB_PORT}/ (background)"
    echo "  From your PC use http://<TrueNAS-IP>:${TTS_LAB_PORT}/ — not 127.0.0.1 unless lab runs locally."
    python3 /app/start_tts_lab.py --host "${TTS_LAB_HOST}" --port "${TTS_LAB_PORT}" &
  else
    echo "WARNING: TTS_LAB_ENABLED=true but start_tts_lab.py missing — git pull / merge PR #15" >&2
  fi
fi

exec /app/docker-entrypoint.sh python3 /app/main.py
