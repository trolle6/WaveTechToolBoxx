# main.py

import asyncio
import json
import logging
import disnake
from disnake.ext import commands
import os
import sys
from dotenv import load_dotenv
import signal

# Load environment variables from .env file (if using)
load_dotenv()

# Configure logging
logger = logging.getLogger("bot_logger")
logger.setLevel(logging.DEBUG)  # Capture all levels of logs

# Formatter for the logs
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Console handler for real-time logging
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)  # Capture all levels for console
console_handler.setFormatter(formatter)

# File handler for persistent logging
file_handler = logging.FileHandler("bot.log", encoding="utf-8")
file_handler.setLevel(logging.DEBUG)  # Capture all levels for file
file_handler.setFormatter(formatter)

# Add handlers to the logger
if not logger.handlers:
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

# Load config
def load_config():
    try:
        with open("config.json") as f:
            config = json.load(f)
        logger.debug("Loaded config.json successfully.")

        # Log the loaded config for debugging (exclude sensitive info)
        discord_config = config.get("discord", {})
        logger.debug(f"Discord Config: {discord_config}")

        # Override with environment variables if they exist
        config["discord"]["token"] = os.getenv("DISCORD_TOKEN") or config["discord"].get("token")
        config["tts"]["api_key"] = os.getenv("OPENAI_API_KEY") or config["tts"].get("api_key")
        config["dalle"]["api_key"] = os.getenv("DALLE_API_KEY") or config["dalle"].get("api_key")
        config["random_org"]["api_key"] = os.getenv("RANDOM_ORG_API_KEY") or config["random_org"].get("api_key")

        # Log the overridden config (exclude sensitive info)
        logger.debug(
            f"Overridden Discord Token: {'Set via Env Variable' if os.getenv('DISCORD_TOKEN') else 'Set via config.json'}"
        )

        if not config["discord"].get("token"):
            raise ValueError(
                "Discord token not found. Please set it in config.json or .env file."
            )
        return config
    except FileNotFoundError:
        logger.error(
            "config.json file not found. Please ensure it exists in the project directory."
        )
        raise
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in config.json: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        raise

config = load_config()

# Set up intents
intents = disnake.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.members = True  # Needed for certain functionalities

# Define a subclass of commands.Bot to use setup_hook
class MyBot(commands.Bot):
    def __init__(self, config, **kwargs):
        super().__init__(**kwargs)
        self.config = config  # Assign config to the bot instance

    async def setup_hook(self):
        # Load cogs here
        COGS = ["SecretSanta_cog", "DALLE_cog", "voice_processing_cog"]  # Update paths as needed

        for cog in COGS:
            try:
                await self.load_extension(cog)
                logger.info(f"Successfully loaded cog: {cog}")
            except commands.ExtensionNotFound:
                logger.error(f"Extension '{cog}' not found.")
            except commands.ExtensionFailed as e:
                logger.error(f"Extension '{cog}' failed to load: {e}", exc_info=True)
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred while loading cog '{cog}': {e}",
                    exc_info=True,
                )

        # Synchronize slash commands
        try:
            guild_id = int(self.config["discord"]["guild_id"])
            guild = self.get_guild(guild_id)
            if guild:
                await self.tree.sync(guild=guild)
                logger.debug(f"Fetched guild_id: {guild_id}")
                logger.info("Slash commands synchronized successfully for the guild.")
            else:
                await self.tree.sync()
                logger.warning("Guild not found. Synchronized slash commands globally.")
        except AttributeError:
            # Fallback if 'tree' attribute is missing
            logger.warning(
                "'tree' attribute not found. Attempting global synchronization."
            )
            try:
                await self.tree.sync()
                logger.info("Slash commands synchronized globally.")
            except Exception as e:
                logger.error(
                    f"Failed to synchronize slash commands: {e}", exc_info=True
                )
        except Exception as e:
            logger.error(f"Failed to synchronize slash commands: {e}", exc_info=True)

# Initialize the bot with the custom subclass and pass the config
bot = MyBot(command_prefix="!", intents=intents, config=config)

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Bot Attributes: {dir(bot)}")  # Debugging line
    logger.info("done")

@bot.event
async def on_error(event_method, *args, **kwargs):
    logger.error(f"Error in {event_method}", exc_info=True)

@bot.event
async def on_slash_command_error(inter, error):
    logger.error(
        f"Error in slash command '{inter.application_command.name}': {error}",
        exc_info=True,
    )
    try:
        await inter.send(f"An error occurred: {error}", ephemeral=True)
    except disnake.InteractionResponded:
        pass  # Interaction already responded to

@bot.event
async def on_shutdown():
    for vc in bot.voice_clients:
        await vc.disconnect()
    logger.info("Bot has disconnected from all voice channels and is shutting down.")

async def shutdown():
    logger.info("Shutting down bot...")
    for voice_client in bot.voice_clients:
        await voice_client.disconnect()
    await bot.close()

def handle_signal(sig, frame):
    try:
        signal_name = signal.Signals(sig).name
    except ValueError:
        # If the signal number is not recognized, use the number itself
        signal_name = str(sig)
    logger.info(f"Received exit signal {signal_name}...")
    asyncio.create_task(shutdown())

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# Run the bot
if __name__ == "__main__":
    logger.info("Bot is starting...")
    try:
        bot.run(config["discord"]["token"])
    except Exception as e:
        logger.error(f"Error running the bot: {e}", exc_info=True)
