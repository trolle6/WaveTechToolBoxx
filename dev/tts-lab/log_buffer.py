"""In-memory log ring buffer for the TTS dev lab UI."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Deque, List


class RingBufferHandler(logging.Handler):
    """Capture log records for /api/logs (thread-safe)."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self._capacity = capacity
        self._lines: Deque[str] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        with self._lock:
            self._lines.append(msg)

    def snapshot(self, limit: int = 200) -> List[str]:
        with self._lock:
            if limit <= 0:
                return []
            if limit >= len(self._lines):
                return list(self._lines)
            return list(self._lines)[-limit:]
