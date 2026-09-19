"""
Bot Utilities - Shared Components for All Cogs

This module provides reusable utility classes for rate limiting, caching,
circuit breaking, Discord interaction helpers, and atomic JSON persistence.
All components are designed for async/await patterns and thread-safe operations.

COMPONENTS:
- autocomplete_safety_wrapper: Ensures autocomplete callbacks always return a list
- RateLimiter: Token bucket rate limiter (O(1) operations with deque)
- CircuitBreaker: Prevents cascading failures with circuit breaker pattern
- LRUCache: Generic LRU cache with TTL support (expires old entries automatically)
- safe_edit_response / safe_followup_send: Retry-aware Discord interaction helpers
- load_json_file / atomic_save_json: Crash-safe JSON persistence with size limits

All classes use asyncio.Lock() for thread-safety in async contexts.
"""

import asyncio
import functools
import json
import logging
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Generic, Optional, TypeVar

import disnake

logger = logging.getLogger("bot")

T = TypeVar('T')  # Generic type for LRUCache

# Max JSON file size to prevent DoS from huge/corrupt files (10MB)
LOAD_JSON_MAX_BYTES = 10 * 1024 * 1024

__all__ = [
    'autocomplete_safety_wrapper',
    'RateLimiter',
    'CircuitBreaker',
    'LRUCache',
    'safe_filename_in_dir',
    'get_openai_headers',
    'safe_edit_response',
    'safe_followup_send',
    'load_json_file',
    'atomic_save_json',
    'LOAD_JSON_MAX_BYTES',
]


def get_openai_headers(api_key: Optional[str]) -> dict[str, str]:
    """Build OpenAI API headers; returns empty dict if key is missing."""
    if not api_key:
        return {}
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def safe_filename_in_dir(filename: str, directory: Path) -> Optional[Path]:
    """
    Resolve a stored filename to a path guaranteed under ``directory``.
    Blocks path traversal (``../``, absolute paths, etc.).
    """
    if not filename or not str(filename).strip():
        return None
    name = Path(filename).name
    if not name or name in (".", ".."):
        return None
    try:
        root = directory.resolve()
        resolved = (root / name).resolve()
        resolved.relative_to(root)
        return resolved
    except (ValueError, OSError):
        return None


def autocomplete_safety_wrapper(func):
    """
    Decorator to ensure autocomplete functions always return a list.

    Discord autocomplete requires a list of choices. This wrapper catches
    exceptions and normalizes return values so autocomplete never crashes.
    Used by SecretSanta and DistributeZip cogs.
    """
    @functools.wraps(func)
    async def wrapper(self, inter, string: str):
        try:
            result = await func(self, inter, string)
            if isinstance(result, list):
                return [str(item) for item in result]
            if result is None:
                return []
            if isinstance(result, str):
                if hasattr(self, "logger"):
                    self.logger.error(f"{func.__name__} returned string instead of list")
                return []
            return [str(item) for item in list(result)]
        except Exception as e:
            if hasattr(self, "logger"):
                self.logger.error(f"Error in {func.__name__}: {e}", exc_info=True)
            return []
    return wrapper


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
            token_deque = self.tokens.get(key)
            if token_deque is not None:
                while token_deque and now - token_deque[0] >= self.window:
                    token_deque.popleft()
                # Drop idle buckets so per-user keys cannot accumulate forever
                if not token_deque:
                    del self.tokens[key]
                    token_deque = None

            if token_deque is None:
                token_deque = deque()
                self.tokens[key] = token_deque

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
    
    Uses OrderedDict for O(1) LRU eviction. Automatically evicts least recently
    accessed entries when cache is full, and expires entries older than TTL.
    Tracks hit/miss statistics.
    
    Attributes:
        max_size: Maximum number of entries before eviction
        ttl: Time-to-live in seconds (entries older than this are expired)
        _cache: OrderedDict mapping keys to (value, creation_timestamp) tuples
            Order reflects access pattern: most recent at end, oldest at beginning
    """

    def __init__(self, max_size: int = 100, ttl: int = 3600):
        self.max_size = max_size
        self.ttl = ttl
        # OrderedDict: end = most recent, beginning = least recent (for O(1) eviction)
        self._cache: OrderedDict[str, tuple[T, float]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[T]:
        """
        Get value from cache.
        
        Checks TTL expiration and moves key to end (most recent) for LRU tracking.
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
                    # Move to end (most recent) - O(1) operation
                    self._cache.move_to_end(key)
                    self._hits += 1
                    return value
                else:
                    # Entry expired - remove it
                    del self._cache[key]

            self._misses += 1
            return None

    async def set(self, key: str, value: T):
        """
        Set value in cache.
        
        Evicts least recently used entry (beginning of OrderedDict) if cache is full.
        If key exists, updates it and moves to end. Otherwise adds to end.
        
        Args:
            key: Cache key
            value: Value to cache
        """
        async with self._lock:
            now = time.time()
            
            if key in self._cache:
                # Update existing entry and move to end (most recent)
                self._cache[key] = (value, now)
                self._cache.move_to_end(key)
            else:
                # Evict LRU entry if cache is full - O(1) pop from beginning
                if len(self._cache) >= self.max_size:
                    self._cache.popitem(last=False)  # Remove oldest (least recent)
                
                # Add new entry at end (most recent)
                self._cache[key] = (value, now)

    async def get_stats(self) -> dict:
        """Get cache statistics (cleanup runs under the same lock — Lock is not reentrant)."""
        async with self._lock:
            now = time.time()
            expired_keys = [k for k, (_, ts) in self._cache.items() if now - ts >= self.ttl]
            for key in expired_keys:
                del self._cache[key]

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


