import disnake
from disnake.ext import commands
import json
import boto3
from tempfile import NamedTemporaryFile
from enum import Enum
import random
from typing import Union
import asyncio
from pydub import AudioSegment
import atexit
from math_cog import MathCog
from SecretSanta_cog import SecretSantaCog

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

TOKEN = config['discord']['token']
intents = disnake.Intents.all()
bot = commands.Bot(command_prefix=config['discord']['command_prefix'], intents=intents)
bot.config = config


@bot.event
async def on_ready():
    print("Bot is ready.")
    channel_id = config['discord'].get('channel_id')
    if channel_id is not None:
        channel = bot.get_channel(channel_id)
        await channel.send("ToolBox Active.")
    else:
        print("channel_id not found in config.json")

@bot.event
async def on_disconnecting():
    print("Bot is shutting down.")
    channel_id = config['discord'].get('channel_id')
    if channel_id is not None:
        channel = bot.get_channel(channel_id)
        await channel.send("ToolBox Deactive.")
    else:
        print("channel_id not found in config.json")



# Configure Polly client
polly_client = boto3.Session(
    aws_access_key_id=config['aws']['access_key_id'],
    aws_secret_access_key=config['aws']['secret_access_key'],
    region_name=config['aws']['region_name']
).client('polly')


async def speak(text, voice_client):
    print("Starting to speak...")
    await asyncio.sleep(0)  # Delay Before Speaking

    # Load the quiet sound
    quiet_sound = AudioSegment.from_wav("yt1s_nYWSz5R.wav")

    try:
        response = polly_client.synthesize_speech(
            VoiceId='Matthew',
            OutputFormat='mp3',
            Text=text
        )
        with NamedTemporaryFile(delete=False, suffix='.mp3') as file:
            file.write(response['AudioStream'].read())
            file.flush()

            # Concatenate the quiet sound and the speech
            speech_sound = AudioSegment.from_mp3(file.name)
            combined_sound = quiet_sound + speech_sound
            combined_sound.export("combined_sound.mp3", format="mp3")

            source = disnake.FFmpegPCMAudio("combined_sound.mp3")  # Use the combined sound file
            voice_client.play(source, after=lambda e: print('done', e))
    except Exception as e:
        print(f"An error occurred: {e}")

    print("Finished speaking.")

# Voice channel leave function

@bot.event
async def on_message(message):
    await bot.process_commands(message)
    print("Message received.")
    if message.author == bot.user:
        return

    if message.channel.name == "tts" and "TTS access" in [role.name for role in message.author.roles]:
        print("Correct role and channel detected.")
        try:
            voice_channel = message.author.voice.channel
            if voice_channel is not None:
                voice_client = message.guild.voice_client
                if voice_client and voice_client.is_connected():
                    await speak(message.content, voice_client)
                else:
                    voice_client = await voice_channel.connect()
                    await speak(message.content, voice_client)
            else:
                await message.channel.send("Please join a voice channel.")
        except AttributeError:
            await message.channel.send("You need to be in a voice channel to use this feature.")

    @commands.command()
    async def ping(self, ctx):
        await ctx.send(f'Pong! Latency is {bot.latency * 1000}ms')

target_message_id = config['discord'].get('target_message_id', None)
bot.target_message_id = target_message_id

aws_config = config['aws']

target_message_id = config['discord'].get('target_message_id')
if target_message_id is None:
    print("target_message_id not found in config.json")

bot.load_extension("admin_cog")
bot.load_extension('todo_cog')
bot.load_extension("reaction_cog")
bot.load_extension("math_cog")
bot.load_extension("color_cog")
bot.load_extension("voice_processing_cog")
bot.load_extension("voice_cog")
voice_processing_cog = bot.get_cog("VoiceProcessingCog")
bot.add_cog(SecretSantaCog(bot, config))

bot.target_message_id = config['discord'].get('target_message_id', None)
bot.run(TOKEN)