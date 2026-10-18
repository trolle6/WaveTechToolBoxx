#!/usr/bin/env bash
# Cloud Agent install script for WaveTechToolBox.
# Idempotent: safe to run repeatedly and against cached/snapshot state.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Ensuring FFmpeg is installed (required for TTS audio processing)"
if ! command -v ffmpeg >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1; then
        sudo apt-get update -qq
        sudo apt-get install -y --no-install-recommends ffmpeg
    else
        apt-get update -qq
        apt-get install -y --no-install-recommends ffmpeg
    fi
else
    echo "    FFmpeg already present: $(ffmpeg -version | head -1)"
fi

echo "==> Installing Python dependencies from requirements.txt"
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo "==> Creating runtime state files from templates (if missing)"
[ -f cogs/secret_santa_state.json ] || cp cogs/secret_santa_state.json.example cogs/secret_santa_state.json
[ -f cogs/distributed_files_metadata.json ] || cp cogs/distributed_files_metadata.json.example cogs/distributed_files_metadata.json

echo "==> Ensuring required runtime directories exist"
mkdir -p cogs/archive/backups cogs/distributed_files logs

echo "==> Verifying runtime dependencies"
python3 - <<'PY'
import importlib.util, sys
import disnake
assert sys.version_info >= (3, 10), f"Python 3.10+ required, got {sys.version_info[:2]}"
assert tuple(int(x) for x in disnake.__version__.split('.')[:2]) >= (2, 12), \
    f"disnake 2.12+ required for DAVE voice, got {disnake.__version__}"
assert importlib.util.find_spec("dave") is not None, "dave-py missing (disnake[voice])"
import aiohttp, dotenv  # noqa: F401
print(f"OK: Python {sys.version.split()[0]}, disnake {disnake.__version__}, dave-py present")
PY

echo "==> Install complete."
