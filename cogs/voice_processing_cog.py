# cogs/voice_processing_cog.py

import asyncio
import os
import random
import aiohttp
import disnake
from disnake.ext import commands

class VoiceProcessingCog(commands.Cog):
    """
    Cog to handle Text-to-Speech (TTS) functionalities.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = bot.config  # Use bot's config
        self.logger = bot.logger  # Use bot's logger

        # Access TTS settings from the main bot's config
        self.tts_api_key = self.config['openai_api_key']
        self.tts_api_url = self.config['tts']['api_url']
        self.default_voice = self.config['tts']['default_voice']
        self.available_voices = self.config['tts']['voices']['available_voices']
        self.delay_between_messages = self.config['tts']['delay_between_messages']

        # Custom voice mappings for specific users
        self.user_voice_mappings = self.config['tts']['voices'].get('user_voice_mappings', {})
        self.required_role_id = int(self.config['discord']['no_mic_role_id'])  # Or the correct role ID
        self.excluded_role_name = "RUTHRO VOICE"

        # Fixed audio file path in the project directory
        self.audio_file = "temp_audio_file.mp3"
        self.audio_path = os.path.join(os.getcwd(), self.audio_file)
        self.logger.debug(f"Fixed TTS audio file path set to: {self.audio_path}")

        self.voice_clients = {}  # Dictionary to manage voice clients per guild
        self.queue = asyncio.Queue()  # Queue for TTS messages

        self.user_voices = {}  # Stores assigned voices per user

        # Start the process_queue task
        self.bot.loop.create_task(self.process_queue())

    @commands.Cog.listener()
    async def on_message(self, message):
        """
        Listener for new messages to process TTS.

        Args:
            message (disnake.Message): The incoming message.
        """
        self.logger.debug(f"on_message triggered: {message.content}")

        # Ignore messages from bots
        if message.author.bot:
            self.logger.debug(f"Ignored message from bot: {message.author}")
            return

        # Check if the message is in the specified guild and channel
        guild_id = int(self.config['discord']['guild_id'])
        channel_id = int(self.config['discord']['channel_id'])
        if message.guild.id != guild_id or message.channel.id != channel_id:
            self.logger.debug(
                f"Ignored message from guild ID {message.guild.id} or channel ID {message.channel.id}"
            )
            return

        self.logger.info(
            f"Processing TTS for message from {message.author} in channel ID {message.channel.id}"
        )
        self.logger.debug(f"Message content: {message.content}")

        # Enqueue the message for TTS processing
        await self.enqueue_tts(message)

    async def enqueue_tts(self, message):
        """
        Enqueues a message for TTS processing.

        Args:
            message (disnake.Message): The message to process.
        """
        await self.queue.put(message)
        self.logger.info(
            f"Message from {message.author} queued for TTS in guild {message.guild.name}."
        )

    async def process_queue(self):
        """
        Continuously processes the TTS queue.
        """
        self.logger.debug("process_queue task started.")
        while True:
            try:
                message = await self.queue.get()
                self.logger.debug(f"Got message from queue: {message.content}")
                await self.process_tts(message)
            except asyncio.CancelledError:
                self.logger.info("process_queue task has been cancelled.")
                break
            except Exception as exc:
                self.logger.error(f"Unexpected error in process_queue: {exc}", exc_info=True)
            finally:
                self.queue.task_done()

    async def process_tts(self, message):
        """
        Processes a single message for TTS.

        Args:
            message (disnake.Message): The message to process.
        """
        member = message.author
        guild = message.guild

        self.logger.debug(f"Starting TTS processing for message ID {message.id} from {member}")

        # Check if the user is in a voice channel
        if not member.voice or not member.voice.channel:
            self.logger.warning(f"User {member} attempted TTS but is not in a voice channel.")
            await message.channel.send("❌ You need to be in a voice channel to use TTS.")
            return

        # Check if the member should be assigned a voice
        if not await self.should_assign_voice(member):
            self.logger.info(f"User {member} is not eligible for voice assignment.")
            return

        # Assign or retrieve the user's voice
        if member.id not in self.user_voices:
            voice_id = random.choice(self.available_voices)
            self.user_voices[member.id] = voice_id
            self.logger.info(f"Assigned voice '{voice_id}' to user {member}.")
        else:
            voice_id = self.user_voices[member.id]
            self.logger.info(f"Using previously assigned voice '{voice_id}' for user {member}.")

        voice_channel = member.voice.channel
        self.logger.debug(f"User {member} is in voice channel: {voice_channel.name}")

        # Get or connect to the voice client for this guild
        voice_client = self.voice_clients.get(guild.id)

        if not voice_client or not voice_client.is_connected():
            try:
                voice_client = await voice_channel.connect()
                self.voice_clients[guild.id] = voice_client
                self.logger.info(
                    f"Connected to voice channel: {voice_channel.name} in guild: {guild.name}"
                )
            except disnake.ClientException as exc:
                self.logger.error(f"Failed to connect to voice channel: {exc}", exc_info=True)
                await message.channel.send(f"❌ Failed to connect to your voice channel: {exc}")
                return
            except Exception as exc:
                self.logger.error(
                    f"Unexpected error while connecting to voice channel: {exc}", exc_info=True
                )
                await message.channel.send(
                    f"❌ An unexpected error occurred while connecting to your voice channel: {exc}"
                )
                return
        else:
            if voice_client.channel.id != voice_channel.id:
                try:
                    await voice_client.move_to(voice_channel)
                    self.logger.info(
                        f"Moved to voice channel: {voice_channel.name} in guild: {guild.name}"
                    )
                except Exception as exc:
                    self.logger.error(f"Failed to move to voice channel: {exc}", exc_info=True)
                    await message.channel.send(f"❌ Failed to move to your voice channel: {exc}")
                    return

        # Generate TTS audio
        self.logger.debug(f"Generating TTS audio for message ID {message.id}")
        audio_content = await self.generate_tts_audio(message.content.strip(), voice_id)

        if audio_content:
            try:
                with open(self.audio_path, 'wb') as audio_file:
                    audio_file.write(audio_content)
                self.logger.info(f"TTS audio generated and saved to {self.audio_path}")
            except Exception as exc:
                self.logger.error(f"Failed to save audio file: {exc}", exc_info=True)
                await message.channel.send("❌ Failed to save TTS audio.")
                return
        else:
            self.logger.error("Failed to generate TTS audio.")
            await message.channel.send("❌ Failed to generate TTS audio.")
            return

        # Play the audio in the voice channel
        try:
            source = disnake.FFmpegPCMAudio(self.audio_path)
            if not voice_client.is_playing():
                voice_client.play(
                    source,
                    after=lambda e: asyncio.run_coroutine_threadsafe(
                        self.after_playing(guild.id, e), self.bot.loop
                    )
                )
                self.logger.info(f"Playing audio: {self.audio_path} in voice channel.")
                await asyncio.sleep(self.delay_between_messages)
            else:
                self.logger.warning("Voice client is already playing audio. Re-queuing the message.")
                await self.queue.put(message)
        except Exception as exc:
            self.logger.error(f"Failed to play audio: {exc}", exc_info=True)
            await message.channel.send(f"❌ Failed to play audio: {exc}")
            await self.delete_audio_file()

    async def should_assign_voice(self, member):
        excluded_role = disnake.utils.get(member.guild.roles, name=self.excluded_role_name)
        has_excluded_role = excluded_role in member.roles if excluded_role else False
        return not has_excluded_role

    async def generate_tts_audio(self, content: str, voice_id: str) -> bytes:
        self.logger.debug("Starting TTS audio generation.")
        try:
            payload = {
                "voice": voice_id,
                "input": content,
                "temperature": self.config['tts'].get('temperature', 0.5),
                "max_tokens": self.config['tts'].get('max_tokens', 100),
                "model": self.config['tts'].get('engine', 'standard')
            }

            self.logger.debug(f"TTS API payload: {payload}")

            headers = {
                "Authorization": f"Bearer {self.tts_api_key}",
                "Content-Type": "application/json"
            }

            self.logger.debug(f"Sending POST request to TTS API at {self.tts_api_url}")

            async with aiohttp.ClientSession() as session:
                async with session.post(self.tts_api_url, json=payload, headers=headers) as response:
                    self.logger.debug(f"TTS API responded with status: {response.status}")
                    if response.status == 200:
                        audio_content = await response.read()
                        self.logger.info("TTS audio successfully generated.")
                        return audio_content
                    else:
                        error_text = await response.text()
                        self.logger.error(
                            f"TTS API request failed with status {response.status}: {error_text}"
                        )
                        return None

        except Exception as e:
            self.logger.error(f"Failed to generate TTS audio: {e}", exc_info=True)
            return None

    async def after_playing(self, guild_id: int, error):
        if error:
            self.logger.error(f"Error in playing audio for guild {guild_id}: {error}", exc_info=True)
        else:
            self.logger.debug(f"Audio played successfully: {self.audio_path}")

        await self.delete_audio_file()

    async def delete_audio_file(self):
        try:
            if os.path.exists(self.audio_path):
                os.remove(self.audio_path)
                self.logger.info(f"Removed temporary audio file: {self.audio_path}")
            else:
                self.logger.warning(f"Attempted to delete non-existent audio file: {self.audio_path}")
        except Exception as exc:
            self.logger.error(
                f"Failed to remove temporary audio file: {self.audio_path}. Error: {exc}",
                exc_info=True
            )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild = member.guild
        guild_id = guild.id

        if guild_id != int(self.config['discord']['guild_id']):
            self.logger.debug(f"Ignored voice state update from guild ID {guild_id}")
            return

        voice_client = self.voice_clients.get(guild_id)
        if not voice_client:
            self.logger.debug(f"No active voice client found for guild ID {guild_id}")
            return

        voice_channel = voice_client.channel
        if len(voice_channel.members) == 1 and voice_channel.members[0].id == self.bot.user.id:
            try:
                await voice_client.disconnect()
                self.logger.info(
                    f"Voice channel '{voice_channel.name}' is empty. Disconnected from voice channel."
                )
                del self.voice_clients[guild_id]
            except Exception as exc:
                self.logger.error(
                    f"Failed to disconnect from voice channel: {exc}", exc_info=True
                )

        if before.channel is not None and after.channel is None:
            if member.id in self.user_voices:
                del self.user_voices[member.id]
                self.logger.info(f"Cleared voice assignment for user {member} after leaving voice channel.")

    @commands.slash_command(name="leave", description="Make the bot leave the voice channel.")
    async def leave(self, inter: disnake.ApplicationCommandInteraction):
        voice_client = inter.guild.voice_client
        if voice_client and voice_client.is_connected():
            try:
                await voice_client.disconnect()
                await inter.send("Disconnected from the voice channel.", ephemeral=True)
                self.logger.info(
                    f"Disconnected from voice channel in guild '{inter.guild.name}'."
                )
                self.voice_clients.pop(inter.guild.id, None)
            except Exception as exc:
                self.logger.error(
                    f"Failed to disconnect from voice channel in guild '{inter.guild.name}': {exc}",
                    exc_info=True
                )
                await inter.send(
                    "❌ Failed to disconnect from the voice channel.", ephemeral=True
                )
        else:
            await inter.send("I'm not connected to any voice channel.", ephemeral=True)
            self.logger.info(
                f"Leave command invoked but bot was not connected to a voice channel in guild '{inter.guild.name}'."
            )

    def cog_unload(self):
        self.logger.info("VoiceProcessingCog is being unloaded.")
        for guild_id, voice_client in list(self.voice_clients.items()):
            if voice_client.is_connected():
                try:
                    asyncio.create_task(voice_client.disconnect())
                    self.logger.info(
                        f"Disconnected from voice channel in guild ID {guild_id} during unload."
                    )
                except Exception as exc:
                    self.logger.error(
                        f"Failed to disconnect from voice channel in guild ID {guild_id}: {exc}",
                        exc_info=True
                    )
        if hasattr(self, 'process_queue_task') and self.process_queue_task and not self.process_queue_task.done():
            self.process_queue_task.cancel()
            self.logger.debug("Cancelled process_queue task during cog unload.")
        if os.path.exists(self.audio_path):
            try:
                os.remove(self.audio_path)
                self.logger.info(f"Removed fixed audio file: {self.audio_path}")
            except Exception as exc:
                self.logger.error(
                    f"Failed to remove fixed audio file: {self.audio_path}. Error: {exc}",
                    exc_info=True
                )

def setup(bot):
    bot.add_cog(VoiceProcessingCog(bot))
