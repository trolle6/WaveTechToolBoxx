"""
Enhanced DALL-E Image Generation Cog for Discord Bot
High-quality image generation with improved reliability and user experience
"""

import asyncio
import hashlib
import time
import aiohttp
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from enum import Enum

import disnake
from disnake.ext import commands


class ImageSize(Enum):
    """Available DALL-E image sizes"""
    SQUARE_1024 = ("1024x1024", "Square (1024x1024)")
    LANDSCAPE_1792 = ("1792x1024", "Landscape (1792x1024)")
    PORTRAIT_1024 = ("1024x1792", "Portrait (1024x1792)")


class ImageQuality(Enum):
    """DALL-E image quality settings"""
    STANDARD = ("standard", "Standard Quality")
    HD = ("hd", "HD Quality (slower)")


@dataclass
class GenerationRequest:
    """Represents an image generation request"""
    user_id: int
    prompt: str
    size: str
    quality: str
    timestamp: float
    interaction: disnake.ApplicationCommandInteraction


class SimpleRateLimiter:
    """Simple rate limiter for basic throttling"""

    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self.requests = {}

    async def check(self, user_id: str) -> bool:
        now = time.time()
        user_requests = self.requests.get(user_id, [])

        # Clean old requests
        user_requests = [req_time for req_time in user_requests if now - req_time < self.window]

        if len(user_requests) >= self.limit:
            return False

        user_requests.append(now)
        self.requests[user_id] = user_requests
        return True

    async def time_until_available(self, user_id: str) -> float:
        now = time.time()
        user_requests = self.requests.get(user_id, [])
        user_requests = [req_time for req_time in user_requests if now - req_time < self.window]

        if len(user_requests) < self.limit:
            return 0

        oldest_request = min(user_requests)
        return self.window - (now - oldest_request)


class SimpleCache:
    """Simple in-memory cache with TTL"""

    def __init__(self, max_size: int = 50, ttl: int = 3600):
        self.max_size = max_size
        self.ttl = ttl
        self.cache = {}
        self.access_times = {}
        self.hits = 0
        self.misses = 0

    async def get(self, key: str):
        if key in self.cache:
            item_time, value = self.cache[key]
            if time.time() - item_time < self.ttl:
                self.access_times[key] = time.time()
                self.hits += 1
                return value
            else:
                # Expired
                del self.cache[key]
                del self.access_times[key]

        self.misses += 1
        return None

    async def set(self, key: str, value):
        # Remove oldest if at capacity
        if len(self.cache) >= self.max_size:
            oldest_key = min(self.access_times.items(), key=lambda x: x[1])[0]
            del self.cache[oldest_key]
            del self.access_times[oldest_key]

        self.cache[key] = (time.time(), value)
        self.access_times[key] = time.time()

    async def get_stats(self):
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        return {
            'size': len(self.cache),
            'max_size': self.max_size,
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': hit_rate
        }


class ImageGenerationQueue:
    """Manages image generation queue with priority support"""

    def __init__(self, max_size: int = 50):
        self.queue = asyncio.PriorityQueue(maxsize=max_size)
        self.processing = False
        self.current_request: Optional[GenerationRequest] = None

    async def add(self, request: GenerationRequest, priority: int = 5) -> bool:
        """Add request to queue. Lower priority number = higher priority"""
        try:
            await self.queue.put((priority, time.time(), request))
            return True
        except asyncio.QueueFull:
            return False

    async def get(self) -> Optional[GenerationRequest]:
        """Get next request from queue"""
        try:
            priority, timestamp, request = await self.queue.get()
            return request
        except asyncio.QueueEmpty:
            return None

    def size(self) -> int:
        """Get current queue size"""
        return self.queue.qsize()


