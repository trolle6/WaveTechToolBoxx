#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Starting TTS Dev Lab — keep this terminal open."
exec python3 start_tts_lab.py --open-browser "$@"
