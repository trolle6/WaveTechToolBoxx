import json
import boto3
from tempfile import NamedTemporaryFile
import asyncio
from pydub import AudioSegment
from disnake.ext import commands

class VoiceProcessingCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config  # New line
        self.user_voices = {}
        self.polly_client = boto3.Session(
            aws_access_key_id=self.config['aws']['access_key_id'],  # Modified
            aws_secret_access_key=self.config['aws']['secret_access_key'],  # Modified
            region_name=self.config['aws']['region_name']  # Modified
        ).client('polly')
        self.available_voices = self.config['aws'].get('available_voices', [])  # Modified

        # Configure Polly client
        try:
            with open('config.json', 'r') as f:
                config = json.load(f)
            self.polly_client = boto3.Session(
                aws_access_key_id=config['aws']['access_key_id'],
                aws_secret_access_key=config['aws']['secret_access_key'],
                region_name=config['aws']['region_name']
            ).client('polly')
            self.available_voices = config['aws'].get('available_voices', [])
        except FileNotFoundError:
            print("The config.json file was not found.")
            exit()
        except json.JSONDecodeError:
            print("Error parsing config.json. Please make sure it is a valid JSON file.")
            exit()

    def has_allowed_role(self, user):
        return True  # Placeholder

    def on_user_join_voice_channel(self, member):
        if member == self.bot.user:  # Skip if the bot itself has joined the channel
            return

        if self.has_allowed_role(member):
            if self.available_voices:
                voice = self.available_voices.pop(0)
                self.user_voices[member.id] = voice
                print(f"Assigning voice {voice} to user {member.id} ({member.display_name})")
            else:
                print(f"No more available voices for user {member.id} ({member.display_name})")

    def on_user_leave_voice_channel(self, member):
        if member == self.bot.user:  # Skip if the bot itself has left the channel
            return

        if member.id in self.user_voices:
            voice = self.user_voices.pop(member.id)
            self.available_voices.append(voice)
            print(f"Removing voice {voice} from user {member.id} ({member.display_name})")

    async def speak(self, text, voice_client, user_id):
        print("Starting to speak...")
        await asyncio.sleep(0)

        voice = self.user_voices.get(user_id, "Joanna")
        print(f"Using voice {voice} for user {user_id}")

        voice = self.user_voices.get(user_id, "Joanna")
        response = self.polly_client.synthesize_speech(
            Text=text,
            OutputFormat='mp3',
            VoiceId=voice
        )
        soundfile = NamedTemporaryFile(delete=True)
        soundbytes = response['AudioStream'].read()
        soundfile.write(soundbytes)

        audio = AudioSegment.from_file(soundfile.name, format="mp3")
        audio.export("temp.mp3", format="mp3")

        # Your logic to play this in the voice channel

    async def check_voice_channels(self):
        for guild in self.bot.guilds:
            for voice_channel in guild.voice_channels:
                if len(voice_channel.members) == 1 and self.bot.user in voice_channel.members:
                    voice_client = voice_channel.guild.voice_client
                    if voice_client and voice_client.is_connected():
                        await voice_client.disconnect()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel is None and after.channel is not None:
            self.on_user_join_voice_channel(member)
        elif before.channel is not None and after.channel is None:
            self.on_user_leave_voice_channel(member)
        await self.check_voice_channels()

def setup(bot):
    config = bot.config  # New line
    bot.add_cog(VoiceProcessingCog(bot, config))  # Modified
