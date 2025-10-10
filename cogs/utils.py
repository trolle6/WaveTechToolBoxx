import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp

logger = logging.getLogger("bot")


class RateLimiter:
    """Token bucket rate limiter"""
    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self.tokens = {}

    def check(self, key: str) -> bool:
        now = time.time()
        if key not in self.tokens:
            self.tokens[key] = []

        # Remove old tokens
        self.tokens[key] = [t for t in self.tokens[key] if now - t < self.window]

        if len(self.tokens[key]) < self.limit:
            self.tokens[key].append(now)
            return True
        return False

    def reset(self, key: str):
        self.tokens.pop(key, None)


class CircuitBreaker:
    """Prevent cascading failures"""
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure = None
        self.state = "closed"  # closed, open, half-open

    def record_success(self):
        self.failures = 0
        self.state = "closed"

    def record_failure(self):
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.failure_threshold:
            self.state = "open"
            logger.warning(f"Circuit breaker opened after {self.failures} failures")

    def can_attempt(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure > self.recovery_timeout:
                self.state = "half-open"
                return True
            return False
        return True  # half-open


class HttpManager:
    """Reusable HTTP session with connection pooling"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.session = None
        return cls._instance

    async def get_session(self, timeout: int = 30) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                connector=connector,
            )
            logger.info("HTTP session created")
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("HTTP session closed")


class JsonFile:
    """Thread-safe JSON file operations"""
    def __init__(self, path: str):
        self.path = Path(path)
        self.lock = asyncio.Lock()

    async def load(self, default: Any = None) -> Any:
        async with self.lock:
            if self.path.exists():
                try:
                    return json.loads(self.path.read_text())
                except (json.JSONDecodeError, OSError) as e:
                    logger.error(f"JSON load error: {e}")
            return default or {}

    async def save(self, data: Any):
        async with self.lock:
            try:
                self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            except OSError as e:
                logger.error(f"JSON save error: {e}")


class RequestCache:
    """Deduplication cache for expensive operations"""
    def __init__(self, ttl: int = 3600):
        self.cache = {}
        self.ttl = ttl

    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            val, expires = self.cache[key]
            if time.time() < expires:
                return val
            del self.cache[key]
        return None

    def set(self, key: str, value: Any):
        self.cache[key] = (value, time.time() + self.ttl)

    def cleanup(self):
        """Remove expired entries"""
        now = time.time()
        self.cache = {k: v for k, v in self.cache.items() if v[1] > now}