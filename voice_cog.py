import asyncio
import boto3
from tempfile import NamedTemporaryFile
from pydub import AudioSegment
from disnake.ext import commands

class VoiceCog(commands.Cog):
    def __init__(self, bot, aws_config):
        self.bot = bot
        self.aws_access_key_id = aws_config['access_key_id']
        self.aws_secret_access_key = aws_config['secret_access_key']
        self.region_name = aws_config['region_name']

    async def speak(self, text, voice_client):
        print("Starting to speak...")
        await asyncio.sleep(0)  # Delay Before Speaking

        # Load the quiet sound
        quiet_sound = AudioSegment.from_wav("yt1s_nYWSz5R.wav")

        try:
            response = self.polly_client.synthesize_speech(
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

                source = self.bot.FFmpegPCMAudio("combined_sound.mp3")  # Use the combined sound file
                voice_client.play(source, after=lambda e: print('done', e))
        except Exception as e:
            print(f"An error occurred: {e}")

        print("Finished speaking.")

    @commands.command(name='speak')
    async def speak_command(self, ctx, *, text: str):
        voice_channel = ctx.author.voice.channel
        if voice_channel:
            voice_client = ctx.guild.voice_client
            if voice_client and voice_client.is_connected():
                await self.speak(text, voice_client)
            else:
                voice_client = await voice_channel.connect()
                await self.speak(text, voice_client)
        else:
            await ctx.send("Please join a voice channel.")

def setup(bot):
    aws_config = {
        'access_key_id': bot.config['aws']['access_key_id'],
        'secret_access_key': bot.config['aws']['secret_access_key'],
        'region_name': bot.config['aws']['region_name']
    }
    bot.add_cog(VoiceCog(bot, aws_config))
