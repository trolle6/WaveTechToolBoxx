"""
DALL-E Image Generation Cog - AI-Powered Image Creation

FEATURES:
- 🎨 DALL-E 3 image generation with HD quality support
- 📋 Queue management (FIFO processing)
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
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional

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
        self.cache = utils.LRUCache[str](max_size=max_queue, ttl=3600)

        # Queue
        self.queue = asyncio.Queue(maxsize=max_queue)
        self.processor_task: Optional[asyncio.Task] = None
        self.is_processing = False

        # API config
        self.api_url = "https://api.openai.com/v1/images/generations"
        self.max_retries = 3

        # Statistics tracking
        self.stats = {
            "total_requests": 0,
            "successful": 0,
            "failed": 0,
            "cache_hits": 0,
            "total_time": 0.0
        }
        self._stats_lock = asyncio.Lock()

        self._shutdown = asyncio.Event()
        self._unloaded = False
        self._health_check_task: Optional[asyncio.Task] = None

        self.logger.info("DALL-E cog initialized")

    # ============ CACHE ============
    def _cache_key(self, prompt: str, size: str, quality: str) -> str:
        """Generate cache key"""
        return str(hash(f"{prompt}:{size}:{quality}"))

    # ============ API GENERATION ============
    async def _generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "hd"
    ) -> Dict:
        """Call DALL-E API with retry logic"""
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

        async with self._stats_lock:
            self.stats["total_requests"] += 1

        # Retry with exponential backoff
        for attempt in range(self.max_retries):
            try:
                session = await self.bot.http_mgr.get_session(timeout=45)

                async with session.post(
                    self.api_url,
                    json=payload,
                    headers=headers
                ) as resp:

                    if resp.status == 200:
                        result = await resp.json()
                        async with self._stats_lock:
                            self.stats["successful"] += 1
                        return {"success": True, "data": result}

                    elif resp.status == 429:
                        retry_after = int(resp.headers.get('Retry-After', '60'))
                        if attempt < self.max_retries - 1:
                            wait = min(retry_after, 30)
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
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        return {"success": False, "error": f"API error {resp.status}"}

            except asyncio.TimeoutError:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return {"success": False, "error": "⏰ Request timeout"}

            except Exception as e:
                self.logger.error(f"Generation error: {e}", exc_info=True)
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return {"success": False, "error": f"Unexpected error: {str(e)[:50]}"}

        async with self._stats_lock:
            self.stats["failed"] += 1
        return {"success": False, "error": "Max retries exceeded"}

    # ============ QUEUE PROCESSING ============
    async def _process_queue(self):
        """Process generation queue"""
        try:
            while not self._shutdown.is_set():
                try:
                    job = await asyncio.wait_for(self.queue.get(), timeout=60)
                    
                    if self._shutdown.is_set():
                        break

                    if job.is_expired():
                        try:
                            await job.interaction.edit_original_response(content="⏰ Request expired")
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
                        prompt_preview = job.prompt[:100] + "..." if len(job.prompt) > 100 else job.prompt
                        embed.add_field(name="Prompt", value=f"```{prompt_preview}```", inline=False)
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
                    
                    async with self._stats_lock:
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

    # ============ RESULT HANDLING ============
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
            prompt_preview = job.prompt[:200] + "..." if len(job.prompt) > 200 else job.prompt

            embed = disnake.Embed(
                title="🎨 Image Generated!",
                description=f"**Prompt:** {prompt_preview}",
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
                await job.interaction.edit_original_response(content="❌ Failed to send result")
            except Exception:
                pass

    # ============ HEALTH CHECK ============
    async def _health_check_loop(self):
        """Health check for hung processor task"""
        try:
            while not self._shutdown.is_set():
                await asyncio.sleep(600)  # Every 10 minutes
                
                if self._shutdown.is_set():
                    break
                
                # Check if processor task died unexpectedly
                if self.processor_task and self.processor_task.done():
                    try:
                        exc = self.processor_task.exception()
                        if exc:
                            self.logger.error(f"Processor task died: {exc}")
                    except (asyncio.CancelledError, asyncio.InvalidStateError):
                        pass
                    
                    # Restart if queue has items
                    if not self.queue.empty() and not self.is_processing:
                        self.logger.info("Restarting DALL-E processor task")
                        self.processor_task = asyncio.create_task(self._process_queue())
                
                # Check for stuck queue
                elif self.queue.qsize() > 0 and not self.is_processing:
                    if not self.processor_task or self.processor_task.done():
                        self.logger.warning("Queue stuck, restarting processor")
                        self.processor_task = asyncio.create_task(self._process_queue())
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Health check loop error: {e}", exc_info=True)

    # ============ COMMAND ============
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
            await inter.response.send_message("❌ DALL-E is not configured", ephemeral=True)
            return

        await inter.response.defer(ephemeral=private)

        # Check rate limit
        if not await self.rate_limiter.check(str(inter.author.id)):
            await inter.edit_original_response(
                content="⏳ Rate limited. Please wait before generating another image."
            )
            return

        # Validate prompt
        prompt = prompt.strip()
        if len(prompt) < 3:
            await inter.edit_original_response(content="❌ Prompt too short (min 3 characters)")
            return

        # Check cache
        cache_key = self._cache_key(prompt, size, quality)
        cached = await self.cache.get(cache_key)

        if cached:
            async with self._stats_lock:
                self.stats["cache_hits"] += 1

            prompt_preview = prompt[:200] + "..." if len(prompt) > 200 else prompt
            embed = disnake.Embed(
                title="🎨 Image (Cached)",
                description=f"**Prompt:** {prompt_preview}",
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

    # ============ COG LIFECYCLE ============
    async def cog_load(self):
        """Initialize cog"""
        if not self.enabled:
            return

        self.processor_task = asyncio.create_task(self._process_queue())
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        self.logger.info("DALL-E cog loaded")

    def cog_unload(self):
        """Cleanup cog"""
        if not self.enabled or self._unloaded:
            return
        
        self._unloaded = True
        self.logger.info("Unloading DALL-E cog...")
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._async_unload())
            else:
                self._shutdown.set()
        except RuntimeError:
            self._shutdown.set()
    
    async def _async_unload(self):
        """Async cleanup"""
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


def setup(bot):
    """Setup the cog"""
    bot.add_cog(DALLECog(bot))
