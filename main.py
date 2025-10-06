import os
import warnings
import sys
import signal
import asyncio
import logging
import time
import psutil
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional
from dataclasses import dataclass, field

import disnake
from disnake.ext import commands
from dotenv import load_dotenv

MAX_MESSAGE_LENGTH = 1990


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


warnings.filterwarnings("ignore", message="coroutine.*was never awaited", category=RuntimeWarning)
load_dotenv("config.env")


def get_env_or_exit(var: str, default: Optional[str] = None) -> str:
    if (value := os.getenv(var, default)) is None:
        print(f"Error: Environment variable '{var}' is required.")
        sys.exit(1)
    return value


def get_int_env_or_exit(var: str) -> int:
    value_str = get_env_or_exit(var)
    try:
        return int(value_str)
    except ValueError:
        print(f"Error: '{var}' must be an integer, got '{value_str}'")
        sys.exit(1)


def load_config() -> Config:
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


config = load_config()


def setup_logger(cfg: Config) -> logging.Logger:
    logger = logging.getLogger("bot_logger")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG if cfg.debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    file_handler = RotatingFileHandler(cfg.discord.log_file_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    return logger


logger = setup_logger(config)

intents = disnake.Intents.all()
bot = commands.InteractionBot(intents=intents)
bot.config, bot.logger = config, logger
bot.start_time = time.time()


class DiscordLogHandler(logging.Handler):
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__()
        self.bot, self.channel_id, self.queue = bot, channel_id, asyncio.Queue()
        self.task: Optional[asyncio.Task] = None

    def emit(self, record: logging.LogRecord) -> None:
        self.queue.put_nowait(record)

    def start(self) -> None:
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self._process_logs())

    async def _process_logs(self) -> None:
        await self.bot.wait_until_ready()
        if not (channel := self.bot.get_channel(self.channel_id)):
            logger.error(f"Discord log channel {self.channel_id} not found.")
            return

        while True:
            record = await self.queue.get()
            message = self.format(record)
            for i in range(0, len(message), MAX_MESSAGE_LENGTH):
                await channel.send(f"```{message[i:i + MAX_MESSAGE_LENGTH]}```")
            self.queue.task_done()


async def periodic_health_check():
    """Periodic health check for the entire bot"""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        try:
            # Check if bot is responding
            if not bot.is_ready():
                logger.warning("Bot is not ready, attempting to reconnect...")
                # Add reconnection logic if needed

            # Log memory usage for monitoring
            memory_mb = psutil.Process().memory_info().rss / 1024 / 1024
            if memory_mb > 500:  # If using more than 500MB
                logger.warning(f"High memory usage: {memory_mb:.2f} MB")

        except Exception as e:
            logger.error(f"Health check error: {e}")


@bot.event
async def on_ready() -> None:
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    handler = DiscordLogHandler(bot, config.discord.log_channel_id)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    logger.addHandler(handler)
    handler.start()

    # Start health monitoring
    asyncio.create_task(periodic_health_check())

    logger.info("Discord log handler and health monitor started.")


@bot.event
async def on_interaction(inter: disnake.Interaction):
    if inter.type == disnake.InteractionType.application_command:
        if inter.application_command.qualified_name.startswith("santa"):
            return


@bot.event
async def on_error(event_method: str, *args: Any, **kwargs: Any) -> None:
    logger.error(f"Unhandled error in {event_method}", exc_info=True)


@bot.event
async def on_slash_command_error(inter: disnake.ApplicationCommandInteraction, error: Exception) -> None:
    logger.error(f"Slash cmd '{getattr(inter.application_command, 'name', 'unknown')}' error: {error}", exc_info=True)
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
    tasks = []

    for cog in bot.cogs.values():
        for attr in ['http_session', 'http']:
            if hasattr(cog, attr) and (session := getattr(cog, attr)) and not session.closed:
                tasks.append(session.close())

    tasks.extend(vc.disconnect(force=True) for vc in bot.voice_clients)

    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
    finally:
        await bot.close()


@bot.slash_command(name="health", description="Check bot health status")
async def health_check(inter: disnake.ApplicationCommandInteraction):
    status = {
        "uptime": time.time() - bot.start_time if hasattr(bot, 'start_time') else "unknown",
        "cogs_loaded": len(bot.cogs),
        "voice_clients": len(bot.voice_clients),
        "memory_usage": f"{psutil.Process().memory_info().rss / 1024 / 1024:.2f} MB"
    }

    embed = disnake.Embed(title="🤖 Bot Health Status", color=disnake.Color.green())
    for key, value in status.items():
        embed.add_field(name=key.replace('_', ' ').title(), value=str(value), inline=True)

    await inter.response.send_message(embed=embed)


for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda *_: asyncio.create_task(shutdown()))


def load_cogs_safely():
    cogs_to_load = ["cogs.SecretSanta_cog", "cogs.voice_processing_cog", "cogs.DALLE_cog"]
    loaded_cogs = []

    for cog in cogs_to_load:
        try:
            bot.load_extension(cog)
            loaded_cogs.append(cog)
            logger.info(f"Successfully loaded {cog}")
        except Exception as e:
            logger.error(f"Failed to load {cog}: {e}")

    return loaded_cogs


if __name__ == "__main__":
    logger.info("Starting bot...")

    if missing_vars := [var for var in
                        ["DISCORD_TOKEN", "DISCORD_GUILD_ID", "DISCORD_CHANNEL_ID", "TTS_BEARER_TOKEN", "TTS_API_URL"]
                        if not os.getenv(var)]:
        logger.critical(f"Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)

    loaded_cogs = load_cogs_safely()
    if not loaded_cogs:
        logger.critical("No cogs loaded successfully! Exiting.")
        sys.exit(1)

    logger.info(f"Loaded {len(loaded_cogs)} cogs: {', '.join(loaded_cogs)}")

    try:
        bot.run(get_env_or_exit("DISCORD_TOKEN"))
    except Exception as e:
        logger.critical(f"Bot run failed: {e}", exc_info=True)
        sys.exit(1)