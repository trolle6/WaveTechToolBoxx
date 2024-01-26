import disnake
from disnake.ext import commands
import json
import openai
from SecretSanta_cog import SecretSantaCog  # Assuming you have a SecretSanta_cog.py
import traceback

# Load config.json
try:
    with open('config.json', 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    print("The config.json file was not found.")
    exit()
except json.JSONDecodeError:
    print("Error parsing config.json. Please make sure it is a valid JSON file.")
    exit()

# OpenAI setup - Use the 'config' variable directly
openai.api_key = config['openai']['api_key']

# Initialize the bot
intents = disnake.Intents.all()
bot = commands.Bot(command_prefix=config['discord'].get('command_prefix', '!'), intents=intents)
bot.config = config

# Voice channel function
@bot.event
async def on_message(message):
    await bot.process_commands(message)
    if message.author == bot.user:
        return
    print("Message received.")

    tts_channel_id = config['discord'].get('channel_id')
    tts_role_name = config['discord'].get('tts_role')

    if message.channel.id == int(tts_channel_id) and tts_role_name in [role.name for role in message.author.roles]:
        # Handle TTS logic in your cogs, not here
        pass

@bot.event
async def on_ready():
    print("Bot is ready.")
    log_channel_id = int(config['discord'].get('log_channel_id', 0))
    log_channel = bot.get_channel(log_channel_id)
    if log_channel:
        await log_channel.send("Bot is ready.")
    else:
        print("Log channel not found.")

@bot.event
async def on_error(event, *args, **kwargs):
    log_channel_id = int(config['discord'].get('log_channel_id', 0))
    log_channel = bot.get_channel(log_channel_id)
    if log_channel:
        await log_channel.send(f"An error occurred in {event}: {args} {kwargs}")
    else:
        print("Log channel not found.")

# Loading Extensions
bot.load_extension("voice_processing_cog")
bot.load_extension("voice_cog")

# Add SecretSantaCog
bot.add_cog(SecretSantaCog(bot, config))

# Run the bot
bot.run(config['discord']['token'])