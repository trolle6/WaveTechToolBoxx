import aiohttp
import disnake
from disnake.ext import commands
import logging
import time
from typing import Any


class DALLECog(commands.Cog):
    """Enhanced DALL-E Image Generation"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger('dalle_cog')
        self.config = bot.config
        self.http_session = None

        # DALL-E config
        self.api_url = self.config.dalle.api_url
        self.api_key = self.config.openai_api_key
        self.size = self.config.dalle.size
        self.rate_limit = 5  # requests per minute per user
        self.last_requests = {}

    async def _get_session(self):
        """Get or create HTTP session"""
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession()
        return self.http_session

    async def cog_unload(self):
        """Cleanup on cog unload"""
        if self.http_session:
            await self.http_session.close()
        self.logger.info("DALLECog unloaded")

    @commands.slash_command(name="generate_image", description="Generate image with DALL-E")
    async def generate_image(self, inter: disnake.ApplicationCommandInteraction, prompt: str):
        """Generate image from text prompt"""
        if not self.api_key:
            await inter.send("DALL-E not configured", ephemeral=True)
            return

        # Validate input
        if len(prompt) > 1000:
            await inter.send("Prompt too long (max 1000 chars)", ephemeral=True)
            return

        # Rate limiting
        if not self._check_rate_limit(inter.author.id):
            await inter.send("Please wait before making another request", ephemeral=True)
            return

        await inter.response.defer()

        try:
            session = await self._get_session()
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "prompt": prompt,
                "n": 1,
                "size": self.size,
                "response_format": "url"
            }

            async with session.post(
                    self.api_url,
                    headers=headers,
                    json=data,
                    timeout=30
            ) as response:
                if response.status != 200:
                    error = await response.text()
                    self.logger.error(f"DALL-E API error: {error}")
                    await inter.edit_original_response(f"❌ API Error: {response.status}")
                    return

                result = await response.json()

            if not result.get("data"):
                await inter.edit_original_response("❌ No image generated")
                return

            image_url = result["data"][0]["url"]
            await inter.edit_original_response(image_url)

        except asyncio.TimeoutError:
            await inter.edit_original_response("❌ Request timed out")
        except aiohttp.ClientError as e:
            await inter.edit_original_response(f"❌ Network Error: {str(e)}")
        except Exception as e:
            self.logger.error(f"Image generation failed: {e}", exc_info=True)
            await inter.edit_original_response("❌ Failed to generate image")
    def cog_unload(self):
        """Cleanup on cog unload"""
        self.logger.info("DALLECog unloaded")


def setup(bot: commands.Bot):
    bot.add_cog(DALLECog(bot))