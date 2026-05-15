#!/bin/bash
# Dependency installation script
# Installs/updates packages from requirements.txt and verifies Discord voice deps.

set -euo pipefail

echo "📦 Upgrading pip..."
python3 -m pip install --upgrade pip

echo "📦 Installing dependencies from requirements.txt..."
python3 -m pip install --no-cache-dir -r requirements.txt

echo "🔍 Verifying runtime versions..."
python3 <<'PY'
import importlib.util
import sys

import disnake

def parse_ver(v: str) -> tuple:
    parts = []
    for piece in v.split(".")[:3]:
        try:
            parts.append(int(piece))
        except ValueError:
            break
    return tuple(parts) or (0,)

if sys.version_info < (3, 10):
    raise SystemExit(f"Python 3.10+ required, got {sys.version_info.major}.{sys.version_info.minor}")

if parse_ver(disnake.__version__) < (2, 12, 0):
    raise SystemExit(f"disnake {disnake.__version__} is too old; need 2.12+ for Discord voice (DAVE)")

if importlib.util.find_spec("dave") is None:
    raise SystemExit(
        "dave-py missing. Reinstall with: pip install \"disnake[voice]>=2.12.0\""
    )

print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
print(f"✅ disnake {disnake.__version__} (voice/DAVE OK)")
PY

echo "✅ All dependencies installed successfully!"
