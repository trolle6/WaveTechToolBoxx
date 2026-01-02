"""
WaveTechToolBox - Discord Bot Main Entry Point

FEATURES:
- 🎤 Text-to-Speech (OpenAI TTS with voice rotation)
- 🎨 AI Image Generation (DALL-E 3 with queue management)
- 🎄 Secret Santa Event Management (with history tracking)

ARCHITECTURE:
- Config management with validation
- HTTP session pooling for API efficiency
- Discord logging handler (sends errors to Discord)
- Graceful shutdown with proper cleanup
- Automatic retry on connection failures

DEPENDENCIES:
- disnake: Discord API wrapper
- aiohttp: Async HTTP client
- python-dotenv: Environment variable management
- PyNaCl: Voice support
- psutil: System monitoring

CONFIGURATION:
- Edit config.env with your settings
- Required: DISCORD_TOKEN, OPENAI_API_KEY, channel IDs
- Optional: Performance tuning, rate limits, timeouts

USAGE:
- python main.py
- Bot will validate API keys and load all cogs
- Logs to bot.log and Discord log channel
- Ctrl+C for graceful shutdown
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
class Config:
    """Load config with validation and defaults"""
    _required = {
        "DISCORD_TOKEN": (str, None),
        "DISCORD_GUILD_ID": (int, None),
        "DISCORD_CHANNEL_ID": (int, None),
        "DISCORD_LOG_CHANNEL_ID": (int, None),
        "DISCORD_MODERATOR_ROLE_ID": (int, None),
        "OPENAI_API_KEY": (str, None),
    }
    _optional = {
        "DEBUG_MODE": (bool, False),
        "LOG_LEVEL": (str, "INFO"),
        "MAX_TTS_CACHE": (int, 50),
        "TTS_TIMEOUT": (int, 15),
        "SKIP_API_VALIDATION": (bool, False),
        "MAX_QUEUE_SIZE": (int, 50),
        "RATE_LIMIT_REQUESTS": (int, 15),
        "RATE_LIMIT_WINDOW": (int, 60),
        "VOICE_TIMEOUT": (int, 10),
        "AUTO_DISCONNECT_TIMEOUT": (int, 300),
        "TTS_ROLE_ID": (int, None),  # Role required for TTS (optional)
        "BOT_OWNER_USERNAME": (str, "trolle6"),  # Bot owner username for owner-only commands
    }

    def __init__(self):
        self.data = {}
        self._load()

    def _load(self):
        missing = []
        for key, (cast_type, _) in self._required.items():
            val = os.getenv(key)
            if not val:
                missing.append(key)
                continue
            # Clean string values (trim whitespace)
            if cast_type == str:
                self.data[key] = val.strip()
            elif cast_type == int:
                self.data[key] = int(val)
            else:
                self.data[key] = val

        if missing:
            print(f"Fatal: Missing env vars: {', '.join(missing)}")
            raise RuntimeError(f"Missing config: {missing}")

        for key, (cast_type, default) in self._optional.items():
            val = os.getenv(key)
            if val is None:
                self.data[key] = default
            elif cast_type == bool:
                self.data[key] = str(val).lower() == "true"
            elif cast_type == int:
                self.data[key] = int(val)
                # Validate integer ranges
                self._validate_int_config(key, self.data[key])
            else:
                self.data[key] = val

    def _validate_int_config(self, key: str, value: int):
        """Validate integer config values are within reasonable ranges"""
        validators = {
            "MAX_TTS_CACHE": (1, 1000),
            "TTS_TIMEOUT": (1, 60),
            "MAX_QUEUE_SIZE": (1, 500),
            "RATE_LIMIT_REQUESTS": (1, 100),
            "RATE_LIMIT_WINDOW": (1, 3600),
            "VOICE_TIMEOUT": (1, 30),
            "AUTO_DISCONNECT_TIMEOUT": (10, 3600),
        }
        
        if key in validators:
            min_val, max_val = validators[key]
            if not (min_val <= value <= max_val):
                print(f"Warning: {key}={value} is outside recommended range ({min_val}-{max_val})")

    def __getattr__(self, name: str):
        key = name.upper()
        if key in self.data:
            return self.data[key]
        raise AttributeError(f"Config missing: {key}")


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
            connector = aiohttp.TCPConnector(
                limit=10, 
                limit_per_host=5, 
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                force_close=True
            )
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                connector=connector,
                headers={'Connection': 'keep-alive'}
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            try:
                # Close all pending connections
                await self.session.close()
                # Wait for all connections to close
                await asyncio.sleep(0.5)
                # Force close any remaining connections
                if hasattr(self.session, '_connector') and self.session._connector:
                    await self.session._connector.close()
            except Exception as e:
                logging.getLogger("bot").debug(f"HTTP session close error: {e}")
            finally:
                self.session = None


# ============ DISCORD LOGGING ============
class DiscordLogHandler(logging.Handler):
    """
    Custom logging handler that sends messages to Discord channels.
    
    FEATURES:
    - Asynchronous message sending (non-blocking)
    - Rate limiting (prevents spam from duplicate messages)
    - Queue-based processing (handles bursts of logs)
    - Only sends WARNING and above to Discord
    """
    
    # Emoji mapping for log levels
    _EMOJI_MAP = {
        "WARNING": "⚠️",
        "ERROR": "❌",
        "CRITICAL": "🚨"
    }
    
    def __init__(self, bot=None, log_channel_id: int = None):
        super().__init__()
        self.bot = bot
        self.log_channel_id = log_channel_id
        self.message_queue = asyncio.Queue(maxsize=50)
        self.sender_task: Optional[asyncio.Task] = None
        self._last_message = {}  # Rate limiting
        
    def set_bot(self, bot):
        """Set bot instance after it's created"""
        self.bot = bot
        if bot and not self.sender_task:
            self.sender_task = asyncio.create_task(self._message_sender())
    
    def emit(self, record):
        """Queue log message for Discord sending"""
        if not self.bot or not self.log_channel_id:
            return
            
        try:
            # Rate limiting - don't spam same message
            msg_key = f"{record.levelname}:{record.getMessage()[:50]}"
            now = time.time()
            if msg_key in self._last_message and (now - self._last_message[msg_key]) < 60:
                return
            self._last_message[msg_key] = now
            
            # Only send WARNING and above to Discord
            if record.levelno < logging.WARNING:
                return
                
            # Format message for Discord
            emoji = self._EMOJI_MAP.get(record.levelname, "ℹ️")
            message = f"{emoji} **{record.levelname}** | {record.name}\n```\n{record.getMessage()}\n```"
            
            # Truncate if too long
            if len(message) > 1900:
                message = message[:1900] + "...\n```"
            
            # Queue for sending
            try:
                self.message_queue.put_nowait(message)
            except asyncio.QueueFull:
                pass  # Drop message if queue is full
                
        except Exception:
            pass  # Don't let logging errors crash the bot
    
    async def _message_sender(self):
        """Background task to send queued messages"""
        while True:
            try:
                message = await self.message_queue.get()
                
                if self.bot and self.log_channel_id:
                    try:
                        channel = self.bot.get_channel(self.log_channel_id)
                        if channel:
                            await channel.send(message)
                        await asyncio.sleep(1)  # Rate limit
                    except Exception:
                        pass  # Don't let Discord errors crash logging
                        
            except asyncio.CancelledError:
                break
            except Exception:
                continue
    
    def close(self):
        """Clean shutdown of Discord logging"""
        if self.sender_task:
            self.sender_task.cancel()
        super().close()


