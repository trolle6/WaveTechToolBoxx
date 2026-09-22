"""TTS per-guild queue state — isolated for voice cog processor lifecycle work."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class TTSQueueItem:
    """Single item in the TTS playback queue."""
    user_id: int
    channel_id: int
    text: str
    voice: str
    audio_data: Optional[bytes] = None
    timestamp: float = 0.0

    def is_expired(self, max_age: int = 60) -> bool:
        return (time.time() - self.timestamp) > max_age


class GuildVoiceState:
    """Voice processing state for one guild (queue + background processor)."""

    def __init__(self, guild_id: int, logger, max_queue_size: int = 20):
        self.guild_id = guild_id
        self.logger = logger
        self.queue: asyncio.Queue[TTSQueueItem] = asyncio.Queue(maxsize=max_queue_size)
        self.processor_task: Optional[asyncio.Task] = None
        self.is_processing = False
        self.last_activity = time.time()
        self.stats = {"processed": 0, "dropped": 0, "errors": 0}

    def mark_active(self) -> None:
        self.last_activity = time.time()

    def is_idle(self, timeout: int) -> bool:
        return (time.time() - self.last_activity) > timeout

    async def stop(self) -> None:
        if self.processor_task and not self.processor_task.done():
            self.processor_task.cancel()
            try:
                await asyncio.wait_for(self.processor_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            finally:
                self.processor_task = None
