import asyncio
from tempfile import NamedTemporaryFile
from pydub import AudioSegment
from disnake.ext import commands

class VoiceCog(commands.Cog):
    def __init__(self, bot, config, voice_processing_cog):
        self.bot = bot
        self.voice_processing_cog = voice_processing_cog
        self.polly_client = voice_processing_cog.polly_client

    async def speak(self, text, voice_client, user_id):
        print("Starting to speak...")
        await asyncio.sleep(0)  # Delay Before Speaking

        # Load the quiet sound
        quiet_sound = AudioSegment.from_wav("yt1s_nYWSz5R.wav")

        # Get the voice ID from the VoiceProcessingCog
        voice_id = self.voice_processing_cog.user_voices.get(user_id, "Joanna")

        try:
            response = self.polly_client.synthesize_speech(
                VoiceId=voice_id,
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
                await self.speak(text, voice_client, ctx.author.id)
            else:
                voice_client = await voice_channel.connect()
                await self.speak(text, voice_client, ctx.author.id)
        else:
            await ctx.send("Please join a voice channel.")

def setup(bot):
    voice_processing_cog = bot.get_cog("VoiceProcessingCog")
    if voice_processing_cog is None:
        print("VoiceProcessingCog has not been loaded yet.")
        return
    config = bot.config
    bot.add_cog(VoiceCog(bot, config, voice_processing_cog))
