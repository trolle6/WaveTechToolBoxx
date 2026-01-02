"""
Bot Utilities - Shared Components for All Cogs

This module provides reusable utility classes for rate limiting, caching,
circuit breaking, and safe file operations. All components are designed
for async/await patterns and thread-safe operations.

COMPONENTS:
- RateLimiter: Token bucket rate limiter (O(1) operations with deque)
- CircuitBreaker: Prevents cascading failures with circuit breaker pattern
- LRUCache: Generic LRU cache with TTL support (expires old entries automatically)
- JsonFile: Thread-safe JSON file operations with atomic writes
- RequestCache: Simple deduplication cache for expensive operations

All classes use asyncio.Lock() for thread-safety in async contexts.
"""

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any, Generic, Optional, TypeVar

logger = logging.getLogger("bot")

T = TypeVar('T')  # Generic type for LRUCache


class RateLimiter:
    """
    Token bucket rate limiter with O(1) operations.
    
    Tracks requests per key using a sliding window. Each request adds a timestamp,
    and old timestamps outside the window are automatically removed.
    
    Attributes:
        limit: Maximum number of requests allowed in the window
        window: Time window in seconds for rate limiting
        tokens: Dict mapping keys to deques of request timestamps
    """

    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self.tokens: dict[str, deque[float]] = {}  # key -> deque of timestamps
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> bool:
        """
        Check if request is allowed within rate limit.
        
        Removes timestamps outside the window, then checks if we're under the limit.
        If allowed, adds current timestamp and returns True.
        
        Args:
            key: Unique identifier for this rate limit bucket (e.g., user ID)
        
        Returns:
            True if request is allowed, False if rate limit exceeded
        """
        async with self._lock:
            now = time.time()
            if key not in self.tokens:
                self.tokens[key] = deque()

            token_deque = self.tokens[key]
            # Remove timestamps outside the sliding window (O(1) per old request)
            while token_deque and now - token_deque[0] >= self.window:
                token_deque.popleft()

            # Check if we're under the limit
            if len(token_deque) < self.limit:
                token_deque.append(now)
                return True
            return False

    async def reset(self, key: str):
        """
        Reset rate limit for a key (clears all tracked requests).
        
        Useful for admin commands or when rate limit should be bypassed.
        
        Args:
            key: Key to reset (removes from tracking)
        """
        async with self._lock:
            self.tokens.pop(key, None)


class CircuitBreaker:
    """
    Circuit breaker pattern to prevent cascading failures.
    
    States:
    - CLOSED: Normal operation, requests allowed
    - OPEN: Too many failures, rejecting requests immediately
    - HALF_OPEN: Testing recovery, allowing limited requests
    
    Transitions:
    - CLOSED → OPEN: When failures exceed threshold
    - OPEN → HALF_OPEN: After recovery_timeout passes
    - HALF_OPEN → CLOSED: After success_threshold consecutive successes
    - HALF_OPEN → OPEN: If any request fails during testing
    """

    # Circuit breaker states
    STATE_CLOSED = "CLOSED"
    STATE_OPEN = "OPEN"
    STATE_HALF_OPEN = "HALF_OPEN"

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60, success_threshold: int = 2):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.failures = 0
        self.last_failure: Optional[float] = None
        self.state = self.STATE_CLOSED
        self.success_count = 0
        self._lock = asyncio.Lock()

    async def record_success(self):
        """
        Record a successful request.
        
        In HALF_OPEN state, counts successes and transitions to CLOSED
        when threshold is reached. In CLOSED state, reduces failure count.
        """
        async with self._lock:
            if self.state == self.STATE_HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    # Recovery successful - return to normal operation
                    self.state = self.STATE_CLOSED
                    self.failures = 0
                    self.success_count = 0
            else:
                # In CLOSED state, reduce failure count (allows recovery from minor issues)
                self.failures = max(0, self.failures - 1)

    async def record_failure(self):
        """
        Record a failed request.
        
        Increments failure count and transitions to OPEN state if threshold exceeded.
        In HALF_OPEN state, immediately transitions back to OPEN (recovery failed).
        """
        async with self._lock:
            self.failures += 1
            self.last_failure = time.time()
            if self.failures >= self.failure_threshold:
                self.state = self.STATE_OPEN
                logger.warning(f"Circuit breaker opened after {self.failures} failures")
            elif self.state == self.STATE_HALF_OPEN:
                # Any failure during testing sends us back to OPEN
                self.state = self.STATE_OPEN
                self.success_count = 0

    async def can_attempt(self) -> bool:
        """
        Check if a request can be attempted based on circuit breaker state.
        
        Returns:
            True if request should be attempted, False if circuit is open
        """
        async with self._lock:
            if self.state == self.STATE_CLOSED:
                return True
            if self.state == self.STATE_OPEN:
                # Check if recovery timeout has passed
                if self.last_failure and time.time() - self.last_failure > self.recovery_timeout:
                    # Transition to HALF_OPEN to test recovery
                    self.state = self.STATE_HALF_OPEN
                    self.success_count = 0
                    return True
                return False
            # HALF_OPEN - allow attempts to test recovery
            return True

    async def get_metrics(self) -> dict:
        """
        Get circuit breaker metrics for monitoring.
        
        Returns:
            Dict with state, failure count, and uptime percentage
        """
        async with self._lock:
            return {
                "state": self.state,
                "current_failures": self.failures,
                "uptime_percentage": 100.0 if self.state == self.STATE_CLOSED else 0.0
            }


