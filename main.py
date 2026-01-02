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
from typing import Optional

import aiohttp
import disnake
from disnake.ext import commands
from dotenv import load_dotenv

load_dotenv("config.env", override=True)


# ============ CONFIG ============
class Config:
    """Configuration loader with validation"""
    
    REQUIRED = {
        "DISCORD_TOKEN", "DISCORD_GUILD_ID", "DISCORD_CHANNEL_ID",
        "DISCORD_LOG_CHANNEL_ID", "DISCORD_MODERATOR_ROLE_ID", "OPENAI_API_KEY"
    }
    
    DEFAULTS = {
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
    
    def __init__(self):
        self.data = {}
        missing = []
        
        # Load required vars
        for key in self.REQUIRED:
            val = os.getenv(key)
            if not val:
                missing.append(key)
                continue
            self.data[key] = val.strip() if isinstance(val, str) else val
        
        if missing:
            raise RuntimeError(f"Missing required config: {', '.join(missing)}")
        
        # Load optional vars with defaults
        for key, default in self.DEFAULTS.items():
            val = os.getenv(key)
            if val is None:
                self.data[key] = default
            elif isinstance(default, bool):
                self.data[key] = str(val).lower() == "true"
            elif isinstance(default, int):
                self.data[key] = int(val)
            else:
                self.data[key] = val
    
    def __getattr__(self, name: str):
        return self.data.get(name.upper(), self.DEFAULTS.get(name.upper()))


# ============ HTTP MANAGER ============
class HttpManager:
    """Singleton HTTP session manager"""
    _instance = None
    _session: Optional[aiohttp.ClientSession] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def get_session(self, timeout: int = 30) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=10, limit_per_host=5, ttl_dns_cache=300,
                enable_cleanup_closed=True, force_close=True
            )
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                connector=connector,
                headers={'Connection': 'keep-alive'}
            )
        return self._session
    
    async def close(self):
        """Close HTTP session"""
        if self._session and not self._session.closed:
            try:
                await self._session.close()
                await asyncio.sleep(0.5)
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
    
    # File handler
    fh = logging.handlers.RotatingFileHandler(
        "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
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


async def validate_openai_key(key: str, logger: logging.Logger) -> bool:
    """Validate OpenAI API key"""
    key = key.strip() if key else ""
    
    if not key:
        logger.error("OPENAI_API_KEY is empty")
        return False
    
    if not key.startswith("sk-"):
        logger.error("Invalid API key format (should start with 'sk-')")
        return False
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    logger.info("OpenAI API key is valid")
                    return True
                elif r.status == 401:
                    logger.error("API key is invalid or expired")
                    return False
                else:
                    logger.warning(f"Unexpected API response: {r.status}")
                    return True  # Allow start on unexpected responses
    except asyncio.TimeoutError:
        logger.warning("API validation timeout - allowing bot to start")
        return True
    except Exception as e:
        logger.warning(f"API validation error: {e} - allowing bot to start")
        return True


# ============ BOT SETUP ============
try:
    config = Config()
except RuntimeError as e:
    print(f"Fatal: {e}")
    sys.exit(1)

logger, discord_handler = setup_logging(config)

# Initialize bot
intents = disnake.Intents.all()
bot = commands.InteractionBot(intents=intents)
bot.config = config
bot.logger = logger
bot.http_mgr = HttpManager()
bot.discord_handler = discord_handler
bot.ready_once = False

# Connection tracking
bot._connection_stats = {
    "disconnects": [],
    "last_disconnect": None,
    "disconnect_count_24h": 0
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
    """Bot ready event"""
    if not bot.ready_once:
        logger.info(f"Logged in as {bot.user}")
        
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


@bot.event
async def on_disconnect():
    """Track disconnections"""
    now = time.time()
    stats = bot._connection_stats
    
    stats["last_disconnect"] = now
    stats["disconnects"].append(now)
    
    # Keep only last 24 hours
    cutoff = now - 86400
    stats["disconnects"] = [d for d in stats["disconnects"] if d > cutoff]
    stats["disconnect_count_24h"] = len(stats["disconnects"])
    
    # Log disconnect
    if len(stats["disconnects"]) > 1:
        time_since = now - stats["disconnects"][-2]
        logger.info(f"⚠️ Bot disconnected (#{stats['disconnect_count_24h']} in 24h, {time_since:.1f}s since last)")
    else:
        logger.info(f"⚠️ Bot disconnected (#{stats['disconnect_count_24h']} in 24h)")
    
    # Warn if too frequent
    if stats["disconnect_count_24h"] >= 10:
        logger.warning(f"🚨 HIGH DISCONNECTION RATE: {stats['disconnect_count_24h']} disconnects in 24h")
        try:
            await send_to_discord_log(
                f"High disconnection rate: {stats['disconnect_count_24h']} disconnects in 24h",
                "WARNING"
            )
        except Exception:
            pass


@bot.event
async def on_resumed():
    """Track reconnections"""
    now = time.time()
    stats = bot._connection_stats
    
    if stats["last_disconnect"]:
        duration = now - stats["last_disconnect"]
        
        if duration < 5:
            logger.info(f"✅ Bot reconnected ({duration:.2f}s)")
        elif duration < 60:
            logger.warning(f"⚠️ Bot reconnected after {duration:.1f}s")
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
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum} - shutting down")
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(graceful_shutdown())
        else:
            asyncio.run(graceful_shutdown())
    except RuntimeError:
        asyncio.run(graceful_shutdown())


# ============ COG LOADING ============
def load_cogs() -> int:
    """Load all cogs and return count"""
    cogs = [
        "cogs.voice_processing_cog",
        "cogs.DALLE_cog",
        "cogs.SecretSanta_cog",
        "cogs.CustomEvents_cog",
        "cogs.DistributeZip_cog",
        "cogs.LongText_cog"
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
    
    # Python version check
    if sys.version_info < (3, 9):
        logger.critical(f"Python 3.9+ required. Current: {sys.version_info.major}.{sys.version_info.minor}")
        sys.exit(1)
    
    # Ensure directories exist
    for dir_path in ['cogs/archive', 'cogs/archive/backups']:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    # Check file permissions
    for file_path in ['main.py', 'cogs/SecretSanta_cog.py']:
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
    
    # Setup signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_signal)
    
    # Run bot with infinite retry
    retry_count = 0
    shutdown_flag = [False]
    
    def shutdown_wrapper(signum, frame):
        shutdown_flag[0] = True
        handle_signal(signum, frame)
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown_wrapper)
    
    try:
        while not shutdown_flag[0]:
            try:
                bot.run(config.DISCORD_TOKEN, reconnect=True)
                break
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt - shutting down")
                shutdown_flag[0] = True
                break
            except Exception as e:
                retry_count += 1
                logger.critical(f"Bot crashed (attempt #{retry_count}): {e}", exc_info=True)
                
                wait_time = min(60, 5 * min(retry_count, 12))
                logger.warning(f"Retrying in {wait_time}s... (will retry forever)")
                time.sleep(wait_time)
                
                if retry_count > 100:
                    retry_count = 0  # Reset backoff
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
