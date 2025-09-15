import os
import warnings
import sys
import signal
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional, List
from dataclasses import dataclass, field

import disnake
from disnake.ext import commands
from dotenv import load_dotenv

# --- Constants ---
MAX_MESSAGE_LENGTH = 1990


# --- Configuration Dataclasses ---
@dataclass
class DiscordConfig:
    guild_id: int
    channel_id: int
    log_channel_id: int
    moderator_role_id: int
    moderator_channel_id: int
    announcement_message_id: int
    no_mic_role_id: int
    log_file_path: str = "bot.log"


@dataclass
class TtsConfig:
    api_url: str
    bearer_token: str
    engine: str = "tts-1"
    delay_between_messages: float = 2.0
    temperature: float = 1.0
    max_tokens: int = 100
    retry_limit: int = 2
    enable_emotions: bool = False
    voices: Dict[str, Any] = field(default_factory=dict)
    default_voice: str = "GenericVoice"


# Suppress the RuntimeWarning about never awaited coroutines
warnings.filterwarnings("ignore", message="coroutine.*was never awaited", category=RuntimeWarning)


@dataclass
class DalleConfig:
    api_url: str
    size: str = "1024x1024"


@dataclass
class Config:
    discord: DiscordConfig
    debug: bool
    openai_api_key: str
    tts: TtsConfig
    dalle: DalleConfig
    prefix: str = "!"


# --- Load and validate environment ---
load_dotenv("config.env")


def get_env_or_exit(var: str, default: Optional[str] = None) -> str:
    value = os.getenv(var, default)
    if value is None:
        print(f"Error: Environment variable '{var}' is required.")
        sys.exit(1)
    return value


def get_int_env_or_exit(var: str) -> int:
    val = get_env_or_exit(var)
    try:
        return int(val)
    except ValueError:
        print(f"Error: '{var}' must be an integer, got '{val}'")
        sys.exit(1)


def load_config() -> Config:
    try:
        # Load TTS-specific configs
        tts_voices_list = [v.strip() for v in os.getenv("OPENAI_VOICES", "").split(",") if v.strip()]
        tts_user_map = {
            kv.split("=")[0]: kv.split("=")[1]
            for kv in os.getenv("TTS_USER_VOICE_MAPPINGS", "").split(",") if "=" in kv
        }

        return Config(
            discord=DiscordConfig(
                guild_id=get_int_env_or_exit("DISCORD_GUILD_ID"),
                channel_id=get_int_env_or_exit("DISCORD_CHANNEL_ID"),
                log_channel_id=get_int_env_or_exit("DISCORD_LOG_CHANNEL_ID"),
                moderator_role_id=get_int_env_or_exit("DISCORD_MODERATOR_ROLE_ID"),
                moderator_channel_id=get_int_env_or_exit("MODERATOR_CHANNEL_ID"),
                announcement_message_id=get_int_env_or_exit("ANNOUNCEMENT_MESSAGE_ID"),
                no_mic_role_id=get_int_env_or_exit("NO_MIC_ROLE_ID"),
                log_file_path=os.getenv("LOG_FILE_PATH", "bot.log"),
            ),
            debug=os.getenv("DEBUG_MODE", "false").lower() == "true",
            openai_api_key=get_env_or_exit("OPENAI_API_KEY", ""),
            tts=TtsConfig(
                api_url=get_env_or_exit("TTS_API_URL", ""),
                bearer_token=get_env_or_exit("TTS_BEARER_TOKEN", ""),
                voices={"available_voices": tts_voices_list, "user_voice_mappings": tts_user_map},
                default_voice=tts_voices_list[0] if tts_voices_list else "GenericVoice",
                delay_between_messages=float(os.getenv("TTS_DELAY", 2)),
                engine=os.getenv("TTS_MODEL", "tts-1"),
                temperature=float(os.getenv("TTS_TEMPERATURE", 1)),
                max_tokens=int(os.getenv("TTS_MAX_TOKENS", 100)),
                retry_limit=int(os.getenv("TTS_RETRY_LIMIT", 2)),
                enable_emotions=os.getenv("TTS_ENABLE_EMOTIONS", "false").lower() == "true",
            ),
            dalle=DalleConfig(
                api_url=get_env_or_exit("DALLE_API_URL", ""),
                size=os.getenv("DALLE_SIZE", "1024x1024"),
            ),
            prefix=os.getenv("BOT_PREFIX", "!"),
        )
    except SystemExit:
        sys.exit(1)  # Re-raise if any required variables are missing


