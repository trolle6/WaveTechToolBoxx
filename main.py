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
            else:
                self.data[key] = val

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


# ============ SETUP ============
def setup_logging(config: Config) -> logging.Logger:
    logger = logging.getLogger("bot")
    logger.setLevel(config.LOG_LEVEL)

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

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

    return logger


async def validate_openai_key(key: str, logger: logging.Logger) -> bool:
    """Test if OpenAI API key is valid before loading cogs"""
    # Clean the key (remove whitespace)
    key = key.strip() if key else ""
    
    logger.debug(f"Validating API key: {key[:15]}...")

    if not key:
        logger.error("OPENAI_API_KEY is empty or not set in config.env")
        return False

    if not key.startswith("sk-"):
        logger.error(f"Invalid key format (should start with 'sk-'): {key[:15]}...")
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
                    logger.debug(f"Key being used: {key[:15]}...")
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

logger = setup_logging(config)

# Bot setup
intents = disnake.Intents.all()
bot = commands.InteractionBot(intents=intents)
bot.config = config
bot.logger = logger
bot.http_mgr = HttpManager()
bot.ready_once = False


@bot.event
async def on_ready():
    if not bot.ready_once:
        logger.info(f"Logged in as {bot.user}")
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

    # Unload all cogs properly (this awaits cog_unload)
    for cog_name in list(bot.cogs.keys()):
        try:
            cog = bot.get_cog(cog_name)
            if cog and hasattr(cog, 'cog_unload'):
                # Properly await cog_unload
                if asyncio.iscoroutinefunction(cog.cog_unload):
                    await cog.cog_unload()
                else:
                    cog.cog_unload()
            bot.remove_cog(cog_name)
        except Exception as e:
            logger.debug(f"Cog unload error for {cog_name}: {e}")

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
            loop.create_task(graceful_shutdown())
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
