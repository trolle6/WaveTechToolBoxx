import aiohttp
import disnake
from disnake.ext import commands
import logging
import time
import asyncio
import json
import os
from typing import Any, Dict


class DALLECog(commands.Cog):
    """Optimized DALL-E Image Generation with Efficient Rate Limiting"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger('dalle_cog')
        self.config = bot.config

        # Configuration
        self.api_url = self.config.dalle.api_url
        self.api_key = self.config.tts.bearer_token
        self.size = self.config.dalle.size
        self.rate_limit = 5
        self.last_requests = {}

        # Session management
        self.http_session = None
        self.session_lock = asyncio.Lock()
        self.max_retries = 3
        self.retry_delay = 1.0

    def _check_rate_limit(self, user_id: int) -> bool:
        """Efficient rate limiting with automatic cleanup"""
        now = time.time()
        user_key = str(user_id)

        # Initialize or cleanup user request history
        if user_key not in self.last_requests:
            self.last_requests[user_key] = []
        else:
            # Remove requests older than 60 seconds
            self.last_requests[user_key] = [t for t in self.last_requests[user_key] if now - t < 60]

        # Periodic cleanup of inactive users (every minute)
        if int(now) % 60 == 0:
            inactive_users = [uid for uid, times in self.last_requests.items()
                              if not times or now - max(times) > 300]
            for uid in inactive_users:
                del self.last_requests[uid]

        # Check if user is within rate limit
        if len(self.last_requests[user_key]) < self.rate_limit:
            self.last_requests[user_key].append(now)
            return True
        return False

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session with connection pooling"""
        async with self.session_lock:
            if self.http_session is None or self.http_session.closed:
                timeout = aiohttp.ClientTimeout(total=30)
                connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
                self.http_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
            return self.http_session

    async def _call_dalle_api(self, prompt: str) -> Dict[str, Any]:
        """Make optimized API call with comprehensive error handling"""
        session = await self._get_session()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        data = {
            "model": "dall-e-3", "prompt": prompt, "n": 1,
            "size": self.size, "quality": "standard", "response_format": "url"
        }

        for attempt in range(self.max_retries):
            try:
                async with session.post(self.api_url, headers=headers, json=data,
                                        timeout=aiohttp.ClientTimeout(total=30)) as response:

                    if response.status == 200:
                        return await response.json()

                    # Handle specific HTTP errors
                    error_text = await response.text()
                    if response.status == 429 and attempt < self.max_retries - 1:
                        await asyncio.sleep((attempt + 1) * self.retry_delay)
                        continue

                    # Parse error response
                    try:
                        error_data = json.loads(error_text)
                        error_msg = error_data.get("error", {}).get("message", "Unknown error")
                        error_code = error_data.get("error", {}).get("code", "unknown")
                        return {"error": error_msg, "code": error_code, "status": response.status}
                    except json.JSONDecodeError:
                        return {"error": f"HTTP {response.status}: {error_text}", "status": response.status}

            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                    continue
                error_type = "Timeout" if isinstance(e, asyncio.TimeoutError) else "Network"
                return {"error": f"{error_type} error: {str(e)}"}
            except Exception as e:
                self.logger.error(f"Unexpected error: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                    continue
                return {"error": f"Unexpected error: {str(e)}"}

        return {"error": "All retry attempts failed"}

    @commands.slash_command(name="generate_image", description="Generate image with DALL-E")
    async def generate_image(self, inter: disnake.ApplicationCommandInteraction, prompt: str):
        """Generate image from text prompt with validation and rate limiting"""
        # Validate configuration and input
        if not self.api_key or self.api_key == "your_tts_bearer_token_here":
            return await inter.send("❌ DALL-E is not properly configured.", ephemeral=True)

        prompt = prompt.strip()
        if not prompt:
            return await inter.send("❌ Please provide a prompt.", ephemeral=True)
        if len(prompt) > 1000:
            return await inter.send("❌ Prompt too long (max 1000 characters)", ephemeral=True)

        # Rate limiting check
        if not self._check_rate_limit(inter.author.id):
            remaining = 60 - int(time.time() - max(self.last_requests[str(inter.author.id)]))
            return await inter.send(f"❌ Rate limit exceeded. Wait {remaining}s.", ephemeral=True)

        await inter.response.defer()

        try:
            self.logger.info(f"DALL-E request from {inter.author}: {prompt[:50]}...")
            result = await self._call_dalle_api(prompt)

            if "error" in result:
                error_msg, error_code = result["error"], result.get("code", "")

                # User-friendly error messages
                error_responses = {
                    "content_policy": "❌ **Content Policy Violation**\nYour prompt was rejected. Avoid harmful content.",
                    "billing_limit": "❌ **Billing Limit Reached**\nContact administrator.",
                    "invalid_key": "❌ **Invalid API Key**\nContact administrator.",
                    "rate_limit": "❌ **OpenAI Rate Limit**\nTry again in a few minutes."
                }

                response_key = (
                    "content_policy" if "content_policy" in error_code or "safety system" in error_msg.lower()
                    else "billing_limit" if "billing_hard_limit" in error_code
                    else "invalid_key" if "invalid_api_key" in error_code or "incorrect api key" in error_msg.lower()
                    else "rate_limit" if result.get("status") == 429
                    else None)

                await inter.edit_original_response(error_responses.get(response_key, f"❌ **API Error**: {error_msg}"))
                return

            if not result.get("data"):
                return await inter.edit_original_response("❌ No image data received from API")

            # Create embed with generated image
            embed = disnake.Embed(
                title="🎨 DALL-E Image Generation",
                description=f"**Prompt:** {prompt[:200]}{'...' if len(prompt) > 200 else ''}",
                color=disnake.Color.blue(),
                timestamp=disnake.utils.utcnow()
            )
            embed.set_image(url=result["data"][0]["url"])
            embed.set_footer(text=f"Requested by {inter.author.display_name}",
                             icon_url=inter.author.display_avatar.url)
            embed.add_field(name="💡 Tips", value="Use detailed descriptions for better results!", inline=False)

            await inter.edit_original_response(embed=embed)
            self.logger.info(f"Successfully generated image for {inter.author}")

        except Exception as e:
            self.logger.error(f"Unexpected error: {e}", exc_info=True)
            try:
                await inter.edit_original_response("❌ **Unexpected Error**\nPlease try again later.")
            except:
                pass  # Interaction already handled

    @commands.slash_command(name="dalle_stats", description="Check DALL-E usage statistics")
    async def dalle_stats(self, inter: disnake.ApplicationCommandInteraction):
        """Display DALL-E usage statistics"""
        embed = disnake.Embed(title="DALL-E Usage Statistics", color=disnake.Color.green(),
                              timestamp=disnake.utils.utcnow())

        active_users = len(self.last_requests)
        recent_requests = sum(len(times) for times in self.last_requests.values())

        stats_data = [
            ("Active Users", active_users, True),
            ("Recent Requests", recent_requests, True),
            ("Rate Limit", f"{self.rate_limit}/min per user", True),
            (
            "Session Status", "✅ Connected" if self.http_session and not self.http_session.closed else "❌ Disconnected",
            True)
        ]

        for name, value, inline in stats_data:
            embed.add_field(name=name, value=value, inline=inline)

        await inter.response.send_message(embed=embed, ephemeral=True)

    async def cog_unload(self):
        """Cleanup resources on cog unload"""
        self.logger.info("DALLECog unloading...")
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
            self.logger.info("DALL-E HTTP session closed")
        self.logger.info("DALLECog unloaded successfully")


def setup(bot: commands.Bot):
    bot.add_cog(DALLECog(bot))