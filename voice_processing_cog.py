import requests
import json
import os
import asyncio
from disnake.ext import commands
import disnake
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class VoiceProcessingCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.available_voices = {voice: True for voice in config['openai']['voices'].get('available_voices', [])}
        self.user_voices = {}
        self.voice_clients = {}

        self.ruthro_voice = config['openai']['voices'].get("ruthro's_voice", {}).get('690607710390976633', 'echo')
        self.ruthro_user_id = '690607710390976633'

        try:
            self.no_mic_role_id = int(config['discord']['no_mic_role_id'])
        except ValueError:
            logger.error("The 'no_mic_role_id' value must be a numeric ID.")
            raise ValueError("The 'no_mic_role_id' value must be a numeric ID.") from None

        self.voice_channel_check_interval = config.get('voice_channel_check_interval', 10)

        bot.loop.create_task(self.assign_voices_to_existing_members())
        bot.loop.create_task(self.voice_channel_monitor())
        logger.info("VoiceProcessingCog initialized.")

    def has_no_mic_role(self, member):
        return any(role.id == self.no_mic_role_id for role in member.roles)

    async def assign_voices_to_existing_members(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                for member in vc.members:
                    if member != self.bot.user and not self.has_no_mic_role(member):
                        if str(member.id) == self.ruthro_user_id:
                            self.user_voices[member.id] = self.ruthro_voice
                            logger.info(f"Assigned Ruthro's voice ({self.ruthro_voice}) to user {member.id} ({member.display_name}) on bot restart.")
                        else:
                            voice = self.get_available_voice()
                            if voice:
                                self.user_voices[member.id] = voice
                                logger.info(f"Assigned voice {voice} to user {member.id} ({member.display_name}) on bot restart.")
                            else:
                                logger.info(f"No available voices for user {member.id} ({member.display_name}) on bot restart.")

    def get_available_voice(self):
        for voice, available in self.available_voices.items():
            if available and voice != self.ruthro_voice:
                self.available_voices[voice] = False
                return voice
        return None

    async def on_user_leave_voice_channel(self, member):
        if member.id in self.user_voices:
            voice = self.user_voices.pop(member.id)
            if voice != self.ruthro_voice:
                self.available_voices[voice] = True
            logger.info(f"Returned voice {voice} to pool for user {member.id} ({member.display_name})")

    async def voice_channel_monitor(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(self.voice_channel_check_interval)
            try:
                await self.check_voice_channels()
            except Exception as e:
                logger.error(f'Error in voice_channel_monitor: {e}')

    async def check_voice_channels(self):
        for guild in self.bot.guilds:
            for voice_channel in guild.voice_channels:
                if len(voice_channel.members) == 1 and self.bot.user in voice_channel.members:
                    voice_client = voice_channel.guild.voice_client
                    if voice_client and voice_client.is_connected():
                        await voice_client.disconnect()
                        del self.voice_clients[voice_channel.guild.id]
                        logger.info(f"Bot disconnected from voice channel {voice_channel.name} in guild {guild.name} as it was alone.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if self.has_no_mic_role(member):
            # Handle the case when a user joins a voice channel
            if after.channel is not None and member.id not in self.user_voices:
                voice = self.get_available_voice()
                if voice:
                    self.user_voices[member.id] = voice
                    logger.info(f"Assigned voice {voice} to user {member.id} ({member.display_name}) upon joining VC.")
                else:
                    logger.info(f"No available voices for user {member.id} ({member.display_name}) upon joining VC.")

            # Handle the case when a user leaves a voice channel
            elif before.channel is not None and after.channel is None:
                if member.id in self.user_voices:
                    # Retrieve the user's assigned voice
                    voice = self.user_voices[member.id]
                    # Remove the user from the voice pool
                    self.user_voices.pop(member.id)
                    # Make the voice available again for others to use
                    if voice != self.ruthro_voice:
                        self.available_voices[voice] = True
                    logger.info(
                        f"User {member.id} ({member.display_name}) left the voice channel. Voice {voice} returned to pool.")

    async def join_and_speak(self, voice_channel, text, user_id):
        try:
            # text = self.apply_custom_dictionary(text)  # Removed this line
            voice_client = voice_channel.guild.voice_client
            if not voice_client or not voice_client.is_connected():
                voice_client = await voice_channel.connect()
                self.voice_clients[voice_channel.guild.id] = voice_client
                logger.info(f"Connected to voice channel: {voice_channel.name}")

            if str(user_id) == self.ruthro_user_id:
                voice = self.ruthro_voice
            else:
                voice = self.user_voices.get(user_id, self.config['openai']['voices'].get('default_voice', 'default_voice'))
            logger.info(f"Using voice {voice} for user {user_id}. Speaking: '{text}'")


            payload = {
                "engine": self.config['openai']['engine'],
                "model": self.config['openai']['model'],
                "input": text,
                "voice": voice
            }
            headers = {
                "Authorization": f"Bearer {self.config['openai']['api_key']}",
                "Content-Type": "application/json"
            }

            response = requests.post(self.config['openai']['api_url'], headers=headers, data=json.dumps(payload))
            if response.status_code == 200:
                temp_file = 'temp_audio_file.mp3'
                with open(temp_file, 'wb') as audio_file:
                    audio_file.write(response.content)
                    logger.info(f"Audio file created: {temp_file}")

                source = disnake.FFmpegPCMAudio(temp_file)
                if voice_client and voice_client.is_connected():
                    voice_client.play(source, after=lambda e: self.after_playing(e, temp_file))
                else:
                    logger.info("Voice client not connected or has an issue")
            else:
                logger.error(f"Failed to generate speech. Status Code: {response.status_code}, Response: {response.text}")

        except Exception as e:
            logger.error(f'Error in join_and_speak: {e}')

    @commands.Cog.listener()
    async def on_message(self, message):
        try:
            if message.channel.id == int(self.config['discord']['channel_id']) and message.author != self.bot.user:
                if message.author.voice and message.author.voice.channel and self.has_no_mic_role(message.author):
                    await self.join_and_speak(message.author.voice.channel, message.content, message.author.id)
                else:
                    await message.channel.send(
                        f"{message.author.display_name}, you need to be in a voice channel with the no-mic role for me to speak.")
        except Exception as e:
            logger.error(f'Error in on_message: {e}')

    def after_playing(self, error, audio_file_path):
        if error:
            logger.error(f'Playback error: {error}')
        else:
            logger.info('Playback finished.')
        if os.path.exists(audio_file_path):
            os.remove(audio_file_path)
            logger.debug(f"Deleted temp audio file: {audio_file_path}")
def setup(bot):
    config = bot.config
    bot.add_cog(VoiceProcessingCog(bot, config))