# ============ SETUP ============
def setup_logging(config: Config) -> tuple[logging.Logger, DiscordLogHandler]:
    logger = logging.getLogger("bot")
    logger.setLevel(config.LOG_LEVEL)

    # Prevent duplicate handlers
    if logger.handlers:
        # Find existing Discord handler if any
        discord_handler = None
        for handler in logger.handlers:
            if isinstance(handler, DiscordLogHandler):
                discord_handler = handler
                break
        return logger, discord_handler

    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler with rotation
    fh = logging.handlers.RotatingFileHandler(
        "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Discord handler (will be connected to bot later)
    discord_handler = DiscordLogHandler(log_channel_id=config.DISCORD_LOG_CHANNEL_ID)
    discord_handler.setLevel(logging.WARNING)  # Only warnings and above to Discord
    logger.addHandler(discord_handler)

    return logger, discord_handler


async def validate_openai_key(key: str, logger: logging.Logger) -> bool:
    """Test if OpenAI API key is valid before loading cogs"""
    # Clean the key (remove whitespace)
    key = key.strip() if key else ""
    
    logger.debug("Validating OpenAI API key...")

    if not key:
        logger.error("OPENAI_API_KEY is empty or not set in config.env")
        return False

    if not key.startswith("sk-"):
        logger.error("Invalid API key format (should start with 'sk-')")
        logger.error("Please check your config.env file for correct OPENAI_API_KEY")
        return False

    headers = {"Authorization": f"Bearer {key}"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    "https://api.openai.com/v1/models",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    logger.info("OpenAI API key is valid")
                    return True
                elif r.status == 401:
                    logger.error("API key is invalid or expired")
                    logger.error("Please verify your OPENAI_API_KEY in config.env")
                    error_body = await r.text()
                    logger.debug(f"API error response: {error_body}")
                    logger.debug("Tip: API key should start with 'sk-' and be from https://platform.openai.com/api-keys")
                    return False
                else:
                    logger.warning(f"Unexpected API response: {r.status}")
                    error_body = await r.text()
                    logger.debug(f"API response: {error_body}")
                    # Allow bot to start on unexpected responses
                    return True
    except asyncio.TimeoutError:
        logger.warning("API key validation timeout (API might be slow) - allowing bot to start")
        return True
    except Exception as e:
        logger.warning(f"Could not validate API key: {e} - allowing bot to start")
        return True


# Load config early
try:
    config = Config()
except RuntimeError as e:
    print(f"Fatal: {e}")
    sys.exit(1)

logger, discord_handler = setup_logging(config)

# Bot setup
intents = disnake.Intents.all()
bot = commands.InteractionBot(intents=intents)
bot.config = config
bot.logger = logger
bot.http_mgr = HttpManager()
bot.discord_handler = discord_handler
bot.ready_once = False

# Connection tracking for monitoring disconnections
bot._connection_stats = {
    "disconnects": [],
    "last_disconnect": None,
    "disconnect_count_24h": 0
}


# Discord feedback utilities
# Level emoji mapping (shared by all Discord message functions)
_LEVEL_EMOJIS = {
    "INFO": "ℹ️",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "CRITICAL": "🚨",
    "SUCCESS": "✅"
}

async def _send_discord_message(channel_id: int, message: str, level: str = "INFO", include_level_text: bool = True):
    """
    Internal helper for sending Discord messages with consistent formatting.
    Reduces code duplication between log and regular channel messages.
    """
    if not bot.ready_once:
        return
        
    try:
        channel = bot.get_channel(channel_id)
        if not channel:
            return
            
        emoji = _LEVEL_EMOJIS.get(level, "ℹ️")
        
        # Format message based on whether we want level text
        if include_level_text:
            formatted_message = f"{emoji} **{level}** | {message}"
        else:
            formatted_message = f"{emoji} {message}"
        
        # Truncate if too long (Discord limit is 2000 chars)
        if len(formatted_message) > 2000:
            formatted_message = formatted_message[:1997] + "..."
            
        await channel.send(formatted_message)
    except Exception as e:
        logger.debug(f"Failed to send Discord message: {e}")


async def send_to_discord_log(message: str, level: str = "INFO"):
    """Send a message to the Discord log channel"""
    await _send_discord_message(config.DISCORD_LOG_CHANNEL_ID, message, level, include_level_text=True)


async def send_to_discord_channel(message: str, level: str = "INFO"):
    """Send a message to the default Discord channel"""
    await _send_discord_message(config.DISCORD_CHANNEL_ID, message, level, include_level_text=False)


# Add utility methods to bot for cogs to use
bot.send_to_discord_log = send_to_discord_log
bot.send_to_discord_channel = send_to_discord_channel

def is_bot_connected() -> bool:
    """Check if bot is currently connected to Discord"""
    return bot.is_ready()


@bot.event
async def on_ready():
    if not bot.ready_once:
        logger.info(f"Logged in as {bot.user}")
        
        # Connect Discord logging handler
        if discord_handler:
            discord_handler.set_bot(bot)
            logger.info("Discord logging handler connected")
        
        # Send startup notification to Discord
        try:
            log_channel = bot.get_channel(config.DISCORD_LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"🤖 **Bot Online** | {bot.user.name} is ready!")
        except Exception as e:
            logger.debug(f"Could not send startup message: {e}")
        
        bot.ready_once = True


@bot.event
async def on_disconnect():
    """
    Track disconnections and log with details.
    
    MONITORING:
    - Tracks all disconnections with timestamps
    - Calculates frequency and duration
    - Warns if disconnections are too frequent (>10 in 24h)
    - Logs time since last disconnect to detect patterns
    
    CONCERNS:
    - Frequent disconnections may interrupt user operations
    - Could indicate network instability or Discord API issues
    - May cause data loss if operations are in progress
    """
    disconnect_time = time.time()
    
    # Track disconnection
    bot._connection_stats["last_disconnect"] = disconnect_time
    bot._connection_stats["disconnects"].append(disconnect_time)
    
    # Keep only last 24 hours of disconnects
    cutoff = disconnect_time - 86400  # 24 hours
    bot._connection_stats["disconnects"] = [d for d in bot._connection_stats["disconnects"] if d > cutoff]
    bot._connection_stats["disconnect_count_24h"] = len(bot._connection_stats["disconnects"])
    
    # Calculate time since last disconnect (if any)
    if len(bot._connection_stats["disconnects"]) > 1:
        time_since_last = disconnect_time - bot._connection_stats["disconnects"][-2]
        logger.info(f"⚠️ Bot disconnected from Discord (disconnect #{bot._connection_stats['disconnect_count_24h']} in last 24h, {time_since_last:.1f}s since last)")
    else:
        logger.info(f"⚠️ Bot disconnected from Discord (disconnect #{bot._connection_stats['disconnect_count_24h']} in last 24h)")
    
    # Warn if disconnections are too frequent
    if bot._connection_stats["disconnect_count_24h"] >= 10:
        logger.warning(f"🚨 HIGH DISCONNECTION RATE: {bot._connection_stats['disconnect_count_24h']} disconnects in last 24 hours - possible network or Discord API issues")
        # Send to Discord log channel if available
        try:
            await send_to_discord_log(
                f"High disconnection rate detected: {bot._connection_stats['disconnect_count_24h']} disconnects in 24h. "
                "This may indicate network issues or Discord API problems.",
                "WARNING"
            )
        except Exception:
            pass  # Don't fail if Discord is down


@bot.event
async def on_resumed():
    """
    Track reconnections and log with duration.
    
    MONITORING:
    - Calculates disconnection duration
    - Warns if disconnection was longer than expected (>5s)
    - Alerts if disconnection was very long (>60s) - may have interrupted operations
    
    IMPACT:
    - Short disconnections (<5s): Usually harmless, Discord gateway resets
    - Medium disconnections (5-60s): May interrupt some operations
    - Long disconnections (>60s): Likely interrupted user operations (TTS, DALL-E, Secret Santa)
    """
    reconnect_time = time.time()
    
    if bot._connection_stats["last_disconnect"]:
        disconnect_duration = reconnect_time - bot._connection_stats["last_disconnect"]
        
        # Log reconnection with duration
        if disconnect_duration < 5:
            logger.info(f"✅ Bot reconnected to Discord (disconnected for {disconnect_duration:.2f}s)")
        elif disconnect_duration < 60:
            logger.warning(f"⚠️ Bot reconnected to Discord after {disconnect_duration:.1f}s - longer than expected")
        else:
            logger.error(f"🚨 Bot reconnected to Discord after {disconnect_duration:.1f}s - very long disconnection!")
            # Send to Discord log channel
            try:
                await send_to_discord_log(
                    f"Long disconnection detected: {disconnect_duration:.1f}s. "
                    "This may have interrupted user operations.",
                    "ERROR"
                )
            except Exception:
                pass
        
        bot._connection_stats["last_disconnect"] = None
    else:
        logger.info("✅ Bot reconnected to Discord")


@bot.event
async def on_error(event, *args, **kwargs):
    logger.error(f"Error in {event}", exc_info=True)


_shutdown_in_progress = False

async def graceful_shutdown():
    """
    Clean shutdown ensuring all resources are released.
    Called on SIGINT/SIGTERM or when bot crashes.
    Prevents resource leaks and ensures data is saved.
    """
    global _shutdown_in_progress
    
    # Prevent multiple simultaneous shutdown calls
    if _shutdown_in_progress:
        logger.debug("Shutdown already in progress, skipping")
        return
    
    _shutdown_in_progress = True
    logger.info("Shutting down...")

    # Unload all cogs properly (they save state and cleanup resources)
    # IMPORTANT: Create snapshot to avoid dict modification during iteration
    cog_names = list(bot.cogs.keys())
    for cog_name in cog_names:
        try:
            cog = bot.get_cog(cog_name)
            if cog and hasattr(cog, 'cog_unload'):
                # cog_unload is synchronous but schedules async cleanup
                cog.cog_unload()
                logger.debug(f"Cog unloaded: {cog_name}")
        except Exception as e:
            logger.debug(f"Cog unload error for {cog_name}: {e}")

    # Wait for any scheduled async cleanup tasks to complete
    await asyncio.sleep(0.8)

    # Disconnect all voice clients
    for vc in list(bot.voice_clients):
        try:
            await asyncio.wait_for(vc.disconnect(force=True), timeout=3.0)
        except Exception as e:
            logger.debug(f"Voice disconnect error: {e}")

    # Close HTTP session
    try:
        await bot.http_mgr.close()
    except Exception as e:
        logger.debug(f"HTTP session close error: {e}")

    # Close bot
    try:
        await bot.close()
    except Exception as e:
        logger.debug(f"Bot close error: {e}")


def load_cogs() -> int:
    """Load cogs and return count of successfully loaded cogs"""
    cogs = [
        "cogs.voice_processing_cog",
        "cogs.DALLE_cog",
        "cogs.SecretSanta_cog",
        "cogs.CustomEvents_cog",  # New modular event system!
        "cogs.DistributeZip_cog",  # Zip file distribution (e.g., Minecraft texture packs)
        "cogs.LongText_cog"  # Long text handler (files and multi-part messages)
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


# Graceful shutdown on signals
def handle_signal(signum, frame):
    """Handle shutdown signals (SIGINT, SIGTERM)"""
    logger.info(f"Received signal {signum} - initiating graceful shutdown")
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(graceful_shutdown())
        else:
            asyncio.run(graceful_shutdown())
    except RuntimeError:
        # No event loop available
        asyncio.run(graceful_shutdown())


# Signal handlers will be set in main block after bot initialization
# (moved to avoid conflicts with retry loop)

if __name__ == "__main__":
    logger.info("Starting bot...")

    # Production deployment checks
    logger.info("Running production deployment checks...")
    
    # Check Python version
    if sys.version_info < (3, 9):
        logger.critical("Python 3.9+ required. Current: {}.{}.{}".format(*sys.version_info[:3]))
        sys.exit(1)
    
    # Check required directories
    from pathlib import Path
    required_dirs = ['cogs/archive', 'cogs/archive/backups']
    for dir_path in required_dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        logger.info(f"Ensured directory exists: {dir_path}")
    
    # Check file permissions
    critical_files = ['main.py', 'cogs/SecretSanta_cog.py']
    for file_path in critical_files:
        if not os.access(file_path, os.R_OK):
            logger.critical(f"Cannot read {file_path} - check permissions")
            sys.exit(1)
    
    logger.info("Production deployment checks passed")

    # Validate API key before loading cogs (unless skipped)
    if config.SKIP_API_VALIDATION:
        logger.warning("API key validation skipped (SKIP_API_VALIDATION=true)")
    else:
        api_key_valid = asyncio.run(validate_openai_key(config.OPENAI_API_KEY, logger))
        if not api_key_valid:
            logger.critical("OpenAI API key is invalid. Fix your config.env")
            logger.info("Tip: Set SKIP_API_VALIDATION=true in config.env to skip this check")
            sys.exit(1)

    # Load cogs once and store result
    num_loaded = load_cogs()
    if num_loaded == 0:
        logger.critical("No cogs loaded!")
        sys.exit(1)

    logger.info(f"Successfully loaded {num_loaded} cogs")

    # Run bot with INFINITE retry on failures (24/7/365 operation)
    # Bot will NEVER give up - only stops on KeyboardInterrupt (Ctrl+C) or signal
    retry_count = 0
    shutdown_flag = [False]  # Use list to allow modification in nested function
    
    def handle_shutdown_signal_wrapper(signum, frame):
        """Handle shutdown signals gracefully - sets flag and calls original handler"""
        shutdown_flag[0] = True
        logger.info(f"Shutdown signal {signum} received - will shutdown after current operation")
        handle_signal(signum, frame)
    
    # Override signal handlers to set shutdown flag
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_shutdown_signal_wrapper)
    
    try:
        while not shutdown_flag[0]:
            try:
                bot.run(config.DISCORD_TOKEN, reconnect=True)
                # If bot.run() returns, it was a normal shutdown (KeyboardInterrupt)
                break
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received - shutting down gracefully")
                shutdown_flag[0] = True
                break
            except Exception as e:
                retry_count += 1
                logger.critical(f"Bot crashed (attempt #{retry_count}): {e}", exc_info=True)
                
                # Exponential backoff with cap (prevents spam but ensures reconnection)
                # Max wait: 60 seconds (reasonable for network issues)
                wait_time = min(60, 5 * min(retry_count, 12))  # Cap at 12 retries for backoff calc
                logger.warning(f"Bot will retry in {wait_time} seconds... (retry #{retry_count}, will retry FOREVER until manually stopped)")
                time.sleep(wait_time)
                
                # Reset retry count after many attempts (prevents infinite backoff)
                # This ensures if bot was down for hours, it doesn't wait 60s forever
                if retry_count > 100:
                    logger.info("Resetting retry backoff after many attempts")
                    retry_count = 0
    finally:
        # Only cleanup if shutdown was requested (not on crash)
        if shutdown_flag[0]:
            logger.info("Performing graceful shutdown...")
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(graceful_shutdown())
                else:
                    asyncio.run(graceful_shutdown())
            except RuntimeError:
                asyncio.run(graceful_shutdown())
            except Exception as cleanup_error:
                logger.error(f"Cleanup failed: {cleanup_error}")
            finally:
                import os
                os._exit(0)  # Force exit to prevent hanging
        else:
            # If we get here, it's a crash - don't exit, let the loop retry
            logger.warning("Bot crashed - will retry automatically (infinite retries enabled)")
