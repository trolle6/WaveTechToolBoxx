"""
WaveTechToolBox - Discord Bot Main Entry Point

FEATURES:
- 🎤 Text-to-Speech (OpenAI TTS)
- 🎨 AI Image Generation (DALL-E 3)
- 🎄 Secret Santa Event Management

USAGE:
    python main.py
"""

import asyncio
import io
import logging
import logging.handlers
import os
import signal
import sys
import time
import warnings
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp
import disnake
from disnake.ext import commands
from dotenv import load_dotenv

load_dotenv("config.env", override=True)


# ============ CONFIG ============
# All Discord IDs and bot settings are centralised here and in config.env.
# Load with: load_dotenv("config.env"); then bot.config = Config() → access as bot.config.DISCORD_* etc.
#
# Required (config.env):
#   DISCORD_TOKEN          - Bot token
#   DISCORD_CHANNEL_ID     - main: send_discord_message; voice_processing: optional TTS channel restriction
#   DISCORD_LOG_CHANNEL_ID - DiscordLogHandler, send_to_discord_log, reconnect notifications
#   DISCORD_MODERATOR_ROLE_ID - secret_santa_checks: mod_check() for /ss mod commands
#   OPENAI_API_KEY        - TTS, DALL-E, Secret Santa anonymize
#
# Optional (CONFIG_DEFAULTS below or in config.env):
#   TTS_ROLE_ID            - voice_processing: restrict who can use TTS (None = everyone)
#   MAX_QUEUE_SIZE, RATE_LIMIT_*, MAX_TTS_CACHE, VOICE_TIMEOUT, etc. - TTS/DALL-E tuning
# Per-event guild_id (not config): Secret Santa stores guild_id on the active event (inter.guild.id).
#
REQUIRED_CONFIG_KEYS = {
    "DISCORD_TOKEN", "DISCORD_CHANNEL_ID",
    "DISCORD_LOG_CHANNEL_ID", "DISCORD_MODERATOR_ROLE_ID", "OPENAI_API_KEY",
}

CONFIG_DEFAULTS = {
    "DEBUG_MODE": False,
    "LOG_LEVEL": "INFO",
    "MAX_TTS_CACHE": 50,
    "SKIP_API_VALIDATION": False,
    "MAX_QUEUE_SIZE": 50,
    "RATE_LIMIT_REQUESTS": 15,
    "RATE_LIMIT_WINDOW": 60,
    "VOICE_TIMEOUT": 10,
    "AUTO_DISCONNECT_TIMEOUT": 300,
    "TTS_ROLE_ID": None,
    "SS_DEBUG_START": False,  # Skip "year already archived" warning on /ss start (testing only)
}


class Config:
    """
    Configuration loader with validation.
    
    Loads environment variables, validates required keys, and provides
    type-safe access to configuration values with sensible defaults.
    """
    
    def __init__(self):
        self.data: dict[str, Any] = {}
        missing = [key for key in REQUIRED_CONFIG_KEYS if not os.getenv(key)]
        
        if missing:
            raise RuntimeError(f"Missing required config: {', '.join(missing)}")
        
        # Load required vars (already validated as non-empty)
        for key in REQUIRED_CONFIG_KEYS:
            val = os.getenv(key)
            self.data[key] = val.strip() if isinstance(val, str) else val
        
        # Load optional vars with defaults and type conversion
        for key, default in CONFIG_DEFAULTS.items():
            val = os.getenv(key)
            if val is None:
                self.data[key] = default
            elif isinstance(default, bool):
                self.data[key] = str(val).lower() == "true"
            elif isinstance(default, int):
                try:
                    self.data[key] = int(val)
                except ValueError:
                    warnings.warn(f"Invalid integer for {key!r}, using default {default!r}", UserWarning)
                    self.data[key] = default
            else:
                self.data[key] = val

        if self.data.get("DEBUG_MODE"):
            self.data["LOG_LEVEL"] = "DEBUG"

        log_level = str(self.data.get("LOG_LEVEL", "INFO")).upper()
        if not isinstance(getattr(logging, log_level, None), int):
            warnings.warn(f"Invalid LOG_LEVEL {log_level!r}, using INFO", UserWarning)
            log_level = "INFO"
        self.data["LOG_LEVEL"] = log_level
    
    def __getattr__(self, name: str) -> Any:
        """Access config values via attribute (e.g., config.DISCORD_TOKEN)"""
        key = name.upper()
        if key in self.data:
            return self.data[key]
        return CONFIG_DEFAULTS.get(key)


