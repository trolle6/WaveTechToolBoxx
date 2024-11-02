import disnake
from disnake.ext import commands
import logging
import json
import sys
import signal
import asyncio
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

# Constants
MAX_MESSAGE_LENGTH = 1990  # Max length for Discord messages minus formatting

# Define the DiscordLogHandler class here
class DiscordLogHandler(logging.Handler):
    def __init__(self, bot: commands.Bot, channel_id: int):
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id
        self.queue = asyncio.Queue()
        self.task = None  # Will be initialized in on_ready

    def start_logging(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.process_logs())

    def emit(self, record: logging.LogRecord):
        self.queue.put_nowait(record)

    async def process_logs(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            self.bot.logger.error(f"DiscordLogHandler: Channel with ID {self.channel_id} not found.")
            # Optional: Implement a retry mechanism or notify the bot admin
            return
        while True:
            record = await self.queue.get()
            try:
                log_entry = self.format(record)
                # Split long messages to fit Discord's message length limit
                for chunk in [log_entry[i:i+MAX_MESSAGE_LENGTH] for i in range(0, len(log_entry), MAX_MESSAGE_LENGTH)]:
                    await channel.send(f"```{chunk}```")
            except Exception as e:
                self.bot.logger.error(f"Error sending log message to Discord: {e}", exc_info=True)
            self.queue.task_done()

# Load configuration first
def load_config() -> Dict[str, Any]:
    try:
        with open("config.json") as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        logging.basicConfig(level=logging.ERROR)
        logger = logging.getLogger("bot_logger")
        logger.error("config.json file not found. Please ensure it exists in the project directory.")
        raise
    except json.JSONDecodeError as e:
        logging.basicConfig(level=logging.ERROR)
        logger = logging.getLogger("bot_logger")
        logger.error(f"JSON decode error in config.json: {e}")
        raise
    except Exception as e:
        logging.basicConfig(level=logging.ERROR)
        logger = logging.getLogger("bot_logger")
        logger.error(f"Failed to load configuration: {e}")
        raise

config = load_config()

# Set up the unified logger
def setup_logger(config: Dict[str, Any]) -> logging.Logger:
    logger = logging.getLogger("bot_logger")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG if config.get("debug", False) else logging.INFO)

        # File handler with rotation
        log_file_path = config.get("discord", {}).get("log_file_path", "bot.log")
        file_handler = RotatingFileHandler(
            log_file_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_level = logging.DEBUG if config.get("debug", False) else logging.INFO
        console_handler.setLevel(console_level)
        console_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    return logger

logger = setup_logger(config)
logger.debug("Logger has been set up.")

# Create the bot instance
intents = disnake.Intents.all()

bot = commands.InteractionBot(
    intents=intents,
    test_guilds=[int(config["discord"]["guild_id"])]
)

bot.config = config  # Assign config to the bot instance
bot.logger = logger  # Assign logger to bot.logger

# Bot events and run code
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info("Bot is ready and running.")

    # Set up the Discord log handler
    log_channel_id = int(bot.config["discord"]["log_channel_id"])
    discord_handler = DiscordLogHandler(bot, log_channel_id)
    discord_handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    discord_handler.setFormatter(formatter)
    logger.addHandler(discord_handler)
    discord_handler.start_logging()
    logger.info("Discord log handler has been added.")

@bot.event
async def on_error(event_method: str, *args, **kwargs):
    logger.error(f"Error in {event_method}", exc_info=True)

@bot.event
async def on_slash_command_error(inter: disnake.ApplicationCommandInteraction, error: Exception):
    command_name = getattr(inter.application_command, "name", "unknown command")
    logger.error(f"Error in slash command '{command_name}': {error}", exc_info=True)
    try:
        await inter.send(f"❌ An error occurred: {error}", ephemeral=True)
    except disnake.InteractionResponded:
        logger.warning(f"Failed to send error message to user for command '{command_name}'. Interaction already responded to.")

async def shutdown():
    logger.info("Shutting down bot...")
    for voice_client in bot.voice_clients:
        await voice_client.disconnect()
    # Filter out tasks related to the bot's own loop and other critical tasks
    tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await bot.close()
    logger.info("Bot has shut down successfully.")
    sys.exit(0)

def handle_signal(sig, frame):
    try:
        signal_name = signal.Signals(sig).name
    except ValueError:
        signal_name = str(sig)
    logger.info(f"Received exit signal {signal_name}...")
    asyncio.create_task(shutdown())

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# Load cogs
bot.load_extension('cogs.SecretSanta_cog')
bot.load_extension('cogs.voice_processing_cog')
bot.load_extension('cogs.DALLE_cog')

# Run the bot
if __name__ == "__main__":
    logger.info("Bot is starting...")
    try:
        bot.run(config["discord"]["token"])
    except Exception as e:
        logger.error(f"Error running the bot: {e}", exc_info=True)
        sys.exit(1)