class LRUCache(Generic[T]):
    """
    LRU (Least Recently Used) Cache with TTL (Time-To-Live) support.
    
    Automatically evicts least recently accessed entries when cache is full,
    and expires entries older than TTL. Tracks hit/miss statistics.
    
    Attributes:
        max_size: Maximum number of entries before eviction
        ttl: Time-to-live in seconds (entries older than this are expired)
        _cache: Dict mapping keys to (value, creation_timestamp) tuples
        _access_times: Dict mapping keys to last access timestamps (for LRU eviction)
    """

    def __init__(self, max_size: int = 100, ttl: int = 3600):
        self.max_size = max_size
        self.ttl = ttl
        self._cache: dict[str, tuple[T, float]] = {}  # key -> (value, creation_timestamp)
        self._access_times: dict[str, float] = {}  # key -> last_access_timestamp
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[T]:
        """
        Get value from cache.
        
        Checks TTL expiration and updates access time for LRU tracking.
        Returns None if key doesn't exist or is expired.
        
        Args:
            key: Cache key to look up
        
        Returns:
            Cached value if found and not expired, None otherwise
        """
        async with self._lock:
            if key in self._cache:
                value, timestamp = self._cache[key]
                now = time.time()
                # Check if entry is still within TTL
                if now - timestamp < self.ttl:
                    self._access_times[key] = now  # Update access time for LRU
                    self._hits += 1
                    return value
                else:
                    # Entry expired - remove it
                    del self._cache[key]
                    del self._access_times[key]

            self._misses += 1
            return None

    async def set(self, key: str, value: T):
        """
        Set value in cache.
        
        Evicts least recently used entry if cache is full and key is new.
        Updates both cache and access times.
        
        Args:
            key: Cache key
            value: Value to cache
        """
        async with self._lock:
            # Evict LRU entry if cache is full and this is a new key
            if len(self._cache) >= self.max_size and key not in self._cache:
                # Find least recently accessed key (oldest access time)
                oldest_key = min(self._access_times, key=self._access_times.get)
                del self._cache[oldest_key]
                del self._access_times[oldest_key]

            now = time.time()
            self._cache[key] = (value, now)
            self._access_times[key] = now

    async def get_stats(self) -> dict:
        """Get cache statistics"""
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
        """
        Clean up expired entries from cache.
        
        Removes all entries that have exceeded their TTL. Useful for periodic
        maintenance to prevent memory growth from expired entries.
        """
        async with self._lock:
            now = time.time()
            expired_keys = [k for k, (_, ts) in self._cache.items() if now - ts >= self.ttl]
            for key in expired_keys:
                del self._cache[key]
                del self._access_times[key]


class JsonFile:
    """
    Thread-safe JSON file operations with atomic writes.
    
    Uses asyncio.Lock to prevent concurrent access issues when multiple
    tasks try to read/write the same file simultaneously.
    
    Note: save() does not use atomic writes (write-temp-replace) like
    secret_santa_storage.py does, but is simpler for less critical files.
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self.lock = asyncio.Lock()

    async def load(self, default: Any = None) -> Any:
        """
        Load JSON file with error handling.
        
        Args:
            default: Default value to return if file doesn't exist or is invalid
        
        Returns:
            Parsed JSON data, or default if file is missing/invalid
        """
        async with self.lock:
            if self.path.exists():
                try:
                    return json.loads(self.path.read_text(encoding='utf-8'))
                except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
                    logger.error(f"JSON load error for {self.path}: {e}")
            return default or {}

    async def save(self, data: Any):
        """
        Save JSON file with error handling.
        
        Args:
            data: Data to serialize to JSON (must be JSON-serializable)
        
        Raises:
            OSError: If file cannot be written (logged but not caught)
        """
        async with self.lock:
            try:
                self.path.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding='utf-8'
                )
            except OSError as e:
                logger.error(f"JSON save error for {self.path}: {e}")
                raise  # Re-raise so caller knows save failed


class RequestCache:
    """
    Simple deduplication cache for expensive operations.
    
    Stores key-value pairs with expiration times. Automatically removes
    expired entries on access. Simpler than LRUCache but doesn't track
    access times or limit size.
    
    Use case: Prevent duplicate expensive operations (API calls, calculations)
    within a time window.
    """

    def __init__(self, ttl: int = 3600):
        self.cache: dict[str, tuple[Any, float]] = {}  # key -> (value, expiration_timestamp)
        self.ttl = ttl
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache if not expired.
        
        Args:
            key: Cache key to look up
        
        Returns:
            Cached value if found and not expired, None otherwise
        """
        async with self._lock:
            if key in self.cache:
                val, expires = self.cache[key]
                now = time.time()
                if now < expires:
                    return val
                # Entry expired - remove it
                del self.cache[key]
            return None

    async def set(self, key: str, value: Any):
        """
        Set value in cache with TTL expiration.
        
        Args:
            key: Cache key
            value: Value to cache (will expire after TTL)
        """
        async with self._lock:
            self.cache[key] = (value, time.time() + self.ttl)

    async def cleanup(self):
        """
        Remove all expired entries from cache.
        
        Useful for periodic maintenance to prevent memory growth.
        Note: Expired entries are also removed on get(), so this is optional.
        """
        async with self._lock:
            now = time.time()
            self.cache = {k: v for k, v in self.cache.items() if v[1] > now}
