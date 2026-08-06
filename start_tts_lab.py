#!/usr/bin/env python3
"""
Start the local TTS dev lab (http://127.0.0.1:8765/).

Usage (from repo root):
    python start_tts_lab.py
    python start_tts_lab.py --open-browser
    python main.py --tts-lab

Keep this terminal open while using the browser.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent / "dev" / "tts-lab" / "app.py"


def main() -> None:
    if not APP.is_file():
        print(
            "ERROR: dev/tts-lab/app.py not found.\n"
            "  git fetch origin && git checkout cursor/tts-dev-lab-6c6a\n"
            "  (or merge PR #15 into your branch)",
            file=sys.stderr,
        )
        sys.exit(1)
    # Forward CLI flags (e.g. --open-browser) to dev/tts-lab/app.py
    sys.argv = [str(APP)] + sys.argv[1:]
    runpy.run_path(str(APP), run_name="__main__")


if __name__ == "__main__":
    main()
