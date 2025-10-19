"""
DALL-E Image Generation Cog - AI-Powered Image Creation

FEATURES:
- 🎨 DALL-E 3 image generation with HD quality support
- 📋 Queue management (handles multiple simultaneous requests)
- ⚡ Smart caching (avoid duplicate generations)
- 🔄 Automatic retry with exponential backoff
- 🚦 Rate limiting (prevents spam and cost control)
- 🏥 Health checks (auto-restart on failures)
- 📊 Usage statistics tracking

COMMANDS:
- /image [prompt] - Generate AI image
  - size: 1024x1024, 1792x1024, 1024x1792
  - quality: standard, hd (HD recommended)
  - private: true/false (make response private)

QUEUE SYSTEM:
- Multiple requests queued automatically
- FIFO processing (first in, first out)
- Position tracking with estimated wait time
- Job expiration (5 minutes)
- Auto-restart on queue stuck

PERFORMANCE:
- ✅ LRU cache for duplicate prompts
- ✅ Fast hash-based cache keys (100x faster than SHA256)
- ✅ Connection pooling for API efficiency
- ✅ Health monitoring and recovery

COST MANAGEMENT:
- Rate limiting: 10 requests/60 seconds per user (configurable)
- Cache hits are free (no API call)
- Queue prevents spam
- Typical cost: $0.04-0.08 per image

DATA STORAGE:
- Cache is IN-MEMORY only (cleared on restart)
- No persistent storage (privacy-friendly)
- Stats reset on bot restart
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional

import aiohttp
import disnake
from disnake.ext import commands

from . import utils


@dataclass
class GenerationJob:
    """Image generation job"""
    user_id: int
    prompt: str
    size: str
    quality: str
    interaction: disnake.ApplicationCommandInteraction
    timestamp: float

    def is_expired(self, max_age: int = 300) -> bool:
        """Check if job is too old (5 minutes)"""
        return (time.time() - self.timestamp) > max_age


class DALLECog(commands.Cog):
    """DALL-E 3 image generation"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("dalle")

        # Check API key
        if not hasattr(bot.config, 'OPENAI_API_KEY') or not bot.config.OPENAI_API_KEY:
            self.logger.warning("OPENAI_API_KEY not configured - DALL-E disabled")
            self.enabled = False
            return

        self.enabled = True

        # Components with configurable limits
        rate_limit = getattr(bot.config, 'RATE_LIMIT_REQUESTS', 10)
        rate_window = getattr(bot.config, 'RATE_LIMIT_WINDOW', 60)
        max_queue = getattr(bot.config, 'MAX_QUEUE_SIZE', 50)
        
        self.rate_limiter = utils.RateLimiter(limit=rate_limit, window=rate_window)
        self.cache = utils.LRUCache[str](max_size=max_queue, ttl=3000)  # URLs expire in ~1 hour

        # Queue
        self.queue = asyncio.Queue(maxsize=max_queue)
        self.processor_task: Optional[asyncio.Task] = None
        self.is_processing = False

        # Config
        self.api_url = "https://api.openai.com/v1/images/generations"

        # Stats
        self.stats = {
            "total_requests": 0,
            "successful": 0,
            "failed": 0,
            "cache_hits": 0,
            "total_time": 0.0
        }

        self._shutdown = asyncio.Event()
        self._unloaded = False  # Track if already unloaded
        self._health_check_task = None

        self.logger.info("DALL-E cog initialized")

    async def cog_load(self):
        """Initialize cog"""
        if not self.enabled:
            # Notify Discord about DALLE being disabled
            if hasattr(self.bot, 'send_to_discord_log'):
                await self.bot.send_to_discord_log("🖼️ DALL-E cog loaded but disabled (no API key)", "WARNING")
            return

        self.processor_task = asyncio.create_task(self._process_queue())
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        self.logger.info("DALL-E cog loaded")
        
        # Notify Discord about successful loading
        if hasattr(self.bot, 'send_to_discord_log'):
            await self.bot.send_to_discord_log("🎨 DALL-E cog loaded successfully", "SUCCESS")

    def cog_unload(self):
        """Cleanup cog (synchronous wrapper to prevent RuntimeWarning)"""
        if not self.enabled or self._unloaded:
            return
        
        self._unloaded = True
        self.logger.info("Unloading DALL-E cog...")
        
        # Schedule async cleanup
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Create task for async cleanup
                loop.create_task(self._async_unload())
            else:
                # If no running loop, do sync cleanup only
                self._shutdown.set()
                self.logger.info("DALL-E cog unloaded (sync)")
        except RuntimeError:
            # No event loop available, do minimal cleanup
            self._shutdown.set()
            self.logger.info("DALL-E cog unloaded (no loop)")
    
    async def _async_unload(self):
        """Async cleanup operations"""
        try:
            self._shutdown.set()

            if self.processor_task:
                self.processor_task.cancel()
                try:
                    await self.processor_task
                except asyncio.CancelledError:
                    pass
            
            if self._health_check_task:
                self._health_check_task.cancel()
                try:
                    await self._health_check_task
                except asyncio.CancelledError:
                    pass

            self.logger.info("DALL-E cog unloaded")
        except Exception as e:
            self.logger.error(f"Async unload error: {e}")

    def _cache_key(self, prompt: str, size: str, quality: str) -> str:
        """
        Generate cache key using Python's built-in hash (faster than SHA256).
        
        PERFORMANCE:
        - Built-in hash() is ~100x faster than SHA256
        - No cryptographic security needed for cache keys
        - Collision risk negligible for cache (overwrite is acceptable)
        """
        return str(hash(f"{prompt}:{size}:{quality}"))

    async def _generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "hd"
    ) -> Dict:
        """Call DALL-E API"""

        headers = {
            "Authorization": f"Bearer {self.bot.config.OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "dall-e-3",
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "url",
            "style": "vivid"
        }

        self.stats["total_requests"] += 1

        # Try up to 3 times
        for attempt in range(3):
            try:
                session = await self.bot.http_mgr.get_session(timeout=45)

                async with session.post(
                    self.api_url,
                    json=payload,
                    headers=headers
                ) as resp:

                    if resp.status == 200:
                        result = await resp.json()
                        self.stats["successful"] += 1
                        return {"success": True, "data": result}

                    elif resp.status == 429:
                        retry_after = resp.headers.get('Retry-After', '60')

                        if attempt < 2:
                            wait = min(int(retry_after), 30)
                            self.logger.warning(f"Rate limited, waiting {wait}s")
                            await asyncio.sleep(wait)
                            continue

                        return {"success": False, "error": f"Rate limited. Try again in {retry_after}s"}

                    elif resp.status == 400:
                        error_data = await resp.json()
                        error_msg = error_data.get("error", {}).get("message", "Bad request")

                        if "content_policy" in error_msg.lower():
                            return {"success": False, "error": "🚫 Content policy violation"}

                        return {"success": False, "error": f"Invalid request: {error_msg}"}

                    elif resp.status == 401:
                        return {"success": False, "error": "🔒 API authentication failed"}

                    else:
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue

                        error = await resp.text()
                        return {"success": False, "error": f"API error {resp.status}"}

            except asyncio.TimeoutError:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return {"success": False, "error": "⏰ Request timeout"}

            except Exception as e:
                self.logger.error(f"Generation error: {e}", exc_info=True)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return {"success": False, "error": f"Unexpected error: {str(e)[:50]}"}

        self.stats["failed"] += 1
        return {"success": False, "error": "Max retries exceeded"}

    async def _process_queue(self):
        """Process generation queue"""
        try:
            while not self._shutdown.is_set():
                try:
                    # Get next job
                    job = await asyncio.wait_for(self.queue.get(), timeout=60)

                    # Check if expired
                    if job.is_expired():
                        try:
                            await job.interaction.edit_original_response(
                                content="⏰ Request expired"
                            )
                        except Exception:
                            pass
                        continue

                    self.is_processing = True

                    # Update status
                    try:
                        embed = disnake.Embed(
                            title="🎨 Generating Image",
                            description="Creating your masterpiece with DALL-E 3...",
                            color=disnake.Color.blue()
                        )
                        embed.add_field(
                            name="Prompt",
                            value=f"```{job.prompt[:100]}...```" if len(job.prompt) > 100 else f"```{job.prompt}```",
                            inline=False
                        )
                        embed.add_field(name="Quality", value=job.quality.upper(), inline=True)
                        embed.add_field(name="Size", value=job.size, inline=True)
                        embed.set_footer(text="This may take 15-30 seconds")

                        await job.interaction.edit_original_response(embed=embed)
                    except Exception:
                        pass

                    # Generate
                    start = time.time()
                    result = await self._generate_image(job.prompt, job.size, job.quality)
                    elapsed = time.time() - start
                    self.stats["total_time"] += elapsed

                    # Cache if successful
                    if result.get("success") and result.get("data"):
                        cache_key = self._cache_key(job.prompt, job.size, job.quality)
                        image_url = result["data"]["data"][0]["url"]
                        await self.cache.set(cache_key, image_url)

                    # Send result
                    await self._send_result(job, result, elapsed)

                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.logger.error(f"Queue processing error: {e}", exc_info=True)
                    await asyncio.sleep(1)
                finally:
                    self.is_processing = False

        except asyncio.CancelledError:
            pass

    async def _health_check_loop(self):
        """Health check for hung processor task"""
        try:
            while not self._shutdown.is_set():
                await asyncio.sleep(600)  # Every 10 minutes
                
                # Check if processor task died unexpectedly
                if self.processor_task and self.processor_task.done():
                    exc = self.processor_task.exception()
                    if exc:
                        self.logger.error(f"Processor task died: {exc}")
                        # Restart it if queue is not empty
                        if not self.queue.empty():
                            self.logger.info("Restarting DALL-E processor task")
                            self.processor_task = asyncio.create_task(self._process_queue())
                
                # Check for stuck queue
                if self.queue.qsize() > 0 and not self.is_processing:
                    self.logger.warning("Queue stuck, attempting restart")
                    if not self.processor_task or self.processor_task.done():
                        self.processor_task = asyncio.create_task(self._process_queue())
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Health check loop error: {e}", exc_info=True)

    async def _send_result(self, job: GenerationJob, result: Dict, elapsed: float):
        """Send generation result"""
        try:
            if not result.get("success"):
                embed = disnake.Embed(
                    title="❌ Generation Failed",
                    description=result.get("error", "Unknown error"),
                    color=disnake.Color.red()
                )
                embed.set_footer(text=f"Time: {elapsed:.1f}s")
                await job.interaction.edit_original_response(embed=embed)
                return

            image_url = result["data"]["data"][0]["url"]

            embed = disnake.Embed(
                title="🎨 Image Generated!",
                description=f"**Prompt:** {job.prompt[:200]}{'...' if len(job.prompt) > 200 else ''}",
                color=disnake.Color.green()
            )
            embed.set_image(url=image_url)
            embed.add_field(name="Time", value=f"{elapsed:.1f}s", inline=True)
            embed.add_field(name="Model", value="DALL-E 3", inline=True)
            embed.add_field(name="Quality", value=job.quality.upper(), inline=True)
            embed.set_footer(text="💡 Tip: Use specific details for better results!")

            await job.interaction.edit_original_response(embed=embed)

        except Exception as e:
            self.logger.error(f"Failed to send result: {e}", exc_info=True)
            try:
                await job.interaction.edit_original_response(
                    content="❌ Failed to send result"
                )
            except Exception:
                pass

    @commands.slash_command(name="image", description="🎨 Generate AI images with DALL-E 3")
    async def imagine(
        self,
        inter: disnake.ApplicationCommandInteraction,
        prompt: str = commands.Param(description="Describe the image", max_length=4000),
        size: str = commands.Param(
            default="1024x1024",
            choices=["1024x1024", "1792x1024", "1024x1792"],
            description="Image size"
        ),
        quality: str = commands.Param(
            default="hd",
            choices=["standard", "hd"],
            description="Image quality (HD recommended)"
        ),
        private: bool = commands.Param(
            default=False,
            description="Make response private"
        )
    ):
        """Generate images with DALL-E 3"""

        if not self.enabled:
            await inter.response.send_message(
                "❌ DALL-E is not configured",
                ephemeral=True
            )
            return

        await inter.response.defer(ephemeral=private)

        user_id = str(inter.author.id)

        # Check rate limit
        if not await self.rate_limiter.check(user_id):
            await inter.edit_original_response(
                content="⏳ Rate limited. Please wait before generating another image."
            )
            return

        # Validate prompt
        if len(prompt.strip()) < 3:
            await inter.edit_original_response(
                content="❌ Prompt too short (min 3 characters)"
            )
            return

        # Clean prompt
        prompt = prompt.strip()

        # Check cache
        cache_key = self._cache_key(prompt, size, quality)
        cached = await self.cache.get(cache_key)

        if cached:
            self.stats["cache_hits"] += 1

            embed = disnake.Embed(
                title="🎨 Image (Cached)",
                description=f"**Prompt:** {prompt[:200]}{'...' if len(prompt) > 200 else ''}",
                color=disnake.Color.blue()
            )
            embed.set_image(url=cached)
            embed.set_footer(text="⚡ Retrieved from cache")

            await inter.edit_original_response(embed=embed)
            return

        # Create job
        job = GenerationJob(
            user_id=inter.author.id,
            prompt=prompt,
            size=size,
            quality=quality,
            interaction=inter,
            timestamp=time.time()
        )

        # Add to queue
        try:
            self.queue.put_nowait(job)

            queue_size = self.queue.qsize()

            embed = disnake.Embed(
                title="⏳ Queued",
                description="Your image is in the queue",
                color=disnake.Color.blue()
            )
            embed.add_field(name="Position", value=f"#{queue_size}", inline=True)
            embed.add_field(name="Est. Wait", value=f"~{queue_size * 30}s", inline=True)
            embed.set_footer(text="You'll be notified when generation starts")

            await inter.edit_original_response(embed=embed)

        except asyncio.QueueFull:
            await inter.edit_original_response(
                content="❌ Queue is full. Try again in a few minutes."
            )



def setup(bot):
    """Setup the cog"""
    bot.add_cog(DALLECog(bot))