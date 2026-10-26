#!/usr/bin/env python3
"""Launch the local Secret Santa history simulator (browser UI, not Discord)."""
import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    app = Path(__file__).resolve().parent / "dev" / "ss-history-sim" / "app.py"
    sys.argv[0] = str(app)
    runpy.run_path(str(app), run_name="__main__")
