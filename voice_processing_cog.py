# voice_processing_cog.py

import disnake
from disnake.ext import commands
import asyncio
import logging
import os
from gtts import gTTS
import time


def setup_logger(name, log_file, level=logging.INFO):
    """Sets up a logger."""
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        handler.setFormatter(formatter)
        logger.setLevel(level)
        logger.addHandler(handler)
    return logger


class VoiceProcessingCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.logger = setup_logger("VoiceProcessing", "VoiceProcessing.log")
        self.channel_id = int(config["discord"].get("channel_id"))  # Ensure this is an integer
        self.guild_id = int(config["discord"].get("guild_id"))  # Read guild_id from config
        self.queues = {}  # asyncio.Queue per guild
        self.locks = {}  # asyncio.Lock per guild to prevent race conditions

        # Define audio directory within the project
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.audio_dir = os.path.join(current_dir, 'tts_audios')
        os.makedirs(self.audio_dir, exist_ok=True)
        self.logger.info(f"TTS audio directory set to: {self.audio_dir}")

    @commands.Cog.listener()
    async def on_ready(self):
        self.logger.info("VoiceProcessingCog is ready.")

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore messages from bots or in other channels or guilds
        if message.author.bot:
            return

        if message.guild.id != self.guild_id:
            return

        if message.channel.id != self.channel_id:
            return

        self.logger.info(f"Processing TTS for message from {message.author} in channel ID {message.channel.id}")
        print(
            f"Processing TTS for message from {message.author} in channel ID {message.channel.id}")  # For immediate
        # feedback

        # Proceed with TTS processing
        await self.enqueue_tts(message)

    async def enqueue_tts(self, message):
        guild_id = message.guild.id

        # Initialize queue and lock for the guild if not present
        if guild_id not in self.queues:
            self.queues[guild_id] = asyncio.Queue()
            self.locks[guild_id] = asyncio.Lock()
            await asyncio.create_task(self.process_queue(guild_id))
            self.logger.info(f"Created new queue and processing task for guild ID {guild_id}.")

        await self.queues[guild_id].put(message)
        self.logger.info(f"Message from {message.author} queued for TTS in guild {message.guild.name}.")

    async def process_queue(self, guild_id):
        queue = self.queues[guild_id]
        lock = self.locks[guild_id]

        while True:
            try:
                message = await queue.get()
                async with lock:
                    await self.process_tts(message)
                queue.task_done()
            except asyncio.CancelledError:
                self.logger.info(f"Queue processing task for guild ID {guild_id} has been cancelled.")
                break
            except Exception as e:
                self.logger.error(f"Error processing message in guild {guild_id}: {e}", exc_info=True)

            # Check if the queue is empty to potentially exit the loop
            if queue.empty():
                # Remove the queue and lock
                del self.queues[guild_id]
                del self.locks[guild_id]
                self.logger.info(f"No more messages in queue for guild ID {guild_id}. Queue task exiting.")
                break

    async def process_tts(self, message):
        member = message.author
        guild = message.guild
        guild_id = guild.id  # Ensure guild_id is defined

        # Check if the user is in a voice channel
        if not member.voice or not member.voice.channel:
            self.logger.warning(f"User {member} attempted TTS but is not in a voice channel.")
            await message.channel.send("❌ You need to be in a voice channel to use TTS.")
            return

        voice_channel = member.voice.channel

        # Check if the bot is already connected to a voice channel in this guild
        voice_client = disnake.utils.get(self.bot.voice_clients, guild=guild)

        if voice_client and voice_client.is_connected():
            self.logger.info(
                f"Bot is already connected to voice channel: {voice_client.channel.name} in guild: {guild.name}")
            target_voice_client = voice_client
        else:
            try:
                # Connect to the voice channel
                target_voice_client = await voice_channel.connect()
                self.logger.info(f"Connected to voice channel: {voice_channel.name} in guild: {guild.name}")
            except disnake.ClientException as e:
                self.logger.error(f"Failed to connect to voice channel: {e}")
                await message.channel.send("❌ Failed to connect to your voice channel.")
                return
            except Exception as e:
                self.logger.error(f"Unexpected error while connecting to voice channel: {e}")
                await message.channel.send("❌ An unexpected error occurred while connecting to your voice channel.")
                return

        # Define filename
        user_id = message.author.id
        timestamp = int(time.time())
        filename = f"temp_audio_file_{guild_id}_{user_id}_{timestamp}.mp3"
        audio_path = os.path.join(self.audio_dir, filename)

        # Generate TTS audio using gTTS and save to the specified file in project directory
        try:
            tts = gTTS(text=message.content, lang='en')
            tts.save(audio_path)
            self.logger.info(f"TTS audio generated and saved to {audio_path}")
        except Exception as e:
            self.logger.error(f"Failed to generate TTS audio: {e}")
            await message.channel.send("❌ Failed to generate TTS audio.")
            return

        # Ensure file exists before playing
        if not os.path.exists(audio_path):
            self.logger.error(f"Audio file was not found after saving: {audio_path}")
            await message.channel.send("❌ Failed to generate TTS audio.")
            return

        # Play the audio in the voice channel
        try:
            # Initialize FFmpeg audio source
            source = disnake.FFmpegPCMAudio(audio_path)
            if not target_voice_client.is_playing():
                # Capture guild_id and audio_path in the lambda
                target_voice_client.play(
                    source,
                    after=lambda e: self.after_playing(e, audio_path)
                )
                self.logger.info(f"Playing audio: {audio_path} in voice channel.")
            else:
                self.logger.warning("Voice client is already playing audio.")
                await message.channel.send("❌ Already playing audio in the voice channel.")
                # Re-queue the message
                await self.queues[guild_id].put(message)
                return

            # Removed the confirmation message
            # await message.channel.send("✅ Playing your message in the voice channel.")

        except Exception as e:
            self.logger.error(f"Failed to play audio: {e}")
            await message.channel.send("❌ Failed to play audio.")
        # No need to delete the file here; handled in after_playing

    def after_playing(self, error, audio_path):
        if error:
            self.logger.error(f"Error in playing audio: {error}")

        # Schedule the deletion of the audio file
        asyncio.run_coroutine_threadsafe(self.delete_audio_file(audio_path), self.bot.loop)

    async def delete_audio_file(self, audio_path):
        try:
            os.remove(audio_path)
            self.logger.info(f"Removed temporary audio file: {audio_path}")
        except Exception as e:
            self.logger.error(f"Failed to remove temporary audio file: {audio_path}. Error: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member):
        """
        Listener to check if the voice channel is empty.
        If empty, disconnect the bot.
        """
        guild_id = member.guild.id
        if guild_id != self.guild_id:
            return  # Ignore updates from other guilds

        voice_client = disnake.utils.get(self.bot.voice_clients, guild=member.guild)
        if voice_client and voice_client.is_connected():
            voice_channel = voice_client.channel
            # Check if the voice channel has no members other than the bot
            if len(voice_channel.members) == 1 and voice_channel.members[0].id == self.bot.user.id:
                self.logger.info(f"Voice channel '{voice_channel.name}' is empty. Disconnecting bot.")
                await voice_client.disconnect()


def setup(bot):
    bot.add_cog(VoiceProcessingCog(bot, bot.config))
