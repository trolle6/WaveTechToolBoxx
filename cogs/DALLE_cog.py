import aiohttp
import disnake
from disnake.ext import commands
import logging
import time
from typing import Any
import asyncio
import json
import os


class DALLECog(commands.Cog):
    """Enhanced DALL-E Image Generation with Proper Rate Limiting"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger('dalle_cog')
        self.config = bot.config

        # DALL-E config - using TTS bearer token
        self.api_url = self.config.dalle.api_url
        self.api_key = self.config.tts.bearer_token  # Use TTS bearer token
        self.size = self.config.dalle.size
        self.rate_limit = 5  # requests per minute per user
        self.last_requests = {}

        # Enhanced session management
        self.http_session = None
        self.session_lock = asyncio.Lock()
        self.max_retries = 3
        self.retry_delay = 1.0

    def _check_rate_limit(self, user_id: int) -> bool:
        """Enhanced rate limiting with cleanup"""
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

        # Clean up old users to prevent memory leaks
        if now % 60 < 1:  # Cleanup every minute
            expired_users = [
                uid for uid, timestamps in self.last_requests.items()
                if not timestamps or now - max(timestamps) > 300  # 5 minutes
            ]
            for uid in expired_users:
                del self.last_requests[uid]

        if len(self.last_requests[user_key]) < self.rate_limit:
            self.last_requests[user_key].append(now)
            return True

        return False

    async def _get_session(self):
        """Get or create HTTP session with proper cleanup"""
        async with self.session_lock:
            if self.http_session is None or self.http_session.closed:
                timeout = aiohttp.ClientTimeout(total=45)  # Increased timeout
                connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
                self.http_session = aiohttp.ClientSession(
                    timeout=timeout,
                    connector=connector
                )
            return self.http_session

    async def _call_dalle_api(self, prompt: str) -> Any:
        """Make API call with retry logic"""
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

        for attempt in range(self.max_retries):
            try:
                async with session.post(
                        self.api_url,
                        headers=headers,
                        json=data,
                        timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    response_text = await response.text()

                    if response.status == 200:
                        return json.loads(response_text)

                    # Handle specific errors
                    if response.status == 429:  # Rate limited
                        if attempt < self.max_retries - 1:
                            wait_time = (attempt + 1) * self.retry_delay
                            self.logger.warning(f"Rate limited, retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue

                    # Parse error response
                    try:
                        error_data = json.loads(response_text)
                        error_message = error_data.get("error", {}).get("message", "Unknown error")
                        error_code = error_data.get("error", {}).get("code", "unknown")
                        return {"error": error_message, "code": error_code, "status": response.status}
                    except json.JSONDecodeError:
                        return {"error": f"HTTP {response.status}: {response_text}", "status": response.status}

            except asyncio.TimeoutError:
                if attempt < self.max_retries - 1:
                    self.logger.warning(f"Timeout on attempt {attempt + 1}, retrying...")
                    await asyncio.sleep(self.retry_delay)
                    continue
                return {"error": "Request timeout after multiple retries"}
            except aiohttp.ClientError as e:
                if attempt < self.max_retries - 1:
                    self.logger.warning(f"Network error on attempt {attempt + 1}: {e}, retrying...")
                    await asyncio.sleep(self.retry_delay)
                    continue
                return {"error": f"Network error: {str(e)}"}
            except Exception as e:
                self.logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                    continue
                return {"error": f"Unexpected error: {str(e)}"}

        return {"error": "All retry attempts failed"}

    @commands.slash_command(name="generate_image", description="Generate image with DALL-E")
    async def generate_image(self, inter: disnake.ApplicationCommandInteraction, prompt: str):
        """Generate image from text prompt"""
        # Validate API configuration first
        if not self.api_key or self.api_key == "your_tts_bearer_token_here":
            await inter.send("❌ DALL-E is not properly configured. Please check the API key.", ephemeral=True)
            return

        # Validate input
        if len(prompt.strip()) == 0:
            await inter.send("❌ Please provide a prompt.", ephemeral=True)
            return

        if len(prompt) > 1000:
            await inter.send("❌ Prompt too long (max 1000 characters)", ephemeral=True)
            return

        # Rate limiting
        if not self._check_rate_limit(inter.author.id):
            remaining = 60 - int(time.time() - max(self.last_requests[str(inter.author.id)]))
            await inter.send(
                f"❌ Rate limit exceeded. Please wait {remaining} seconds before making another request.",
                ephemeral=True
            )
            return

        await inter.response.defer()

        try:
            self.logger.info(f"DALL-E request from {inter.author} (ID: {inter.author.id}): {prompt[:50]}...")

            result = await self._call_dalle_api(prompt)

            if "error" in result:
                error_msg = result["error"]
                error_code = result.get("code", "")

                # User-friendly error messages
                if "content_policy_violation" in error_code or "safety system" in error_msg.lower():
                    await inter.edit_original_response(
                        "❌ **Content Policy Violation**\n"
                        "Your prompt was rejected by the safety system. Please:\n"
                        "• Avoid violent, adult, or harmful content\n"
                        "• Use more descriptive, positive language\n"
                        "• Try a different approach to your idea"
                    )
                elif "billing_hard_limit" in error_code:
                    await inter.edit_original_response(
                        "❌ **Billing Limit Reached**\n"
                        "The DALL-E API billing limit has been reached. Please contact the bot administrator."
                    )
                elif "invalid_api_key" in error_code or "incorrect api key" in error_msg.lower():
                    await inter.edit_original_response(
                        "❌ **Invalid API Key**\n"
                        "The DALL-E API key is invalid or expired. Please contact the bot administrator."
                    )
                elif result.get("status") == 429:
                    await inter.edit_original_response(
                        "❌ **OpenAI Rate Limit**\n"
                        "The service is currently rate limited. Please try again in a few minutes."
                    )
                else:
                    await inter.edit_original_response(f"❌ **API Error**: {error_msg}")
                return

            if not result.get("data"):
                await inter.edit_original_response("❌ No image data received from API")
                return

            image_url = result["data"][0]["url"]

            # Create an embed with the image
            embed = disnake.Embed(
                title="🎨 DALL-E Image Generation",
                description=f"**Prompt:** {prompt[:200]}{'...' if len(prompt) > 200 else ''}",
                color=disnake.Color.blue(),
                timestamp=disnake.utils.utcnow()
            )
            embed.set_image(url=image_url)
            embed.set_footer(
                text=f"Requested by {inter.author.display_name}",
                icon_url=inter.author.display_avatar.url
            )

            # Add some useful info
            embed.add_field(
                name="💡 Tips",
                value="Use `/generate_image` with detailed descriptions for better results!",
                inline=False
            )

            await inter.edit_original_response(embed=embed)
            self.logger.info(f"Successfully generated image for {inter.author}")

        except Exception as e:
            self.logger.error(f"Unexpected error in generate_image: {e}", exc_info=True)
            try:
                await inter.edit_original_response(
                    "❌ **Unexpected Error**\n"
                    "An unexpected error occurred. Please try again later."
                )
            except:
                pass  # Already responded or interaction expired

    @commands.slash_command(name="dalle_stats", description="Check DALL-E usage statistics")
    async def dalle_stats(self, inter: disnake.ApplicationCommandInteraction):
        """Show DALL-E usage statistics"""
        embed = disnake.Embed(
            title="DALL-E Usage Statistics",
            color=disnake.Color.green(),
            timestamp=disnake.utils.utcnow()
        )

        active_users = len(self.last_requests)
        recent_requests = sum(len(times) for times in self.last_requests.values())

        embed.add_field(name="Active Users", value=str(active_users), inline=True)
        embed.add_field(name="Recent Requests", value=str(recent_requests), inline=True)
        embed.add_field(name="Rate Limit", value=f"{self.rate_limit}/min per user", inline=True)

        if self.http_session and not self.http_session.closed:
            embed.add_field(name="Session Status", value="✅ Connected", inline=True)
        else:
            embed.add_field(name="Session Status", value="❌ Disconnected", inline=True)

        await inter.response.send_message(embed=embed, ephemeral=True)

    async def cog_unload(self):
        """Enhanced cleanup on cog unload"""
        self.logger.info("DALLECog unloading...")

        if hasattr(self, 'http_session') and self.http_session and not self.http_session.closed:
            try:
                await self.http_session.close()
                self.logger.info("DALL-E HTTP session closed")
            except Exception as e:
                self.logger.error(f"Error closing HTTP session: {e}")

        self.logger.info("DALLECog unloaded successfully")


def setup(bot: commands.Bot):
    bot.add_cog(DALLECog(bot))