class DALLECog(commands.Cog):
    """Enhanced DALL-E image generation cog with high-quality features"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("dalle")

        # Rate limiting
        self.rate_limiter = SimpleRateLimiter(limit=10, window=60)
        self.premium_rate_limiter = SimpleRateLimiter(limit=20, window=60)

        # Image URL cache (DALL-E URLs expire after ~1 hour)
        self.url_cache = SimpleCache(max_size=50, ttl=3000)

        # Generation queue
        self.queue = ImageGenerationQueue(max_size=50)
        self.queue_processor_task = None

        # Configuration
        self.api_url = "https://api.openai.com/v1/images/generations"
        self.default_model = "dall-e-3"

        # Statistics
        self.stats = {
            "total_requests": 0,
            "successful_generations": 0,
            "failed_generations": 0,
            "cache_hits": 0,
            "total_wait_time": 0.0
        }

        # User preferences storage
        self.user_preferences: Dict[int, Dict] = {}

    async def cog_load(self):
        """Initialize cog resources"""
        self.queue_processor_task = asyncio.create_task(self._process_queue())
        self.logger.info("DALL-E Cog loaded successfully")

    async def cog_unload(self):
        """Clean up cog resources"""
        if self.queue_processor_task:
            self.queue_processor_task.cancel()
            try:
                await self.queue_processor_task
            except asyncio.CancelledError:
                pass
        self.logger.info("DALL-E Cog unloaded")

    def _generate_cache_key(self, prompt: str, size: str, quality: str) -> str:
        """Generate cache key for prompt"""
        content = f"{prompt}:{size}:{quality}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _is_premium_user(self, user_id: int) -> bool:
        """Check if user has premium access"""
        # Implement your premium logic here
        return False

    def _enhance_prompt(self, prompt: str) -> str:
        """Enhance prompt for better DALL-E 3 results"""
        prompt = " ".join(prompt.split())

        # Quality enhancement keywords
        enhancement_keywords = [
            "masterpiece", "best quality", "4k", "8k", "ultra detailed",
            "professional", "award winning", "trending on artstation"
        ]

        # Check if prompt already has quality indicators
        has_quality = any(keyword in prompt.lower() for keyword in enhancement_keywords)

        # Add enhancement if there's room and it's needed
        if not has_quality and len(prompt) < 3900:
            enhancements = "masterpiece, best quality, ultra detailed, professional"
            prompt = f"{prompt}, {enhancements}"

        return prompt

    def _get_style_suggestions(self, prompt: str) -> List[str]:
        """Get style suggestions based on prompt content"""
        styles = []

        prompt_lower = prompt.lower()

        # Artistic styles
        if any(word in prompt_lower for word in ['painting', 'art', 'drawing']):
            if 'digital' in prompt_lower:
                styles.append("digital painting")
            elif 'oil' in prompt_lower:
                styles.append("oil painting")
            elif 'watercolor' in prompt_lower:
                styles.append("watercolor painting")
            else:
                styles.append("concept art")

        # Photography styles
        if any(word in prompt_lower for word in ['photo', 'photograph', 'camera']):
            styles.append("professional photography")
            if 'portrait' in prompt_lower:
                styles.append("studio lighting")
            if 'landscape' in prompt_lower:
                styles.append("wide angle lens")

        # Digital media
        if any(word in prompt_lower for word in ['3d', 'render', 'cg']):
            styles.append("3D render")
            styles.append("octane render")

        return styles

    async def _call_dalle_api(
            self,
            prompt: str,
            size: str = "1024x1024",
            quality: str = "hd",  # Default to HD for better quality
            model: str = "dall-e-3"
    ) -> Dict:
        """Call DALL-E API with enhanced error handling and retries"""

        headers = {
            "Authorization": f"Bearer {self.bot.config.OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "url",
            "style": "vivid"  # More dramatic and hyper-realistic
        }

        self.stats["total_requests"] += 1

        for attempt in range(3):
            try:
                timeout = aiohttp.ClientTimeout(total=45)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                            self.api_url,
                            json=payload,
                            headers=headers
                    ) as response:

                        if response.status == 200:
                            result = await response.json()
                            self.stats["successful_generations"] += 1
                            return result

                        elif response.status == 429:
                            # Rate limited
                            retry_after = response.headers.get('Retry-After', '60')

                            if attempt < 2:
                                wait_time = min(int(retry_after), 30)
                                self.logger.warning(f"Rate limited, waiting {wait_time}s (attempt {attempt + 1})")
                                await asyncio.sleep(wait_time)
                                continue

                            return {"error": f"Rate limited. Try again in {retry_after} seconds."}

                        elif response.status == 400:
                            # Bad request
                            error_data = await response.json()
                            error_message = error_data.get("error", {}).get("message", "Bad request")

                            if "content_policy" in error_message.lower():
                                return {"error": "🚫 Content policy violation. Please modify your prompt to be more appropriate."}

                            return {"error": f"Invalid request: {error_message}"}

                        elif response.status == 401:
                            return {"error": "🔑 API authentication failed. Please contact an administrator."}

                        else:
                            error_text = await response.text()

                            if attempt < 2:
                                await asyncio.sleep(2 ** attempt)
                                continue

                            return {"error": f"API error {response.status}: {error_text[:100]}"}

            except asyncio.TimeoutError:
                self.logger.warning(f"DALL-E timeout on attempt {attempt + 1}")
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return {"error": "⏰ Request timeout. The API is taking too long to respond."}

            except Exception as e:
                self.logger.error(f"DALL-E API error: {e}")
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return {"error": f"Unexpected error: {str(e)[:100]}"}

        self.stats["failed_generations"] += 1
        return {"error": "Maximum retries exceeded. Please try again later."}

    async def _process_queue(self):
        """Process generation queue"""
        try:
            while True:
                try:
                    # Wait for next request
                    request = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=60
                    )

                    if not request:
                        continue

                    self.queue.current_request = request

                    # Check if request is stale (older than 5 minutes)
                    if time.time() - request.timestamp > 300:
                        try:
                            await request.interaction.edit_original_response(
                                content="⏰ Request timed out. Please try again."
                            )
                        except Exception:
                            pass
                        continue

                    # Update status
                    try:
                        embed = disnake.Embed(
                            title="🎨 Generating Your Image",
                            description="**DALL-E 3** is creating your masterpiece...",
                            color=disnake.Color.blue()
                        )
                        embed.add_field(
                            name="Prompt",
                            value=f"```{request.prompt[:100]}{'...' if len(request.prompt) > 100 else ''}```",
                            inline=False
                        )
                        embed.add_field(
                            name="Quality",
                            value=request.quality.upper(),
                            inline=True
                        )
                        embed.add_field(
                            name="Size",
                            value=request.size,
                            inline=True
                        )
                        embed.set_footer(text="This may take 15-30 seconds for HD quality")

                        await request.interaction.edit_original_response(embed=embed)
                    except Exception:
                        continue

                    # Generate image
                    start_time = time.time()
                    result = await self._call_dalle_api(
                        request.prompt,
                        request.size,
                        request.quality
                    )
                    generation_time = time.time() - start_time
                    self.stats["total_wait_time"] += generation_time

                    # Cache successful results
                    if "data" in result and result["data"]:
                        cache_key = self._generate_cache_key(request.prompt, request.size, request.quality)
                        await self.url_cache.set(cache_key, result["data"][0]["url"])

                    # Send result
                    await self._send_generation_result(
                        request.interaction,
                        request.prompt,
                        result,
                        generation_time
                    )

                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.logger.error(f"Queue processing error: {e}")
                    await asyncio.sleep(1)
                finally:
                    self.queue.current_request = None

        except asyncio.CancelledError:
            self.logger.debug("Queue processor cancelled")

    async def _send_generation_result(
            self,
            interaction: disnake.ApplicationCommandInteraction,
            prompt: str,
            result: Dict,
            generation_time: float
    ):
        """Send generation result to user"""
        try:
            if "error" in result:
                embed = disnake.Embed(
                    title="❌ Generation Failed",
                    description=result["error"],
                    color=disnake.Color.red()
                )
                embed.set_footer(text=f"Time: {generation_time:.1f}s")
                await interaction.edit_original_response(embed=embed)

            elif result.get("data"):
                image_url = result["data"][0]["url"]

                # Create success embed
                embed = disnake.Embed(
                    title="🎨 Image Generated Successfully!",
                    description=f"**Prompt:** {prompt[:200]}{'...' if len(prompt) > 200 else ''}",
                    color=disnake.Color.green()
                )
                embed.set_image(url=image_url)
                embed.add_field(
                    name="Generation Time",
                    value=f"{generation_time:.1f} seconds",
                    inline=True
                )
                embed.add_field(
                    name="Model",
                    value="DALL-E 3",
                    inline=True
                )
                embed.add_field(
                    name="Quality",
                    value="HD" if "hd" in prompt.lower() else "Standard",
                    inline=True
                )
                embed.set_footer(
                    text="💡 Tip: Use specific details and artistic styles for even better results!"
                )

                # Add style suggestions for future prompts
                style_suggestions = self._get_style_suggestions(prompt)
                if style_suggestions:
                    embed.add_field(
                        name="🎨 Style Suggestions",
                        value=", ".join(style_suggestions[:3]),
                        inline=False
                    )

                await interaction.edit_original_response(embed=embed)

            else:
                await interaction.edit_original_response(
                    content="❌ No image data received from API"
                )

        except Exception as e:
            self.logger.error(f"Failed to send result: {e}")
            try:
                await interaction.edit_original_response(
                    content="❌ Failed to send result. Please try again."
                )
            except Exception:
                pass

    @commands.slash_command(name="imagine", description="Generate an image with DALL-E 3")
    async def imagine(
            self,
            inter: disnake.ApplicationCommandInteraction,
            prompt: str = commands.Param(description="Describe the image you want to generate", max_length=4000),
            size: str = commands.Param(
                default="1024x1024",
                choices=["1024x1024", "1792x1024", "1024x1792"],
                description="Image size and orientation"
            ),
            quality: str = commands.Param(
                default="hd",
                choices=["standard", "hd"],
                description="Image quality (HD recommended for best results)"
            ),
            private: bool = commands.Param(
                default=False,
                description="Make response visible only to you"
            )
    ):
        """Generate high-quality images using DALL-E 3"""

        await inter.response.defer(ephemeral=private)

        user_id = str(inter.author.id)
        is_premium = self._is_premium_user(inter.author.id)

        # Check rate limit
        rate_limiter = self.premium_rate_limiter if is_premium else self.rate_limiter
        if not await rate_limiter.check(user_id):
            wait_time = await rate_limiter.time_until_available(user_id)

            embed = disnake.Embed(
                title="⏳ Rate Limited",
                description=f"Please wait {wait_time:.0f} seconds before generating another image.",
                color=disnake.Color.orange()
            )
            embed.add_field(
                name="Limits",
                value=f"{'Premium' if is_premium else 'Standard'}: "
                      f"{rate_limiter.limit} images per minute",
                inline=False
            )

            await inter.edit_original_response(embed=embed)
            return

        # Validate prompt
        if len(prompt.strip()) < 3:
            await inter.edit_original_response(
                content="❌ Prompt too short. Please provide a more detailed description (at least 3 characters)."
            )
            return

        # Enhance prompt for better results
        enhanced_prompt = self._enhance_prompt(prompt)

        # Check cache
        cache_key = self._generate_cache_key(enhanced_prompt, size, quality)
        cached_url = await self.url_cache.get(cache_key)

        if cached_url:
            self.stats["cache_hits"] += 1

            embed = disnake.Embed(
                title="🎨 Image (Cached Result)",
                description=f"**Prompt:** {prompt[:200]}{'...' if len(prompt) > 200 else ''}",
                color=disnake.Color.blue()
            )
            embed.set_image(url=cached_url)
            embed.set_footer(text="⚡ This image was retrieved from cache for faster delivery")

            await inter.edit_original_response(embed=embed)
            return

        # Create and queue request
        request = GenerationRequest(
            user_id=inter.author.id,
            prompt=enhanced_prompt,
            size=size,
            quality=quality,
            timestamp=time.time(),
            interaction=inter
        )

        # Priority: premium users get higher priority
        priority = 1 if is_premium else 5

        if not await self.queue.add(request, priority):
            await inter.edit_original_response(
                content="❌ Generation queue is full. Please try again in a few minutes."
            )
            return

        queue_position = self.queue.size()

        # Send queue status
        embed = disnake.Embed(
            title="⏳ Added to Generation Queue",
            description="Your image is in the queue and will be processed shortly.",
            color=disnake.Color.blue()
        )
        embed.add_field(
            name="Queue Position",
            value=f"#{queue_position}",
            inline=True
        )
        embed.add_field(
            name="Estimated Wait",
            value=f"~{queue_position * 30}s",
            inline=True
        )
        embed.add_field(
            name="Prompt Preview",
            value=f"```{prompt[:50]}{'...' if len(prompt) > 50 else ''}```",
            inline=False
        )
        embed.set_footer(text="You'll be notified when generation starts")

        await inter.edit_original_response(embed=embed)

    @commands.slash_command(name="dalle")
    async def dalle_group(self, inter: disnake.ApplicationCommandInteraction):
        """DALL-E image generation commands"""
        pass

    @dalle_group.sub_command(name="stats", description="View DALL-E usage statistics")
    async def dalle_stats(self, inter: disnake.ApplicationCommandInteraction):
        """Show DALL-E usage statistics"""
        await inter.response.defer(ephemeral=True)

        cache_stats = await self.url_cache.get_stats()

        embed = disnake.Embed(
            title="📊 DALL-E Statistics",
            color=disnake.Color.blue()
        )

        # Usage stats
        success_rate = (self.stats["successful_generations"] /
                       max(1, self.stats["total_requests"])) * 100
        avg_time = (self.stats["total_wait_time"] /
                   max(1, self.stats["successful_generations"]))

        embed.add_field(
            name="🚀 Generation Stats",
            value=f"• Total Requests: `{self.stats['total_requests']}`\n"
                  f"• Successful: `{self.stats['successful_generations']}`\n"
                  f"• Failed: `{self.stats['failed_generations']}`\n"
                  f"• Success Rate: `{success_rate:.1f}%`\n"
                  f"• Avg Time: `{avg_time:.1f}s`",
            inline=True
        )

        # Cache stats
        embed.add_field(
            name="⚡ Cache Performance",
            value=f"• Cache Hits: `{self.stats['cache_hits']}`\n"
                  f"• Cache Size: `{cache_stats['size']}/{cache_stats['max_size']}`\n"
                  f"• Hit Rate: `{cache_stats['hit_rate']:.1f}%`",
            inline=True
        )

        # Queue stats
        embed.add_field(
            name="📋 Queue Status",
            value=f"• Queue Size: `{self.queue.size()}`\n"
                  f"• Processing: `{'Yes' if self.queue.current_request else 'No'}`\n"
                  f"• Premium Users: `{sum(1 for pref in self.user_preferences.values() if pref.get('premium', False))}`",
            inline=True
        )

        await inter.edit_original_response(embed=embed)

    @dalle_group.sub_command(name="queue", description="View current generation queue")
    async def dalle_queue(self, inter: disnake.ApplicationCommandInteraction):
        """Show current generation queue status"""
        await inter.response.defer(ephemeral=True)

        queue_size = self.queue.size()
        current = self.queue.current_request

        embed = disnake.Embed(
            title="📋 Generation Queue Status",
            color=disnake.Color.blue()
        )

        embed.add_field(
            name="Queue Overview",
            value=f"• **Queue Size**: {queue_size} requests\n"
                  f"• **Currently Processing**: {'Yes' if current else 'No'}\n"
                  f"• **Max Queue Size**: 50 requests",
            inline=False
        )

        if current:
            time_elapsed = time.time() - current.timestamp
            embed.add_field(
                name="Current Generation",
                value=f"• **User**: <@{current.user_id}>\n"
                      f"• **Started**: {time_elapsed:.0f}s ago\n"
                      f"• **Quality**: {current.quality.upper()}",
                inline=False
            )

            # Add prompt preview
            prompt_preview = current.prompt[:100] + "..." if len(current.prompt) > 100 else current.prompt
            embed.add_field(
                name="Prompt Preview",
                value=f"```{prompt_preview}```",
                inline=False
            )

        if queue_size > 0:
            embed.set_footer(text=f"Estimated wait time: ~{queue_size * 30} seconds")

        await inter.edit_original_response(embed=embed)

    @dalle_group.sub_command(name="tips", description="Get tips for better DALL-E prompts")
    async def dalle_tips(self, inter: disnake.ApplicationCommandInteraction):
        """Get tips for creating better DALL-E prompts"""

        embed = disnake.Embed(
            title="💡 DALL-E Prompt Tips",
            description="Create amazing images with these prompt techniques:",
            color=disnake.Color.gold()
        )

        tips = [
            "**Be Specific**: Instead of 'a cat', try 'a fluffy siamese cat wearing a tiny crown, photorealistic'",
            "**Add Style**: Include terms like 'digital art', 'oil painting', 'anime style', 'photorealistic'",
            "**Set the Scene**: Describe lighting, environment, and mood - 'sunset lighting, dramatic shadows'",
            "**Artist Inspiration**: Reference styles like 'in the style of Van Gogh' or 'Studio Ghibli style'",
            "**Quality Terms**: Use '4k', 'ultra detailed', 'masterpiece', 'best quality'",
            "**Composition**: Specify 'close-up', 'wide shot', 'portrait', 'landscape view'",
            "**Color Palette**: Mention specific colors or 'vibrant colors', 'monochrome', 'pastel palette'"
        ]

        for tip in tips:
            embed.add_field(name="📌", value=tip, inline=False)

        embed.set_footer(text="Experiment with different combinations for unique results!")

        await inter.response.send_message(embed=embed, ephemeral=True)


def setup(bot):
    """Setup function for loading the cog"""
    bot.add_cog(DALLECog(bot))