def load_json_file(path: Path, default: Any = None, *, max_bytes: int = LOAD_JSON_MAX_BYTES) -> Any:
    """
    Load JSON from disk with graceful error handling and a size cap.

    Returns parsed content on success. On failure, returns ``default`` if
    provided, otherwise ``{}``.
    """
    fallback = default if default is not None else {}
    if path is None or not hasattr(path, "exists"):
        return fallback
    if not path.exists():
        return fallback
    try:
        size = path.stat().st_size
        if size > max_bytes:
            logger.warning("JSON file too large (%s bytes): %s", size, path)
            return fallback
        text = path.read_text(encoding='utf-8', errors='replace').strip()
        if not text:
            return fallback
        return json.loads(text)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        logger.warning("Failed to load JSON from %s: %s", path, e)
    return fallback


def atomic_save_json(path: Path, data: Any, logger: Optional[logging.Logger] = None) -> None:
    """
    Save JSON atomically with crash-safe write-temp-replace.

    Writes to ``path.tmp`` first, then atomically replaces the target file.
    """
    log = logger or globals()["logger"]
    temp = path.with_suffix('.tmp')
    try:
        temp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        temp.replace(path)
    except Exception as e:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
        log.error("Failed to save JSON to %s: %s", path, e)
        raise


async def safe_edit_response(
    log: logging.Logger,
    inter: disnake.ApplicationCommandInteraction,
    content: Optional[str] = None,
    embed: Optional[disnake.Embed] = None,
    view: Optional[disnake.ui.View] = None,
    file: Optional[disnake.File] = None,
    max_retries: int = 3,
) -> bool:
    """Edit an interaction response with retry logic for transient Discord errors."""
    for attempt in range(max_retries):
        try:
            kwargs = {}
            if content is not None:
                kwargs['content'] = content
            if embed is not None:
                kwargs['embed'] = embed
            if view is not None:
                kwargs['view'] = view
            if file is not None:
                kwargs['file'] = file
            if not kwargs:
                return True

            await asyncio.wait_for(
                inter.edit_original_response(**kwargs),
                timeout=10.0,
            )
            return True
        except disnake.errors.NotFound:
            log.warning("Interaction expired before edit: %s", inter.id)
            return False
        except disnake.errors.InteractionResponded:
            return True
        except disnake.HTTPException as e:
            status = getattr(e, 'status', None)
            if status == 429:
                retry_after = getattr(e, 'retry_after', 1.0)
                if attempt < max_retries - 1:
                    log.warning(
                        "Rate limited on edit_response, waiting %ss (attempt %s/%s)",
                        retry_after, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(retry_after)
                    continue
            elif status and status >= 500:
                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 5.0)
                    log.warning(
                        "Discord server error %s on edit_response, retrying in %ss (attempt %s/%s)",
                        status, wait_time, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(wait_time)
                    continue
            else:
                log.error("HTTP error %s on edit_response: %s", status, e)
                return False
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            if attempt < max_retries - 1:
                wait_time = min(2 ** attempt, 5.0)
                log.warning(
                    "Connection error on edit_response, retrying in %ss (attempt %s/%s): %s",
                    wait_time, attempt + 1, max_retries, e,
                )
                await asyncio.sleep(wait_time)
                continue
            log.error("Connection error on edit_response after %s attempts: %s", max_retries, e)
            return False
        except Exception as e:
            log.error("Unexpected error on edit_response: %s", e, exc_info=True)
            return False
    return False


async def safe_followup_send(
    log: logging.Logger,
    inter: disnake.ApplicationCommandInteraction,
    content: Optional[str] = None,
    embed: Optional[disnake.Embed] = None,
    view: Optional[disnake.ui.View] = None,
    file: Optional[disnake.File] = None,
    ephemeral: bool = False,
    max_retries: int = 3,
) -> Optional[disnake.WebhookMessage]:
    """Send an interaction followup with retry logic for transient Discord errors."""
    for attempt in range(max_retries):
        try:
            kwargs = {'ephemeral': ephemeral}
            if content is not None:
                kwargs['content'] = content
            if embed is not None:
                kwargs['embed'] = embed
            if view is not None:
                kwargs['view'] = view
            if file is not None:
                kwargs['file'] = file

            return await asyncio.wait_for(
                inter.followup.send(**kwargs),
                timeout=10.0,
            )
        except disnake.errors.NotFound:
            log.warning("Interaction expired before followup: %s", inter.id)
            return None
        except disnake.HTTPException as e:
            status = getattr(e, 'status', None)
            if status == 429:
                retry_after = getattr(e, 'retry_after', 1.0)
                if attempt < max_retries - 1:
                    log.warning(
                        "Rate limited on followup_send, waiting %ss (attempt %s/%s)",
                        retry_after, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(retry_after)
                    continue
            elif status and status >= 500:
                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 5.0)
                    log.warning(
                        "Discord server error %s on followup_send, retrying in %ss (attempt %s/%s)",
                        status, wait_time, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(wait_time)
                    continue
            else:
                log.error("HTTP error %s on followup_send: %s", status, e)
                return None
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            if attempt < max_retries - 1:
                wait_time = min(2 ** attempt, 5.0)
                log.warning(
                    "Connection error on followup_send, retrying in %ss (attempt %s/%s): %s",
                    wait_time, attempt + 1, max_retries, e,
                )
                await asyncio.sleep(wait_time)
                continue
            log.error("Connection error on followup_send after %s attempts: %s", max_retries, e)
            return None
        except Exception as e:
            log.error("Unexpected error on followup_send: %s", e, exc_info=True)
            return None
    return None
