"""
Bot Utilities - Shared Components for All Cogs

COMPONENTS:
- RateLimiter: Token bucket rate limiter (O(1) operations with deque)
- CircuitBreaker: Prevents cascading failures with circuit breaker pattern
- LRUCache: Generic LRU cache with TTL support
- JsonFile: Thread-safe JSON file operations
- RequestCache: Simple deduplication cache

OPTIMIZATIONS:
- ✅ RateLimiter uses deque for O(1) operations (vs O(n) list filtering)
- ✅ All classes use lazy cleanup (only clean on access)
- ✅ Minimal async overhead (sync where possible, async wrappers for API compatibility)

USAGE:
    from . import utils
    
    # Rate limiting
    limiter = utils.RateLimiter(limit=10, window=60)
    if await limiter.check(user_id):
        # Allowed
    
    # Circuit breaker
    breaker = utils.CircuitBreaker(failure_threshold=5)
    if await breaker.can_attempt():
        # Try operation
        await breaker.record_success()  # or record_failure()
    
    # LRU Cache
    cache = utils.LRUCache[bytes](max_size=100, ttl=3600)
    cached = await cache.get(key)
    await cache.set(key, value)
"""

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional, Generic, TypeVar

import aiohttp

logger = logging.getLogger("bot")

T = TypeVar('T')


class RateLimiter:
    """
    Token bucket rate limiter with O(1) operations.
    
    PERFORMANCE:
    - Uses deque for O(1) append/popleft (instead of list filtering O(n))
    - Only removes expired tokens when checking (lazy cleanup)
    - Thread-safe with asyncio.Lock for concurrent async access
    """

    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self.tokens = {}  # key -> deque of timestamps
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> bool:
        """Check if request is allowed (thread-safe)"""
        async with self._lock:
            now = time.time()
            if key not in self.tokens:
                self.tokens[key] = deque()

            # Remove expired tokens from front (they're oldest)
            token_deque = self.tokens[key]
            while token_deque and now - token_deque[0] >= self.window:
                token_deque.popleft()

            # Check if we can add a new token
            if len(token_deque) < self.limit:
                token_deque.append(now)
                return True
            return False

    async def reset(self, key: str):
        """Reset rate limit for a key (thread-safe)"""
        async with self._lock:
            self.tokens.pop(key, None)


class CircuitBreaker:
    """
    Prevent cascading failures with circuit breaker pattern.
    
    STATES:
    - CLOSED: Normal operation, requests allowed
    - OPEN: Too many failures, requests blocked
    - HALF_OPEN: Testing recovery, limited requests allowed
    
    PERFORMANCE:
    - Thread-safe with asyncio.Lock for concurrent async access
    - State transitions are immediate (no I/O)
    - Async methods for consistent API
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60, success_threshold: int = 2):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.failures = 0
        self.last_failure = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.success_count = 0
        self._lock = asyncio.Lock()

    async def record_success(self):
        """Record a successful request (thread-safe)"""
        async with self._lock:
            if self.state == "HALF_OPEN":
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    self.state = "CLOSED"
                    self.failures = 0
                    self.success_count = 0
            else:
                self.failures = max(0, self.failures - 1)

    async def record_failure(self):
        """Record a failed request (thread-safe)"""
        async with self._lock:
            self.failures += 1
            self.last_failure = time.time()
            if self.failures >= self.failure_threshold:
                self.state = "OPEN"
                logger.warning(f"Circuit breaker opened after {self.failures} failures")

    async def can_attempt(self) -> bool:
        """Check if request can be attempted (thread-safe)"""
        async with self._lock:
            if self.state == "CLOSED":
                return True
            if self.state == "OPEN":
                if self.last_failure and time.time() - self.last_failure > self.recovery_timeout:
                    self.state = "HALF_OPEN"
                    self.success_count = 0
                    return True
                return False
            return True  # HALF_OPEN

    async def get_metrics(self) -> dict:
        """Get circuit breaker metrics (thread-safe)"""
        async with self._lock:
            return {
                "state": self.state,
                "current_failures": self.failures,
                "uptime_percentage": 100.0 if self.state == "CLOSED" else 0.0
            }


class LRUCache(Generic[T]):
    """
    LRU Cache with TTL (Time-To-Live) support.
    
    FEATURES:
    - O(1) get/set operations (lazy TTL expiration on access)
    - Automatic eviction of least recently used items
    - TTL expiration per item
    - Hit/miss statistics tracking
    
    PERFORMANCE:
    - Lazy cleanup (only removes expired items when accessed)
    - Periodic cleanup via cleanup() method
    - Thread-safe with asyncio.Lock for concurrent async access
    """

    def __init__(self, max_size: int = 100, ttl: int = 3600):
        self.max_size = max_size
        self.ttl = ttl
        self._cache = {}  # key -> (value, creation_timestamp)
        self._access_times = {}  # key -> last_access_timestamp
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[T]:
        """Get value from cache (thread-safe)"""
        async with self._lock:
            if key in self._cache:
                value, timestamp = self._cache[key]
                # Check TTL
                if time.time() - timestamp < self.ttl:
                    self._access_times[key] = time.time()
                    self._hits += 1
                    return value
                else:
                    # Expired - remove immediately (lazy cleanup)
                    del self._cache[key]
                    del self._access_times[key]

            self._misses += 1
            return None

    async def set(self, key: str, value: T):
        """Set value in cache (thread-safe)"""
        async with self._lock:
            # Evict least recently used if at capacity
            if len(self._cache) >= self.max_size and key not in self._cache:
                # Find oldest accessed key (LRU eviction)
                oldest_key = min(self._access_times, key=self._access_times.get)
                del self._cache[oldest_key]
                del self._access_times[oldest_key]

            # Store with current timestamp
            self._cache[key] = (value, time.time())
            self._access_times[key] = time.time()

    async def get_stats(self) -> dict:
        """Get cache statistics (thread-safe)"""
        # Clean expired entries first for accurate stats
        await self.cleanup()

        async with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0.0

            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hit_rate": hit_rate,
                "hits": self._hits,
                "misses": self._misses
            }

    async def cleanup(self):
        """Clean up expired entries (thread-safe)"""
        async with self._lock:
            now = time.time()
            # Find all expired keys
            expired_keys = [k for k, (_, ts) in self._cache.items() if now - ts >= self.ttl]
            # Remove them
            for key in expired_keys:
                del self._cache[key]
                del self._access_times[key]


# HttpManager moved to main.py to avoid duplication
# Use bot.http_mgr instead of importing from here


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
    """Deduplication cache for expensive operations (thread-safe)"""

    def __init__(self, ttl: int = 3600):
        self.cache = {}
        self.ttl = ttl
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache (thread-safe)"""
        async with self._lock:
            if key in self.cache:
                val, expires = self.cache[key]
                if time.time() < expires:
                    return val
                del self.cache[key]
            return None

    async def set(self, key: str, value: Any):
        """Set value in cache (thread-safe)"""
        async with self._lock:
            self.cache[key] = (value, time.time() + self.ttl)

    async def cleanup(self):
        """Remove expired entries (thread-safe)"""
        async with self._lock:
            now = time.time()
            self.cache = {k: v for k, v in self.cache.items() if v[1] > now}