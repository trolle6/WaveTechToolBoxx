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
            val = os.getenv(key, default)
            if cast_type == bool:
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
    """Custom logging handler that sends messages to Discord channels"""
    
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
            emoji = {"WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}.get(record.levelname, "ℹ️")
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


# Discord feedback utilities
async def send_to_discord_log(message: str, level: str = "INFO"):
    """Send a message to the Discord log channel"""
    if not bot.ready_once:
        return
        
    try:
        log_channel = bot.get_channel(config.DISCORD_LOG_CHANNEL_ID)
        if not log_channel:
            return
            
        emoji = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨", "SUCCESS": "✅"}.get(level, "ℹ️")
        formatted_message = f"{emoji} **{level}** | {message}"
        
        # Truncate if too long
        if len(formatted_message) > 2000:
            formatted_message = formatted_message[:1997] + "..."
            
        await log_channel.send(formatted_message)
    except Exception as e:
        logger.debug(f"Failed to send Discord log message: {e}")


async def send_to_discord_channel(message: str, level: str = "INFO"):
    """Send a message to the default Discord channel"""
    if not bot.ready_once:
        return
        
    try:
        channel = bot.get_channel(config.DISCORD_CHANNEL_ID)
        if not channel:
            return
            
        emoji = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨", "SUCCESS": "✅"}.get(level, "ℹ️")
        formatted_message = f"{emoji} {message}"
        
        # Truncate if too long
        if len(formatted_message) > 2000:
            formatted_message = formatted_message[:1997] + "..."
            
        await channel.send(formatted_message)
    except Exception as e:
        logger.debug(f"Failed to send Discord channel message: {e}")


# Add utility methods to bot for cogs to use
bot.send_to_discord_log = send_to_discord_log
bot.send_to_discord_channel = send_to_discord_channel


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
    logger.warning("Bot disconnected from Discord")


@bot.event
async def on_resumed():
    logger.info("Bot reconnected to Discord")


@bot.event
async def on_error(event, *args, **kwargs):
    logger.error(f"Error in {event}", exc_info=True)


async def graceful_shutdown():
    """Clean shutdown ensuring all resources are released"""
    logger.info("Shutting down...")

    # Unload all cogs properly
    for cog_name in list(bot.cogs.keys()):
        try:
            cog = bot.get_cog(cog_name)
            if cog and hasattr(cog, 'cog_unload'):
                # cog_unload is now synchronous but schedules async cleanup
                cog.cog_unload()
            # Remove the cog after we've unloaded it to prevent disnake from calling cog_unload again
            bot.remove_cog(cog_name)
        except Exception as e:
            logger.debug(f"Cog unload error for {cog_name}: {e}")

    # Wait a moment for any scheduled async cleanup tasks to start
    await asyncio.sleep(0.5)

    # Clear the cogs dictionary to prevent disnake from trying to clean up again
    try:
        bot.cogs.clear()
    except Exception as e:
        logger.debug(f"Error clearing cogs: {e}")

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
    cogs = ["cogs.voice_processing_cog", "cogs.DALLE_cog", "cogs.SecretSanta_cog"]
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
    logger.info(f"Received signal {signum}")
    # Create task in the event loop if it's running
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Create a task that will also stop the bot
            async def shutdown_and_stop():
                await graceful_shutdown()
                # Force stop the bot to prevent disnake from doing its own cleanup
                try:
                    await bot.close()
                except Exception:
                    pass
            loop.create_task(shutdown_and_stop())
        else:
            # If loop isn't running, run cleanup directly
            asyncio.run(graceful_shutdown())
    except RuntimeError:
        # No event loop, run cleanup directly
        asyncio.run(graceful_shutdown())


for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, handle_signal)

if __name__ == "__main__":
    logger.info("Starting bot...")

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

    max_retries = 5
    retry_count = 0
    
    try:
        while retry_count < max_retries:
            try:
                bot.run(config.DISCORD_TOKEN, reconnect=True)
                break  # If we get here, bot shut down normally
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                break
            except Exception as e:
                retry_count += 1
                logger.critical(f"Bot failed (attempt {retry_count}/{max_retries}): {e}", exc_info=True)
                
                if retry_count < max_retries:
                    wait_time = min(30, 5 * retry_count)  # Exponential backoff, max 30s
                    logger.info(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    logger.critical("Max retries exceeded. Bot will not restart.")
    finally:
        # Ensure cleanup even if bot crashes
        try:
            # Try to run cleanup in existing event loop if possible
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Schedule cleanup task
                    loop.create_task(graceful_shutdown())
                else:
                    asyncio.run(graceful_shutdown())
            except RuntimeError:
                # No event loop, create new one
                asyncio.run(graceful_shutdown())
        except Exception as cleanup_error:
            logger.error(f"Cleanup failed: {cleanup_error}")
        finally:
            # Force exit to prevent hanging
            import os
            os._exit(0)
