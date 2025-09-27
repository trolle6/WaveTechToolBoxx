import aiohttp
import disnake
from disnake.ext import commands
import logging
import time
from typing import Any
import asyncio
import json


class DALLECog(commands.Cog):
    """Enhanced DALL-E Image Generation with Proper Rate Limiting"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger('dalle_cog')
        self.config = bot.config
        self.api_key = self.config.openai_api_key

        # DALL-E config - using TTS bearer token
        self.api_url = self.config.dalle.api_url
        self.api_key = self.config.tts.bearer_token  # Use TTS bearer token
        self.size = self.config.dalle.size
        self.rate_limit = 5  # requests per minute per user
        self.last_requests = {}

        # Initialize http_session to None
        self.http_session = None  # ← ADD THIS LINE

    def _check_rate_limit(self, user_id: int) -> bool:
        """Check if user is rate limited"""
        now = time.time()
        user_key = str(user_id)

        if user_key not in self.last_requests:
            self.last_requests[user_key] = []
            return True

        # Remove old requests (older than 60 seconds)
        self.last_requests[user_key] = [
            t for t in self.last_requests[user_key]
            if now - t < 60
        ]

        if len(self.last_requests[user_key]) < self.rate_limit:
            self.last_requests[user_key].append(now)
            return True

        return False

    async def _get_session(self):
        """Get or create HTTP session"""
        if self.http_session is None or self.http_session.closed:
            self.http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self.http_session

    @commands.slash_command(name="generate_image", description="Generate image with DALL-E")
    async def generate_image(self, inter: disnake.ApplicationCommandInteraction, prompt: str):
        """Generate image from text prompt"""
        if not self.api_key:
            await inter.send("DALL-E not configured - missing API key", ephemeral=True)
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
                "model": "dall-e-3",
                "prompt": prompt,
                "n": 1,
                "size": self.size,
                "quality": "standard",
                "response_format": "url"
            }

            async with session.post(
                    self.api_url,
                    headers=headers,
                    json=data,
                    timeout=30
            ) as response:
                response_text = await response.text()

                if response.status != 200:
                    self.logger.error(f"DALL-E API error: {response_text}")

                    # Try to parse the error response
                    try:
                        error_data = json.loads(response_text)
                        error_message = error_data.get("error", {}).get("message", "Unknown error")
                        error_code = error_data.get("error", {}).get("code", "unknown")

                        if "content_policy_violation" in error_code or "safety system" in error_message:
                            await inter.edit_original_response(
                                "❌ Your prompt was rejected by the safety system. "
                                "Please try a different prompt that doesn't violate content policies."
                            )
                        elif "Incorrect API key" in error_message:
                            await inter.edit_original_response(
                                "❌ API Key Error: The provided API key is invalid. "
                                "Please check your configuration."
                            )
                        else:
                            await inter.edit_original_response(f"❌ API Error: {error_message}")
                    except json.JSONDecodeError:
                        await inter.edit_original_response(f"❌ API Error: {response.status}")
                    return

                result = json.loads(response_text)

            if not result.get("data"):
                await inter.edit_original_response("❌ No image generated")
                return

            image_url = result["data"][0]["url"]

            # Create an embed with the image
            embed = disnake.Embed(
                title="DALL-E Image Generation",
                description=f"Prompt: {prompt[:200]}{'...' if len(prompt) > 200 else ''}",
                color=disnake.Color.blue()
            )
            embed.set_image(url=image_url)
            embed.set_footer(text=f"Requested by {inter.author.display_name}")

            await inter.edit_original_response(embed=embed)

        except asyncio.TimeoutError:
            await inter.edit_original_response("❌ Request timed out")
        except aiohttp.ClientError as e:
            await inter.edit_original_response(f"❌ Network Error: {str(e)}")
        except Exception as e:
            self.logger.error(f"Image generation failed: {e}", exc_info=True)
            await inter.edit_original_response("❌ Failed to generate image")

    async def cog_unload(self):
        """Cleanup on cog unload"""
        if hasattr(self, 'http_session') and self.http_session and not self.http_session.closed:
            await self.http_session.close()
        self.logger.info("DALLECog unloaded")


def setup(bot: commands.Bot):
    bot.add_cog(DALLECog(bot))