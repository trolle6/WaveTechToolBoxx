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
import logging
import logging.handlers
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp
import disnake
from disnake.ext import commands
from dotenv import load_dotenv

load_dotenv("config.env", override=True)


# ============ CONFIG ============
# Configuration constants for clarity and maintainability
REQUIRED_CONFIG_KEYS = {
    "DISCORD_TOKEN", "DISCORD_GUILD_ID", "DISCORD_CHANNEL_ID",
    "DISCORD_LOG_CHANNEL_ID", "DISCORD_MODERATOR_ROLE_ID", "OPENAI_API_KEY"
}

CONFIG_DEFAULTS = {
    "DEBUG_MODE": False,
    "LOG_LEVEL": "INFO",
    "MAX_TTS_CACHE": 50,
    "TTS_TIMEOUT": 15,
    "SKIP_API_VALIDATION": False,
    "MAX_QUEUE_SIZE": 50,
    "RATE_LIMIT_REQUESTS": 15,
    "RATE_LIMIT_WINDOW": 60,
    "VOICE_TIMEOUT": 10,
    "AUTO_DISCONNECT_TIMEOUT": 300,
    "TTS_ROLE_ID": None,
    "BOT_OWNER_USERNAME": "trolle6",
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
                    # Logger not available yet, use print for early config errors
                    print(f"Warning: Invalid integer for {key}, using default {default}")
                    self.data[key] = default
            else:
                self.data[key] = val
    
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
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def get_session(self, timeout: int = HTTP_DEFAULT_TIMEOUT) -> aiohttp.ClientSession:
        """
        Get or create HTTP session with connection pooling.
        
        Note: Session timeout is set at creation, but individual requests
        can override with request-level timeout (see DALL-E and TTS code).
        
        Args:
            timeout: Request timeout in seconds (default: HTTP_DEFAULT_TIMEOUT)
        
        Returns:
            Configured aiohttp ClientSession
        """
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=HTTP_CONNECTION_LIMIT,
                limit_per_host=HTTP_CONNECTION_LIMIT_PER_HOST,
                ttl_dns_cache=HTTP_DNS_CACHE_TTL,
                enable_cleanup_closed=True,
                force_close=False  # Keep connections alive for reuse
            )
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                connector=connector,
                headers={'Connection': 'keep-alive'}
            )
        return self._session
    
    async def close(self):
        """Cleanly close HTTP session and connection pool"""
        if self._session and not self._session.closed:
            try:
                await self._session.close()
                await asyncio.sleep(0.5)  # Allow pending requests to finish
                if hasattr(self._session, '_connector') and self._session._connector:
                    await self._session._connector.close()
            except Exception:
                pass
            finally:
                self._session = None


