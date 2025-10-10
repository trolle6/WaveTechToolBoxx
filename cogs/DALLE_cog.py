import asyncio

import disnake
from disnake.ext import commands

from . import utils


class DALLECog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rate_limiter = utils.RateLimiter(5, 60)
        self.breaker = utils.CircuitBreaker()

    async def _call_api(self, prompt: str) -> dict:
        """Call DALL-E with circuit breaker"""
        if not self.breaker.can_attempt():
            return {"error": "Service temporarily unavailable"}

        headers = {"Authorization": f"Bearer {self.bot.config.TTS_BEARER_TOKEN}"}
        data = {
            "model": "dall-e-3",
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
        }

        for attempt in range(3):
            try:
                session = await self.bot.http_mgr.get_session()
                async with session.post(
                    "https://api.openai.com/v1/images/generations",
                    json=data,
                    headers=headers,
                ) as r:
                    if r.status == 200:
                        self.breaker.record_success()
                        return await r.json()
                    if r.status == 429 and attempt < 2:
                        self.breaker.record_failure()
                        await asyncio.sleep(2**attempt)
                        continue
                    self.breaker.record_failure()
                    return {"error": f"API error: {r.status}"}

            except asyncio.TimeoutError:
                if attempt == 2:
                    self.breaker.record_failure()
                    return {"error": "Request timeout"}
                await asyncio.sleep(1)

        return {"error": "Max retries exceeded"}

    @commands.slash_command(name="generate_image")
    async def generate(
        self, inter: disnake.ApplicationCommandInteraction, prompt: str
    ):
        if not self.rate_limiter.check(str(inter.author.id)):
            await inter.send("Rate limited. Try again later.", ephemeral=True)
            return

        if not prompt or len(prompt) > 1000:
            await inter.send("Invalid prompt (1-1000 chars)", ephemeral=True)
            return

        await inter.response.defer()
        result = await self._call_api(prompt)

        if "error" in result:
            await inter.edit_original_response(f"Error: {result['error']}")
            return

        if result.get("data"):
            embed = disnake.Embed(
                title="DALL-E Generation",
                description=prompt[:200],
                color=disnake.Color.blue(),
            )
            embed.set_image(url=result["data"][0]["url"])
            await inter.edit_original_response(embed=embed)


def setup(bot):
    bot.add_cog(DALLECog(bot))