import asyncio
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path
from typing import Any, Optional

import aiohttp
import disnake
from disnake.ext import commands
from dotenv import load_dotenv

load_dotenv("config.env")


# ============ CONFIG ============
class Config:
    """Load config with validation and defaults"""
    _required = {
        "DISCORD_TOKEN": (str, None),
        "DISCORD_GUILD_ID": (int, None),
        "DISCORD_CHANNEL_ID": (int, None),
        "DISCORD_LOG_CHANNEL_ID": (int, None),
        "DISCORD_MODERATOR_ROLE_ID": (int, None),
        "TTS_BEARER_TOKEN": (str, None),
        "OPENAI_API_KEY": (str, None),
    }
    _optional = {
        "DEBUG_MODE": (bool, False),
        "LOG_LEVEL": (str, "INFO"),
        "MAX_TTS_CACHE": (int, 50),
        "TTS_TIMEOUT": (int, 15),
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
            self.data[key] = cast_type(val) if cast_type == int else val

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
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                connector=connector,
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            try:
                await self.session.close()
                await asyncio.sleep(0.25)  # Allow time for connections to close
            except Exception as e:
                logging.getLogger("bot").debug(f"HTTP session close error: {e}")


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
    if not key or not key.startswith("sk-"):
        logger.error(f"Invalid key format: {key[:10] if key else 'EMPTY'}...")
        return False

    headers = {"Authorization": f"Bearer {key}"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    "https://api.openai.com/v1/models",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                if r.status == 200:
                    logger.info("✓ OpenAI API key is valid")
                    return True
                elif r.status == 401:
                    logger.error("✗ API key is invalid or expired")
                    return False
                else:
                    logger.warning(f"Unexpected API response: {r.status}")
                    return False
    except asyncio.TimeoutError:
        logger.warning("API key validation timeout (API might be slow)")
        return True
    except Exception as e:
        logger.warning(f"Could not validate API key: {e}")
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
                await cog.cog_unload()
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
    # Create task in the event loop
    loop = asyncio.get_event_loop()
    loop.create_task(graceful_shutdown())


for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, handle_signal)

if __name__ == "__main__":
    logger.info("Starting bot...")

    # Validate API key before loading cogs
    api_key_valid = asyncio.run(validate_openai_key(config.TTS_BEARER_TOKEN, logger))
    if not api_key_valid:
        logger.critical("OpenAI API key is invalid. Fix your config.env")
        sys.exit(1)

    # Load cogs once and store result
    num_loaded = load_cogs()
    if num_loaded == 0:
        logger.critical("No cogs loaded!")
        sys.exit(1)

    logger.info(f"Successfully loaded {num_loaded} cogs")

    try:
        bot.run(config.DISCORD_TOKEN)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.critical(f"Bot failed: {e}", exc_info=True)
    finally:
        # Ensure cleanup even if bot crashes
        try:
            asyncio.run(graceful_shutdown())
        except Exception as cleanup_error:
            logger.error(f"Cleanup failed: {cleanup_error}")
        sys.exit(1)