config = load_config()


# --- Logging Setup ---
def setup_logger(cfg: Config) -> logging.Logger:
    logger = logging.getLogger("bot_logger")
    if logger.handlers:
        return logger
    level = logging.DEBUG if cfg.debug else logging.INFO
    logger.setLevel(level)

    # Rotating file handler
    file_handler = RotatingFileHandler(
        cfg.discord.log_file_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    logger.addHandler(console)

    return logger


logger = setup_logger(config)
logger.debug("Logger initialized.")

# --- Bot Initialization ---
intents = disnake.Intents.all()
bot = commands.InteractionBot(intents=intents)
bot.config = config
bot.logger = logger


# --- Discord Log Handler ---
class DiscordLogHandler(logging.Handler):
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id
        self.queue: asyncio.Queue = asyncio.Queue()
        self.task: Optional[asyncio.Task] = None

    def emit(self, record: logging.LogRecord) -> None:
        self.queue.put_nowait(record)

    def start(self) -> None:
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self._process_logs())

    async def _process_logs(self) -> None:
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            logger.error(f"Discord log channel {self.channel_id} not found.")
            return
        while True:
            record = await self.queue.get()
            message = self.format(record)
            for i in range(0, len(message), MAX_MESSAGE_LENGTH):
                await channel.send(f"```{message[i:i + MAX_MESSAGE_LENGTH]}```")
            self.queue.task_done()


@bot.event
async def on_ready() -> None:
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    handler = DiscordLogHandler(bot, config.discord.log_channel_id)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    logger.addHandler(handler)
    handler.start()
    logger.info("Discord log handler started.")


@bot.event
async def on_error(event_method: str, *args: Any, **kwargs: Any) -> None:
    logger.error(f"Unhandled error in {event_method}", exc_info=True)


@bot.event
async def on_slash_command_error(
        inter: disnake.ApplicationCommandInteraction, error: Exception
) -> None:
    cmd = getattr(inter.application_command, "name", "unknown")
    logger.error(f"Slash cmd '{cmd}' error: {error}", exc_info=True)
    try:
        await inter.send(f"❌ {error}", ephemeral=True)
    except disnake.InteractionResponded:
        logger.warning("Already responded to error.")


@bot.event
async def on_guild_join(guild: disnake.Guild) -> None:
    if guild.id != config.discord.guild_id:
        logger.warning(f"Joined unauthorized guild {guild.id}, leaving.")
        await guild.leave()


async def shutdown():
    logger.info("Shutdown initiated.")

    try:
        # Disconnect all voice clients
        for vc in bot.voice_clients:
            try:
                await vc.disconnect(force=True)
            except Exception as e:
                logger.error(f"Error disconnecting voice client: {e}")

        # Cancel all tasks except the current one
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()

        # Wait for tasks to complete with timeout
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Some tasks didn't complete in time during shutdown")

    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
    finally:
        await bot.close()


# Signal handlers
for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda *_: asyncio.create_task(shutdown()))


# --- Load Cogs with better error handling ---
def load_cogs_safely():
    """Load cogs with proper error handling"""
    cogs_to_load = [
        "cogs.SecretSanta_cog",
        "cogs.voice_processing_cog",
        "cogs.DALLE_cog"
    ]

    loaded_cogs = []

    for cog in cogs_to_load:
        try:
            bot.load_extension(cog)
            loaded_cogs.append(cog)
            logger.info(f"Successfully loaded {cog}")
        except Exception as e:
            logger.error(f"Failed to load {cog}: {e}")
            # Don't exit immediately, try to load other cogs
            continue

    return loaded_cogs


async def keep_alive():
    """Prevent the bot from exiting unexpectedly"""
    while True:
        await asyncio.sleep(3600)  # Sleep for 1 hour
        logger.debug("Keep-alive ping")


if __name__ == "__main__":
    logger.info("Starting bot...")

    try:
        # Load cogs first
        loaded_cogs = load_cogs_safely()

        if not loaded_cogs:
            logger.critical("No cogs loaded successfully! Exiting.")
            sys.exit(1)

        logger.info(f"Loaded {len(loaded_cogs)} cogs: {', '.join(loaded_cogs)}")

        # Get token
        token = get_env_or_exit("DISCORD_TOKEN")

        # Start keep-alive task using bot's event loop
        bot.keep_alive_task = bot.loop.create_task(keep_alive())

        # Run the bot
        bot.run(token)

    except Exception as e:
        logger.critical(f"Bot run failed: {e}", exc_info=True)
        sys.exit(1)