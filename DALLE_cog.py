# DALLE_cog.py

import logging
import disnake
from disnake.ext import commands
import aiohttp
import json
import io

class DALLECog(commands.Cog):
    LOGGER_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.logger = self.setup_logger('DALLECog', 'DALLE.log')
        self.DALLE_API_URL = config["dalle"].get("api_url")
        self.DALLE_API_KEY = config["dalle"].get("api_key")  # Ensure 'api_key' exists in 'dalle' section

    def setup_logger(self, name: str, log_file: str, level=logging.INFO):
        """
        Setup logger with specified name, log_file and level.
        """
        formatter = logging.Formatter(self.LOGGER_FORMAT)
        handler = logging.FileHandler(log_file, encoding='utf-8')
        handler.setFormatter(formatter)

        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.setLevel(level)
            logger.addHandler(handler)

        return logger

    async def handle_error(self, inter: disnake.ApplicationCommandInteraction, e: Exception):
        """
        Handles the given error and provides a proper message to the user.
        Logs the error for tracking.
        """
        self.logger.error(e, exc_info=True)

        if isinstance(e, commands.CommandInvokeError):
            await inter.response.send_message('❌ An error occurred while processing your request.', ephemeral=True)
        else:
            await inter.response.send_message('❌ An unknown error occurred.', ephemeral=True)

    @commands.slash_command(description="Generate an image using DALL-E")
    @commands.cooldown(rate=5, per=60, type=commands.BucketType.user)  # Limit to 5 uses per minute per user
    async def dalle(
            self,
            inter: disnake.ApplicationCommandInteraction,
            prompt: str = commands.Param(description="Enter your desired prompt", max_length=200),
            size: str = commands.Param(
                default="1024x1024",
                choices=["256x256", "512x512", "1024x1024"],
                description="Choose the image size."
            )
    ):
        """Generates an image based on the prompt using DALL·E API."""
        await inter.response.defer()
        try:
            image_data = await self.generate_image(prompt, size)
            if image_data:
                image_stream = io.BytesIO(image_data)
                image_stream.seek(0)
                file = disnake.File(fp=image_stream, filename='generated_image.png')
                await inter.followup.send(file=file)
            else:
                await inter.followup.send("❌ Failed to generate image.")
        except Exception as e:
            await self.handle_error(inter, e)

    async def generate_image(self, prompt, size):
        """Generates an image using the DALL-E API."""
        headers = {
            'Authorization': f'Bearer {self.DALLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        payload = {
            'prompt': prompt,
            'n': self.config['dalle']['settings'].get('n', 1),
            'size': size,
            'response_format': 'url'  # Assuming the API returns a URL
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.DALLE_API_URL, headers=headers, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        image_url = data['data'][0]['url']
                        self.logger.info(f"Image URL received: {image_url}")
                        async with session.get(image_url) as img_response:
                            if img_response.status == 200:
                                self.logger.info("Image downloaded successfully.")
                                return await img_response.read()
                            else:
                                self.logger.error(f"Failed to download image: {img_response.status}")
                                return None
                    else:
                        error_text = await response.text()
                        self.logger.error(f"Error generating image: {response.status}, {error_text}")
                        return None
        except aiohttp.ClientError as e:
            self.logger.error(f"HTTP error during image generation: {e}", exc_info=True)
            return None

    @dalle.error
    async def dalle_error(self, inter: disnake.ApplicationCommandInteraction, error):
        if isinstance(error, commands.CommandOnCooldown):
            await inter.response.send_message(f"⏳ You're on cooldown. Try again in {error.retry_after:.2f} seconds.", ephemeral=True)
        elif isinstance(error, commands.MissingRequiredArgument):
            await inter.response.send_message("❌ Missing required argument: prompt.", ephemeral=True)
        else:
            await self.handle_error(inter, error)

def setup(bot):
    config = bot.config
    bot.add_cog(DALLECog(bot, config))
