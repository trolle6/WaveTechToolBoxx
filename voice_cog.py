import json
import openai
import disnake
from disnake.ext import commands
import asyncio
import os
import aiohttp

with open('config.json') as config_file:
    config = json.load(config_file)

openai.api_key = config['openai']['api_key']

class VoiceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = config

    async def process_with_openai(self, text):
        response = openai.Completion.create(
            engine=self.config['openai']['engine'] if 'engine' in self.config['openai'] else "text-davinci-003",
            prompt=text,
            max_tokens=self.config['openai'].get('max_tokens', 1000),
            n=1,
            stop=None,
            temperature=self.config['openai'].get('temperature', 0.5)
        )
        processed_text = response.choices[0].text.strip()
        return processed_text

    async def generate_speech(self, text, voice):
        response = openai.TextToSpeech.create(
            text=text,
            voice=voice
        )
        audio_url = response['audio_url']
        return audio_url

    @commands.command(name='speak', help='Make the bot speak generated text')
    async def speak(self, ctx, *, message):
        processed_message = await self.process_with_openai(message)
        voice_code = self.config['voice']['default_voice']
        audio_url = await self.generate_speech(processed_message, voice_code)
        if ctx.author.voice:
            voice_channel = ctx.author.voice.channel
            if voice_channel not in self.bot.voice_clients:
                voice_client = await voice_channel.connect()
            else:
                voice_client = disnake.utils.get(self.bot.voice_clients, guild=ctx.guild)
            async with aiohttp.ClientSession() as session:
                async with session.get(audio_url) as resp:
                    if resp.status == 200:
                        audio_data = await resp.read()
                        with open('temp_audio.mp3', 'wb') as f:
                            f.write(audio_data)
                        audio_source = disnake.FFmpegPCMAudio('temp_audio.mp3')
                        voice_client.play(audio_source)
                        while voice_client.is_playing():
                            await asyncio.sleep(1)
                        await voice_client.disconnect()
                        os.remove('temp_audio.mp3')  # Corrected file name for removal
        else:
            await ctx.send("You are not in a voice channel.")

def setup(bot):
    bot.add_cog(VoiceCog(bot))