# ============ HTTP MANAGER ============
# HTTP connection pool configuration
HTTP_CONNECTION_LIMIT = 10  # Maximum total connections
HTTP_CONNECTION_LIMIT_PER_HOST = 5  # Maximum connections per host
HTTP_DNS_CACHE_TTL = 300  # DNS cache time-to-live (seconds)
HTTP_DEFAULT_TIMEOUT = 30  # Default request timeout (seconds)


class HttpManager:
    """
    Singleton HTTP session manager.
    
    Maintains a single aiohttp ClientSession with connection pooling
    for efficient reuse across all API requests (OpenAI, etc.).
    """
    _instance = None
    _session: Optional[aiohttp.ClientSession] = None

    def __new__(cls):
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._session_lock = asyncio.Lock()
            cls._instance = inst
        return cls._instance

    def _session_needs_rebuild(
        self,
        session: Optional[aiohttp.ClientSession],
        current_loop: Optional[asyncio.AbstractEventLoop],
    ) -> bool:
        if session is None or session.closed:
            return True
        session_loop = getattr(session, "_loop", None)
        if session_loop is None or session_loop.is_closed():
            return True
        if current_loop is not None and session_loop is not current_loop:
            return True
        return False

    async def get_session(self, timeout: int = HTTP_DEFAULT_TIMEOUT) -> aiohttp.ClientSession:
        """
        Get or create HTTP session with connection pooling.
        
        Handles event loop changes (e.g. bot crash+retry) by recreating the session
        when the current loop differs from the session's loop or the loop is closed.
        
        Args:
            timeout: Request timeout in seconds (default: HTTP_DEFAULT_TIMEOUT)
        
        Returns:
            Configured aiohttp ClientSession
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if not self._session_needs_rebuild(self._session, current_loop):
            return self._session  # hot path: no lock

        async with self._session_lock:
            if not self._session_needs_rebuild(self._session, current_loop):
                return self._session

            if self._session and not self._session.closed:
                try:
                    await self._session.close()
                except Exception:
                    pass
                self._session = None

            connector = aiohttp.TCPConnector(
                limit=HTTP_CONNECTION_LIMIT,
                limit_per_host=HTTP_CONNECTION_LIMIT_PER_HOST,
                ttl_dns_cache=HTTP_DNS_CACHE_TTL,
                enable_cleanup_closed=True,
                force_close=False,
            )
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                connector=connector,
                headers={"Connection": "keep-alive"},
            )
            return self._session

    async def invalidate_session(self):
        """Force-close current session so next get_session() creates a fresh one.
        Use when the session's event loop has closed (e.g. after validation, or bot restart).
        """
        async with self._session_lock:
            if self._session and not self._session.closed:
                try:
                    await self._session.close()
                except Exception:
                    pass
            self._session = None

    async def close(self):
        """Cleanly close HTTP session and connection pool"""
        async with self._session_lock:
            sess = self._session
            self._session = None
            if not sess or sess.closed:
                return
            try:
                await sess.close()
                await asyncio.sleep(0.5)  # Allow pending requests to finish
                if hasattr(sess, '_connector') and sess._connector:
                    await sess._connector.close()
            except Exception:
                pass


# ============ DISCORD LOGGING ============
class DiscordLogHandler(logging.Handler):
    """Send log messages to Discord channel"""
    
    EMOJI_MAP = {"WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}
    # Cap dedupe keys so unusual log spam cannot grow this dict without bound
    _DEDUPE_MAX_KEYS = 512
    _DEDUPE_WINDOW_SEC = 60.0
    
    def __init__(self, log_channel_id: int):
        super().__init__()
        self.log_channel_id = log_channel_id
        self.bot: Optional[disnake.Client] = None
        self.message_queue = asyncio.Queue(maxsize=50)
        self.sender_task: Optional[asyncio.Task] = None
        self._last_message: OrderedDict[str, float] = OrderedDict()
    
    def set_bot(self, bot: disnake.Client):
        """Set bot instance and start sender task"""
        self.bot = bot
        if self.sender_task is None or self.sender_task.done():
            self.sender_task = asyncio.create_task(self._sender_loop())
    
    def emit(self, record: logging.LogRecord):
        """Queue log message for Discord"""
        if not self.bot or not self.log_channel_id or record.levelno < logging.WARNING:
            return
        
        # Rate limit duplicate messages
        msg_key = f"{record.levelname}:{record.getMessage()[:50]}"
        now = time.time()
        last = self._last_message.get(msg_key)
        if last is not None and (now - last) < self._DEDUPE_WINDOW_SEC:
            return
        if last is not None:
            self._last_message.move_to_end(msg_key)
        elif len(self._last_message) >= self._DEDUPE_MAX_KEYS:
            self._last_message.popitem(last=False)
        self._last_message[msg_key] = now
        
        # Format message
        emoji = self.EMOJI_MAP.get(record.levelname, "ℹ️")
        message = f"{emoji} **{record.levelname}** | {record.name}\n```\n{record.getMessage()}\n```"
        if len(message) > 1900:
            message = message[:1900] + "...\n```"
        
        # Queue for sending
        try:
            self.message_queue.put_nowait(message)
        except asyncio.QueueFull:
            pass
    
    async def _sender_loop(self):
        """Background task to send queued messages"""
        while True:
            try:
                message = await self.message_queue.get()
                if self.bot and self.log_channel_id:
                    channel = self.bot.get_channel(self.log_channel_id)
                    if channel:
                        await channel.send(message)
                    await asyncio.sleep(1)  # Rate limit
            except asyncio.CancelledError:
                break
            except Exception:
                continue
    
    def close(self):
        """Clean shutdown"""
        if self.sender_task:
            self.sender_task.cancel()
        super().close()


# ============ LOGGING ============
LOG_FILE_MAX_BYTES = 5_000_000  # 5MB - max log file size before rotation
LOG_FILE_BACKUP_COUNT = 5  # Number of rotated log files to keep


def setup_logging(config: Config) -> tuple[logging.Logger, DiscordLogHandler]:
    """Setup logging with file, console, and Discord handlers"""
    logger = logging.getLogger("bot")
    logger.setLevel(config.LOG_LEVEL)
    
    # Prevent duplicate handlers
    if logger.handlers:
        for handler in logger.handlers:
            if isinstance(handler, DiscordLogHandler):
                return logger, handler
    
    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # File handler with rotation to prevent log files from growing too large
    fh = logging.handlers.RotatingFileHandler(
        "bot.log",
        maxBytes=LOG_FILE_MAX_BYTES,
        backupCount=LOG_FILE_BACKUP_COUNT,
        encoding="utf-8"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    
    # Console handler - use UTF-8 to avoid UnicodeEncodeError on Windows (cp1252)
    utf8_stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace') if hasattr(sys.stdout, 'buffer') else sys.stdout
    ch = logging.StreamHandler(utf8_stream)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    
    # Discord handler
    discord_handler = DiscordLogHandler(config.DISCORD_LOG_CHANNEL_ID)
    discord_handler.setLevel(logging.WARNING)
    logger.addHandler(discord_handler)
    
    return logger, discord_handler


# OpenAI API validation configuration
OPENAI_VALIDATION_URL = "https://api.openai.com/v1/models"
OPENAI_VALIDATION_TIMEOUT = 10  # seconds
OPENAI_API_KEY_PREFIX = "sk-"


async def validate_openai_key(key: str, logger: logging.Logger, http_mgr: "HttpManager") -> bool:
    """
    Validate OpenAI API key format and connectivity.
    
    Checks key format (must start with 'sk-') and makes a test API call.
    Uses shared HttpManager session for connection pool reuse.
    Allows bot to start even on network errors (may be transient).
    
    Args:
        key: OpenAI API key to validate
        logger: Logger instance for validation messages
        http_mgr: HttpManager instance for session reuse
    
    Returns:
        True if key appears valid, False if format is wrong or key is invalid
    """
    key = key.strip() if key else ""
    
    if not key:
        logger.error("OPENAI_API_KEY is empty")
        return False
    
    if not key.startswith(OPENAI_API_KEY_PREFIX):
        logger.error(f"Invalid API key format (should start with '{OPENAI_API_KEY_PREFIX}')")
        return False
    
    try:
        session = await http_mgr.get_session()
        async with session.get(
                OPENAI_VALIDATION_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=aiohttp.ClientTimeout(total=OPENAI_VALIDATION_TIMEOUT)
            ) as r:
                if r.status == 200:
                    logger.info("OpenAI API key is valid")
                    return True
                elif r.status == 401:
                    logger.error("API key is invalid or expired")
                    return False
                else:
                    logger.warning(f"Unexpected API response: {r.status} - allowing bot to start")
                    return True  # Allow start on unexpected responses (may be transient)
    except asyncio.TimeoutError:
        logger.warning("API validation timeout - allowing bot to start (may be network issue)")
        return True
    except Exception as e:
        logger.warning(f"API validation error: {e} - allowing bot to start")
        return True


# ============ BOT SETUP ============
PYTHON_MIN_VERSION = (3, 10)  # disnake 2.12+ (DAVE voice) requires Python 3.10+
MIN_DISNAKE_VERSION = (2, 12, 0)  # Discord mandates DAVE (E2EE) for voice; older libs get close 4017


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    """Parse '2.12.0' -> (2, 12, 0) for minimum-version checks."""
    parts: list[int] = []
    for piece in version.split(".")[:3]:
        try:
            parts.append(int(piece))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


def validate_runtime_dependencies(logger: logging.Logger) -> bool:
    """
    Verify disnake and Discord voice (DAVE) dependencies before loading cogs.

    Returns False if requirements are not met (caller should exit).
    """
    import importlib.util

    if _parse_version_tuple(disnake.__version__) < MIN_DISNAKE_VERSION:
        logger.critical(
            f"disnake {disnake.__version__} is too old; need "
            f"{MIN_DISNAKE_VERSION[0]}.{MIN_DISNAKE_VERSION[1]}+ for Discord voice (DAVE/E2EE). "
            f'Run: pip install -r requirements.txt'
        )
        return False

    if importlib.util.find_spec("dave") is None:
        logger.critical(
            "dave-py is not installed (required for Discord voice since 2026). "
            'Install with: pip install "disnake[voice]>=2.12.0"'
        )
        return False

    try:
        import aiohttp
        from importlib.metadata import PackageNotFoundError, version as pkg_version

        try:
            dotenv_ver = pkg_version("python-dotenv")
        except PackageNotFoundError:
            dotenv_ver = "unknown"
        logger.info(
            f"Runtime deps OK: disnake {disnake.__version__}, "
            f"aiohttp {aiohttp.__version__}, python-dotenv {dotenv_ver}, dave-py present"
        )
    except ImportError as e:
        logger.critical(f"Missing dependency: {e}. Run: pip install -r requirements.txt")
        return False

    return True


DISCONNECT_WARNING_THRESHOLD = 10  # Warn if disconnects exceed this in 24h
SECONDS_PER_DAY = 86400  # Used for 24h disconnect tracking
MAX_CONNECTION_PERIODS = 10000  # Max periods to track (safety limit)

try:
    config = Config()
except RuntimeError as e:
    print(f"Fatal: {e}")
    sys.exit(1)

logger, discord_handler = setup_logging(config)

# Initialize bot with all intents (needed for voice, members, etc.)
intents = disnake.Intents.all()
bot = commands.InteractionBot(intents=intents)
bot.config = config
bot.logger = logger
bot.http_mgr = HttpManager()
bot.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bot-io")
bot.discord_handler = discord_handler
bot.ready_once = False

# Connection tracking for monitoring stability
bot._connection_stats = {
    "disconnects": [],
    "connection_periods": [],  # List of (start_time, end_time) tuples for last 24h
    "last_disconnect": None,
    "last_connect": None,  # Timestamp of last successful connection
    "connection_start": None,  # Timestamp when bot first connected
    "disconnect_count_24h": 0,
    "longest_uptime": 0.0  # Longest continuous connection period
}


# ============ DISCORD UTILITIES ============
LEVEL_EMOJIS = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨", "SUCCESS": "✅"}

async def send_discord_message(channel_id: int, message: str, level: str = "INFO", include_level: bool = True):
    """Send formatted message to Discord channel"""
    if not bot.ready_once:
        return
    
    try:
        channel = bot.get_channel(channel_id)
        if not channel:
            return
        
        emoji = LEVEL_EMOJIS.get(level, "ℹ️")
        formatted = f"{emoji} **{level}** | {message}" if include_level else f"{emoji} {message}"
        
        if len(formatted) > 2000:
            formatted = formatted[:1997] + "..."
        
        await channel.send(formatted)
    except Exception as e:
        logger.debug("Failed to send Discord message to channel %s: %s", channel_id, e)

async def send_to_discord_log(message: str, level: str = "INFO"):
    """Send message to Discord log channel"""
    await send_discord_message(config.DISCORD_LOG_CHANNEL_ID, message, level, include_level=True)

async def send_to_discord_channel(message: str, level: str = "INFO"):
    """Send message to default Discord channel"""
    await send_discord_message(config.DISCORD_CHANNEL_ID, message, level, include_level=False)

bot.send_to_discord_log = send_to_discord_log
bot.send_to_discord_channel = send_to_discord_channel


# ============ DAILY MAINTENANCE ============
async def daily_maintenance_loop():
    """
    Run once per day at midnight UTC. Cogs may implement async daily_maintenance()
    for work that should not run on a tight loop (e.g. DALL-E image URL cache).
    Voice uses its own 5-minute cleanup task instead.
    """
    while True:
        now = datetime.now(timezone.utc)
        tomorrow = (now.date() + timedelta(days=1))
        next_midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
        wait_seconds = (next_midnight - now).total_seconds()
        if wait_seconds <= 0:
            wait_seconds = SECONDS_PER_DAY
        logger.info(f"Daily maintenance next at midnight UTC (in {wait_seconds/3600:.1f}h)")
        await asyncio.sleep(wait_seconds)
        logger.info("Daily maintenance (midnight UTC) — running cog cleanups")
        for name, cog in bot.cogs.items():
            fn = getattr(cog, "daily_maintenance", None)
            if asyncio.iscoroutinefunction(fn):
                try:
                    await fn()
                except Exception as e:
                    logger.error(f"Daily maintenance failed for {name}: {e}", exc_info=True)
        # No second fixed sleep here; loop recalculates next UTC midnight each iteration.


# ============ BOT EVENTS ============
@bot.event
async def on_ready():
    """Bot ready event - tracks connection start time for stability metrics"""
    now = time.time()
    stats = bot._connection_stats
    
    if not bot.ready_once:
        logger.info(f"Logged in as {bot.user}")
        
        # Track connection start time for uptime calculation
        stats["connection_start"] = now
        stats["last_connect"] = now
        
        if discord_handler:
            discord_handler.set_bot(bot)
            logger.info("Discord logging handler connected")

        # Clear any HTTP session from pre-bot validation (asyncio.run uses a different loop)
        try:
            await bot.http_mgr.invalidate_session()
        except Exception:
            pass

        asyncio.create_task(daily_maintenance_loop())
        
        try:
            channel = bot.get_channel(config.DISCORD_LOG_CHANNEL_ID)
            if channel:
                await channel.send(f"🤖 **Bot Online** | {bot.user.name} is ready!")
        except Exception:
            pass
        
        bot.ready_once = True
    else:
        # Reconnect after disconnect: on_resumed logs downtime; only track connect time here.
        stats["last_connect"] = now


@bot.event
async def on_disconnect():
    """
    Track disconnections and log frequency with uptime metrics.
    
    Monitors connection stability by tracking disconnect timestamps and calculating
    uptime statistics. Disconnects are often network-level or Discord API-side issues
    (not code problems) - the reconnection system handles these automatically.
    """
    now = time.time()
    stats = bot._connection_stats
    
    # Calculate uptime since last connection
    if stats["last_connect"]:
        uptime = now - stats["last_connect"]
        
        # Record this connection period (start, end)
        stats["connection_periods"].append((stats["last_connect"], now))
        
        if uptime > stats["longest_uptime"]:
            stats["longest_uptime"] = uptime
        
        # Format uptime for logging
        if uptime < 60:
            uptime_str = f"{uptime:.1f}s"
        elif uptime < 3600:
            uptime_str = f"{uptime/60:.1f}m"
        else:
            uptime_str = f"{uptime/3600:.1f}h"
    else:
        uptime_str = "unknown"
        uptime = 0
    
    stats["last_disconnect"] = now
    stats["disconnects"].append(now)
    
    # Keep only last 24 hours for accurate tracking
    cutoff = now - SECONDS_PER_DAY
    stats["disconnects"] = [d for d in stats["disconnects"] if d > cutoff]
    stats["disconnect_count_24h"] = len(stats["disconnects"])
    
    # Calculate total uptime in last 24 hours by summing connection periods
    # We calculate BEFORE pruning so we include all periods that have any portion in last 24h
    total_uptime_24h = 0.0
    for start, end in stats["connection_periods"]:
        # Clamp period to last 24 hours (in case start is before cutoff)
        period_start = max(start, cutoff)
        period_end = min(end, now)  # Ensure we don't count future time
        if period_end > period_start:  # Only add if valid period
            total_uptime_24h += (period_end - period_start)
    
    # Prune connection periods older than 24 hours (after calculation)
    stats["connection_periods"] = [
        (start, end) for start, end in stats["connection_periods"]
        if end > cutoff  # Keep if end time is within last 24h
    ]
    
    # Safety limit: prevent unbounded growth even in extreme edge cases
    # Keep only the most recent periods if list somehow exceeds reasonable size
    # (Should never happen with proper pruning, but protects against bugs/edge cases)
    if len(stats["connection_periods"]) > MAX_CONNECTION_PERIODS:
        # Sort by end time and keep only the most recent periods
        stats["connection_periods"].sort(key=lambda x: x[1])  # Sort by end time
        stats["connection_periods"] = stats["connection_periods"][-MAX_CONNECTION_PERIODS:]
        logger.warning(f"Connection periods list exceeded safety limit, trimmed to {MAX_CONNECTION_PERIODS}")
    
    # Calculate uptime percentage (capped at 100%)
    uptime_percent = min(100.0, (total_uptime_24h / SECONDS_PER_DAY * 100)) if total_uptime_24h > 0 else 0.0
    
    # Log disconnect with context
    if len(stats["disconnects"]) > 1:
        time_since = now - stats["disconnects"][-2]
        logger.info(
            f"⚠️ Bot disconnected (#{stats['disconnect_count_24h']} in 24h, "
            f"{time_since:.1f}s since last, uptime: {uptime_str})"
        )
    else:
        logger.info(
            f"⚠️ Bot disconnected (#{stats['disconnect_count_24h']} in 24h, uptime: {uptime_str})"
        )
    
    # Warn if disconnects are too frequent (indicates stability issues)
    if stats["disconnect_count_24h"] >= DISCONNECT_WARNING_THRESHOLD:
        logger.warning(
            f"🚨 HIGH DISCONNECTION RATE: {stats['disconnect_count_24h']} disconnects in 24h "
            f"(uptime: {uptime_percent:.1f}%)"
        )
        await send_to_discord_log(
            f"High disconnection rate: {stats['disconnect_count_24h']} disconnects in 24h "
            f"(uptime: {uptime_percent:.1f}%)",
            "WARNING"
        )


@bot.event
async def on_resumed():
    """
    Track reconnections with downtime metrics.
    
    Logs reconnection time and updates connection statistics. Quick reconnections
    (< 5s) are normal and indicate the auto-reconnect system is working properly.
    """
    now = time.time()
    stats = bot._connection_stats
    
    # Update connection start time for next uptime calculation
    stats["last_connect"] = now
    
    if stats["last_disconnect"]:
        duration = now - stats["last_disconnect"]
        
        if duration < 5:
            logger.info(f"✅ Bot reconnected ({duration:.2f}s downtime - auto-reconnect working)")
        elif duration < 60:
            logger.warning(f"⚠️ Bot reconnected after {duration:.1f}s downtime")
        else:
            logger.error(f"🚨 Bot reconnected after {duration:.1f}s - very long disconnection!")
            await send_to_discord_log(
                f"Long disconnection: {duration:.1f}s - may have interrupted operations",
                "ERROR"
            )
        
        stats["last_disconnect"] = None
    else:
        logger.info("✅ Bot reconnected")


# REMOVED: on_application_command_autocomplete event handler
# Having a custom handler prevents disnake from automatically routing to decorator-based autocomplete functions.
# Disnake handles autocomplete routing automatically when using @command.autocomplete() decorators.


@bot.event
async def on_error(event, *args, **kwargs):
    """Global error handler"""
    logger.error(f"Error in {event}", exc_info=True)


# ============ SHUTDOWN ============
_shutdown_in_progress = False

async def graceful_shutdown():
    """Clean shutdown with resource cleanup"""
    global _shutdown_in_progress
    
    if _shutdown_in_progress:
        return
    
    _shutdown_in_progress = True
    logger.info("Shutting down...")
    
    # Unload cogs — await async cleanup where available
    unload_tasks = []
    for cog_name in list(bot.cogs.keys()):
        try:
            cog = bot.get_cog(cog_name)
            if not cog:
                continue
            if hasattr(cog, "_async_unload"):
                if getattr(cog, "_unloaded", False):
                    continue
                unload_tasks.append(cog._async_unload())
                if hasattr(cog, "_unloaded"):
                    cog._unloaded = True
            elif hasattr(cog, "cog_unload"):
                cog.cog_unload()
        except Exception as e:
            logger.debug("Cog unload error for %s: %s", cog_name, e)

    if unload_tasks:
        await asyncio.gather(*unload_tasks, return_exceptions=True)
    else:
        await asyncio.sleep(0.8)
    
    # Disconnect voice clients
    for vc in list(bot.voice_clients):
        try:
            await asyncio.wait_for(vc.disconnect(force=True), timeout=3.0)
        except Exception:
            pass
    
    # Shutdown shared thread pool
    if hasattr(bot, "executor") and bot.executor:
        try:
            bot.executor.shutdown(wait=True)
        except Exception:
            pass
    
    # Close HTTP session
    try:
        await bot.http_mgr.close()
    except Exception:
        pass
    
    # Close bot
    try:
        await bot.close()
    except Exception:
        pass


def handle_signal(signum, frame):
    """Handle shutdown signals - schedules graceful shutdown"""
    logger.info(f"Received signal {signum} - shutting down")
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(graceful_shutdown())
    except RuntimeError:
        # No running loop - bot.run() will handle shutdown
        pass


# ============ COG LOADING ============
COG_EXTENSIONS = [
    "cogs.voice_processing_cog",
    "cogs.DALLE_cog",
    "cogs.SecretSanta_cog",
    "cogs.DistributeZip_cog",
]


def load_cogs() -> int:
    """Load all cogs and return count"""
    loaded = 0
    for cog in COG_EXTENSIONS:
        try:
            bot.load_extension(cog)
            logger.info(f"Loaded {cog}")
            loaded += 1
        except Exception as e:
            logger.error(f"Failed to load {cog}: {e}")
    
    return loaded


def reload_cogs() -> int:
    """Reload extensions after a crash so cog_load hooks and tasks restart."""
    loaded = 0
    for cog in COG_EXTENSIONS:
        try:
            if cog in bot.extensions:
                bot.reload_extension(cog)
                logger.info(f"Reloaded {cog}")
            else:
                bot.load_extension(cog)
                logger.info(f"Loaded {cog}")
            loaded += 1
        except Exception as e:
            logger.error(f"Failed to reload {cog}: {e}")
    return loaded


def prepare_bot_for_retry() -> None:
    """
    Reset asyncio/disnake runtime after bot.run() closes the event loop.

    bot.run() always closes self.loop on exit. Retrying bot.run() on the same
    bot instance without a fresh loop causes immediate 'Event loop is closed'.
    """
    global _shutdown_in_progress

    _shutdown_in_progress = False

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot.loop = loop

    from disnake.http import HTTPClient

    bot.http = HTTPClient(loop=loop)

    bot.http_mgr._session = None
    bot.http_mgr._session_lock = asyncio.Lock()

    bot.ready_once = False

    if discord_handler:
        discord_handler.bot = None
        discord_handler.sender_task = None
        discord_handler.message_queue = asyncio.Queue(maxsize=50)

    if bot.extensions:
        reload_cogs()
    logger.info(
        "Prepared fresh event loop for bot restart (loop_id=%s)",
        id(bot.loop),
    )


def _resolve_git_short_commit() -> str:
    """Best-effort commit id (entrypoint env, then .git/HEAD)."""
    short = os.getenv("GIT_COMMIT_SHORT")
    if short:
        return short
    full = os.getenv("GIT_COMMIT")
    if full:
        return full[:12]
    git_dir = Path(__file__).resolve().parent / ".git"
    head_file = git_dir / "HEAD"
    if not head_file.is_file():
        return "unknown"
    head = head_file.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref_path = git_dir / head[5:]
        if ref_path.is_file():
            return ref_path.read_text(encoding="utf-8").strip()[:12]
    return head[:12]


def _log_deploy_identity() -> None:
    """Log branch/commit and whether SS simplify layout is present."""
    commit = _resolve_git_short_commit()
    branch = os.getenv("GIT_BRANCH_ACTUAL") or os.getenv("GIT_BRANCH") or "unknown"
    root = Path(__file__).resolve().parent
    split_layout = (root / "cogs" / "secret_santa_core.py").is_file()
    layout = "split" if split_layout else "legacy-monolith"
    logger.info("Deploy identity: branch=%s commit=%s ss_layout=%s", branch, commit, layout)
    if not split_layout:
        logger.warning(
            "secret_santa_core.py missing — outdated code tree; check GIT_BRANCH / git pull."
        )


# ============ MAIN ============
if __name__ == "__main__":
    if "--tts-lab" in sys.argv:
        lab_args = [a for a in sys.argv[1:] if a != "--tts-lab"]
        if "--open-browser" not in lab_args:
            lab_args.append("--open-browser")
        lab_script = Path(__file__).resolve().parent / "start_tts_lab.py"
        if not lab_script.is_file():
            logger.critical(
                "TTS dev lab not found (%s). Merge PR #15 or checkout cursor/tts-dev-lab-6c6a.",
                lab_script,
            )
            sys.exit(1)
        import runpy

        print("Starting TTS dev lab (--tts-lab); Discord bot will not start.")
        sys.argv = [str(lab_script)] + lab_args
        runpy.run_path(str(lab_script), run_name="__main__")
        sys.exit(0)

    logger.info("Starting bot...")
    _log_deploy_identity()
    
    # Python version check - disnake 2.12+ (DAVE voice) requires Python 3.10+
    if sys.version_info < PYTHON_MIN_VERSION:
        logger.critical(
            f"Python {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+ required. "
            f"Current: {sys.version_info.major}.{sys.version_info.minor}"
        )
        sys.exit(1)
    
    # Ensure required directories exist for cogs (Secret Santa archives, etc.)
    REQUIRED_DIRS = ['cogs/archive', 'cogs/archive/backups', 'cogs/distributed_files']
    for dir_path in REQUIRED_DIRS:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    # Check critical file permissions before starting
    CRITICAL_FILES = ['main.py', 'cogs/SecretSanta_cog.py']
    for file_path in CRITICAL_FILES:
        if not os.access(file_path, os.R_OK):
            logger.critical(f"Cannot read {file_path} - check permissions")
            sys.exit(1)
    
    logger.info("Production checks passed")

    if not validate_runtime_dependencies(logger):
        sys.exit(1)
    
    # Validate API key (uses shared HttpManager for connection reuse)
    if not config.SKIP_API_VALIDATION:
        if not asyncio.run(validate_openai_key(config.OPENAI_API_KEY, logger, bot.http_mgr)):
            logger.critical("OpenAI API key is invalid. Fix config.env or set SKIP_API_VALIDATION=true")
            sys.exit(1)
        # Clear HTTP session - it was bound to asyncio.run's temporary loop; bot uses a different loop
        asyncio.run(bot.http_mgr.invalidate_session())
    else:
        logger.warning("API validation skipped")

    if config.SS_DEBUG_START:
        logger.warning(
            "SS_DEBUG_START is enabled — /ss start will skip archive-year warnings. "
            "Disable in production config.env."
        )
    
    # Load cogs
    num_loaded = load_cogs()
    if num_loaded == 0:
        logger.critical("No cogs loaded!")
        sys.exit(1)
    
    logger.info(f"Successfully loaded {num_loaded} cogs")
    logger.info("Bot runtime: crash-retry-v3 (bot.run + loop reset, no process exit on crash)")
    
    # Retry configuration for infinite retry with exponential backoff
    MAX_RETRY_WAIT = 60  # Maximum wait time between retries (seconds)
    RETRY_BACKOFF_MULTIPLIER = 5  # Seconds per retry attempt (up to max)
    RETRY_BACKOFF_CAP = 12  # Maximum retry count before capping wait time
    RETRY_RESET_THRESHOLD = 100  # Reset backoff after this many retries (prevents overflow)
    
    retry_count = 0
    shutdown_flag = [False]
    
    def shutdown_wrapper(signum, frame):
        """Wrapper to set shutdown flag when signal received"""
        shutdown_flag[0] = True
        handle_signal(signum, frame)
    
    # Register signal handlers for graceful shutdown (SIGINT = Ctrl+C, SIGTERM = termination)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown_wrapper)
    
    # Main bot loop with infinite retry on crashes (ensures 24/7/365 uptime)
    try:
        while not shutdown_flag[0]:
            if retry_count > 0 or bot.loop.is_closed():
                prepare_bot_for_retry()
            try:
                bot.run(config.DISCORD_TOKEN, reconnect=True)
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt - shutting down")
                shutdown_flag[0] = True
                break
            except Exception as e:
                retry_count += 1
                logger.critical(f"Bot crashed (attempt #{retry_count}): {e}", exc_info=True)
            else:
                if shutdown_flag[0]:
                    break
                retry_count += 1
                logger.critical(
                    "Bot session ended without shutdown signal (attempt #%s)",
                    retry_count,
                )

            if shutdown_flag[0]:
                break

            wait_time = min(MAX_RETRY_WAIT, RETRY_BACKOFF_MULTIPLIER * min(retry_count, RETRY_BACKOFF_CAP))
            logger.warning(f"Retrying in {wait_time}s... (will retry forever)")
            time.sleep(wait_time)

            if retry_count > RETRY_RESET_THRESHOLD:
                retry_count = 0
    finally:
        if shutdown_flag[0]:
            logger.info("Performing graceful shutdown...")
            try:
                asyncio.run(asyncio.wait_for(graceful_shutdown(), timeout=20.0))
            except asyncio.TimeoutError:
                logger.error("Graceful shutdown timed out after 20s")
            except Exception as e:
                logger.error(f"Cleanup failed: {e}")
            finally:
                os._exit(0)
