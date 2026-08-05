"""Shared test harness — fake bot/config for isolated cog tests."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

# Minimal valid-looking env for cog __init__ paths
TEST_ENV = {
    "DISCORD_TOKEN": "test_discord_token_" + "x" * 40,
    "DISCORD_CHANNEL_ID": "627896843149508609",
    "DISCORD_LOG_CHANNEL_ID": "627896843149508610",
    "DISCORD_MODERATOR_ROLE_ID": "123456789012345678",
    "OPENAI_API_KEY": "sk-test" + "x" * 40,
    "DEBUG_MODE": "false",
    "LOG_LEVEL": "WARNING",
    "SKIP_API_VALIDATION": "true",
}


class FakeHttpManager:
  async def get_session(self, timeout=None):
    return AsyncMock()

  async def invalidate_session(self):
    pass


def apply_test_env() -> dict[str, Optional[str]]:
    """Set test env vars; return previous values for restore."""
    previous: dict[str, Optional[str]] = {}
    for key, value in TEST_ENV.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    return previous


def restore_env(previous: dict[str, Optional[str]]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Python 3.12+ needs an explicit loop before importing main (creates InteractionBot)."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass
    try:
        loop = asyncio.get_event_loop()
        if not loop.is_closed():
            return loop
    except RuntimeError:
        pass
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def make_fake_bot() -> Any:
    ensure_event_loop()
    from main import Config

    logger = logging.getLogger("bot.test")
    logger.setLevel(logging.WARNING)
    config = Config()
    bot = SimpleNamespace(
        config=config,
        logger=logger,
        executor=ThreadPoolExecutor(max_workers=2, thread_name_prefix="test"),
        http_mgr=FakeHttpManager(),
        cogs={},
        extensions={},
        is_closed=lambda: False,
        get_cog=lambda name: None,
    )
    return bot