# ============ DISCORD LOGGING ============
class DiscordLogHandler(logging.Handler):
    """Send log messages to Discord channel"""
    
    EMOJI_MAP = {"WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}
    
    def __init__(self, log_channel_id: int):
        super().__init__()
        self.log_channel_id = log_channel_id
        self.bot: Optional[disnake.Client] = None
        self.message_queue = asyncio.Queue(maxsize=50)
        self.sender_task: Optional[asyncio.Task] = None
        self._last_message = {}  # Rate limiting
    
    def set_bot(self, bot: disnake.Client):
        """Set bot instance and start sender task"""
        self.bot = bot
        if not self.sender_task:
            self.sender_task = asyncio.create_task(self._sender_loop())
    
    def emit(self, record: logging.LogRecord):
        """Queue log message for Discord"""
        if not self.bot or not self.log_channel_id or record.levelno < logging.WARNING:
            return
        
        # Rate limit duplicate messages
        msg_key = f"{record.levelname}:{record.getMessage()[:50]}"
        now = time.time()
        if msg_key in self._last_message and (now - self._last_message[msg_key]) < 60:
            return
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


# ============ SETUP ============
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
    
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
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


async def validate_openai_key(key: str, logger: logging.Logger) -> bool:
    """
    Validate OpenAI API key format and connectivity.
    
    Checks key format (must start with 'sk-') and makes a test API call.
    Allows bot to start even on network errors (may be transient).
    
    Args:
        key: OpenAI API key to validate
        logger: Logger instance for validation messages
    
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
        async with aiohttp.ClientSession() as session:
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
# Bot initialization constants
PYTHON_MIN_VERSION = (3, 9)  # Minimum required Python version
LOG_FILE_MAX_BYTES = 5_000_000  # 5MB - max log file size before rotation
LOG_FILE_BACKUP_COUNT = 5  # Number of rotated log files to keep
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
    except Exception:
        pass

async def send_to_discord_log(message: str, level: str = "INFO"):
    """Send message to Discord log channel"""
    await send_discord_message(config.DISCORD_LOG_CHANNEL_ID, message, level, include_level=True)

async def send_to_discord_channel(message: str, level: str = "INFO"):
    """Send message to default Discord channel"""
    await send_discord_message(config.DISCORD_CHANNEL_ID, message, level, include_level=False)

bot.send_to_discord_log = send_to_discord_log
bot.send_to_discord_channel = send_to_discord_channel


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
        
        try:
            channel = bot.get_channel(config.DISCORD_LOG_CHANNEL_ID)
            if channel:
                await channel.send(f"🤖 **Bot Online** | {bot.user.name} is ready!")
        except Exception:
            pass
        
        bot.ready_once = True
    else:
        # Reconnection - update connection start
        stats["last_connect"] = now
        if stats["last_disconnect"]:
            # Calculate uptime since last disconnect
            downtime = now - stats["last_disconnect"]
            if downtime > 60:
                logger.info(f"📊 Connection restored after {downtime:.1f}s downtime")


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
        
        # Track longest uptime
        # Note: This grows indefinitely, but it's just one float (8 bytes) so it's fine even over millions of years
        # The value itself doesn't affect calculations, it's just for logging/monitoring
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
        try:
            await send_to_discord_log(
                f"High disconnection rate: {stats['disconnect_count_24h']} disconnects in 24h "
                f"(uptime: {uptime_percent:.1f}%)",
                "WARNING"
            )
        except Exception:
            pass


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
            try:
                await send_to_discord_log(
                    f"Long disconnection: {duration:.1f}s - may have interrupted operations",
                    "ERROR"
                )
            except Exception:
                pass
        
        stats["last_disconnect"] = None
    else:
        logger.info("✅ Bot reconnected")


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
    
    # Unload cogs
    for cog_name in list(bot.cogs.keys()):
        try:
            cog = bot.get_cog(cog_name)
            if cog and hasattr(cog, 'cog_unload'):
                cog.cog_unload()
        except Exception:
            pass
    
    await asyncio.sleep(0.8)
    
    # Disconnect voice clients
    for vc in list(bot.voice_clients):
        try:
            await asyncio.wait_for(vc.disconnect(force=True), timeout=3.0)
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
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(graceful_shutdown())
        else:
            asyncio.run(graceful_shutdown())
    except RuntimeError:
        # Fallback if event loop is in invalid state
        asyncio.run(graceful_shutdown())


# ============ COG LOADING ============
def load_cogs() -> int:
    """Load all cogs and return count"""
    cogs = [
        "cogs.voice_processing_cog",
        "cogs.DALLE_cog",
        "cogs.SecretSanta_cog",
        "cogs.CustomEvents_cog",
        "cogs.DistributeZip_cog"
    ]
    
    loaded = 0
    for cog in cogs:
        try:
            bot.load_extension(cog)
            logger.info(f"Loaded {cog}")
            loaded += 1
        except Exception as e:
            logger.error(f"Failed to load {cog}: {e}")
    
    return loaded


# ============ MAIN ============
if __name__ == "__main__":
    logger.info("Starting bot...")
    
    # Python version check - disnake requires 3.9+
    if sys.version_info < PYTHON_MIN_VERSION:
        logger.critical(
            f"Python {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+ required. "
            f"Current: {sys.version_info.major}.{sys.version_info.minor}"
        )
        sys.exit(1)
    
    # Ensure required directories exist for cogs (Secret Santa archives, etc.)
    REQUIRED_DIRS = ['cogs/archive', 'cogs/archive/backups']
    for dir_path in REQUIRED_DIRS:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    # Check critical file permissions before starting
    CRITICAL_FILES = ['main.py', 'cogs/SecretSanta_cog.py']
    for file_path in CRITICAL_FILES:
        if not os.access(file_path, os.R_OK):
            logger.critical(f"Cannot read {file_path} - check permissions")
            sys.exit(1)
    
    logger.info("Production checks passed")
    
    # Validate API key
    if not config.SKIP_API_VALIDATION:
        if not asyncio.run(validate_openai_key(config.OPENAI_API_KEY, logger)):
            logger.critical("OpenAI API key is invalid. Fix config.env or set SKIP_API_VALIDATION=true")
            sys.exit(1)
    else:
        logger.warning("API validation skipped")
    
    # Load cogs
    num_loaded = load_cogs()
    if num_loaded == 0:
        logger.critical("No cogs loaded!")
        sys.exit(1)
    
    logger.info(f"Successfully loaded {num_loaded} cogs")
    
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
            try:
                bot.run(config.DISCORD_TOKEN, reconnect=True)
                break  # Normal shutdown (signal received)
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt - shutting down")
                shutdown_flag[0] = True
                break
            except Exception as e:
                retry_count += 1
                logger.critical(f"Bot crashed (attempt #{retry_count}): {e}", exc_info=True)
                
                # Exponential backoff: 5s, 10s, 15s... up to 60s max
                wait_time = min(MAX_RETRY_WAIT, RETRY_BACKOFF_MULTIPLIER * min(retry_count, RETRY_BACKOFF_CAP))
                logger.warning(f"Retrying in {wait_time}s... (will retry forever)")
                time.sleep(wait_time)
                
                # Reset counter periodically to prevent integer overflow on long-running systems
                if retry_count > RETRY_RESET_THRESHOLD:
                    retry_count = 0
    finally:
        if shutdown_flag[0]:
            logger.info("Performing graceful shutdown...")
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(graceful_shutdown())
                else:
                    asyncio.run(graceful_shutdown())
            except Exception as e:
                logger.error(f"Cleanup failed: {e}")
            finally:
                os._exit(0)
        else:
            logger.warning("Bot crashed - will retry automatically")
