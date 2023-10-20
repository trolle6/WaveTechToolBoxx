import json
import boto3
from tempfile import NamedTemporaryFile
import asyncio
from pydub import AudioSegment
from disnake.ext import commands


class VoiceProcessingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Configure Polly client
        try:
            with open('config.json', 'r') as f:
                config = json.load(f)
        except FileNotFoundError:
            print("The config.json file was not found.")
            exit()
        except json.JSONDecodeError:
            print("Error parsing config.json. Please make sure it is a valid JSON file.")
            exit()

        self.polly_client = boto3.Session(
            aws_access_key_id=config['aws']['access_key_id'],
            aws_secret_access_key=config['aws']['secret_access_key'],
            region_name=config['aws']['region_name']
        ).client('polly')

    async def speak(self, text, voice_client):
        print("Starting to speak...")
        await asyncio.sleep(0)  # Delay Before Speaking
        # ... (rest of your speak code here)

    async def check_voice_channels(self):
        for guild in self.bot.guilds:
            for voice_channel in guild.voice_channels:
                if len(voice_channel.members) == 1 and self.bot.user in voice_channel.members:
                    voice_client = voice_channel.guild.voice_client
                    if voice_client and voice_client.is_connected():
                        await voice_client.disconnect()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        await self.check_voice_channels()


def setup(bot):
    bot.add_cog(VoiceProcessingCog(bot))
