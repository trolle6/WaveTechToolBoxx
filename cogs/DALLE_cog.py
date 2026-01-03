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

import aiohttp
import disnake
from disnake.ext import commands

from . import utils


@dataclass
class GenerationJob:
    """
    Represents a single image generation request in the processing queue.
    
    Attributes:
        user_id: Discord user ID who requested the generation
        prompt: Text description of the image to generate
        size: Image size (1024x1024, 1792x1024, or 1024x1792)
        quality: Image quality (standard or hd)
        interaction: Discord interaction object for responding to the user
        timestamp: Unix timestamp when job was created (for expiry tracking)
    
    Design: Separates job creation from processing to enable queue management and prevent
    API rate limiting by processing requests sequentially.
    """
    user_id: int
    prompt: str
    size: str
    quality: str
    interaction: disnake.ApplicationCommandInteraction
    timestamp: float

    # Job expiry configuration
    DEFAULT_EXPIRY_SECONDS = 300  # 5 minutes - jobs expire after this time

    def is_expired(self, max_age: int = DEFAULT_EXPIRY_SECONDS) -> bool:
        """
        Check if this generation job has expired.
        
        Args:
            max_age: Maximum age in seconds before job expires
            
        Returns:
            True if job is older than max_age seconds
        """
        return (time.time() - self.timestamp) > max_age


class DALLECog(commands.Cog):
    """
    DALL-E 3 image generation cog with queue management and caching.
    
    DESIGN DECISIONS:
    - FIFO queue: Ensures fair processing order, prevents API rate limiting
    - Sequential processing: One image at a time for reliability and cost control
    - LRU cache: Avoids duplicate generations for same prompt/size/quality
    - Health monitoring: Auto-restarts processor if it crashes or hangs
    - Exponential backoff: Retries failed requests with increasing delays
    """

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

    # ============ EMBED HELPERS ============
    # These methods standardize embed creation for consistent user experience
    # and easier maintenance (change style in one place)
    
    def _create_error_embed(self, error_msg: str, elapsed: float = 0.0) -> disnake.Embed:
        """
        Create standardized error embed for failed generations.
        
        Args:
            error_msg: Error message to display to user
            elapsed: Optional elapsed time to show in footer (0 = don't show)
            
        Returns:
            Configured error embed with red color
        """
        embed = disnake.Embed(
            title="❌ Generation Failed",
            description=error_msg,
            color=disnake.Color.red()
        )
        if elapsed > 0:
            embed.set_footer(text=f"Time: {elapsed:.1f}s")
        return embed

    def _create_success_embed(self, image_url: str, prompt: str, quality: str, elapsed: float) -> disnake.Embed:
        """
        Create standardized success embed for completed generations.
        
        Args:
            image_url: URL of generated image
            prompt: Original prompt (truncated for display)
            quality: Image quality (standard/hd)
            elapsed: Generation time in seconds
            
        Returns:
            Configured success embed with image, stats, and helpful tip
        """
        prompt_preview = prompt[:200] + "..." if len(prompt) > 200 else prompt
        embed = disnake.Embed(
            title="🎨 Image Generated!",
            description=f"**Prompt:** {prompt_preview}",
            color=disnake.Color.green()
        )
        embed.set_image(url=image_url)
        embed.add_field(name="Time", value=f"{elapsed:.1f}s", inline=True)
        embed.add_field(name="Model", value="DALL-E 3", inline=True)
        embed.add_field(name="Quality", value=quality.upper(), inline=True)
        embed.set_footer(text="💡 Tip: Use specific details for better results!")
        return embed

    def _create_loading_embed(self, prompt: str, size: str, quality: str) -> disnake.Embed:
        """Create loading embed"""
        embed = disnake.Embed(
            title="🎨 Generating Image",
            description="Creating your masterpiece with DALL-E 3...",
            color=disnake.Color.blue()
        )
        prompt_preview = prompt[:100] + "..." if len(prompt) > 100 else prompt
        embed.add_field(name="Prompt", value=f"```{prompt_preview}```", inline=False)
        embed.add_field(name="Quality", value=quality.upper(), inline=True)
        embed.add_field(name="Size", value=size, inline=True)
        embed.set_footer(text="This may take 15-30 seconds")
        return embed

    def _create_cache_embed(self, image_url: str) -> disnake.Embed:
        """Create cache hit embed"""
        embed = disnake.Embed(
            title="🎨 Image Generated!",
            description="Retrieved from cache",
            color=disnake.Color.blue()
        )
        embed.set_image(url=image_url)
        embed.set_footer(text="⚡ Retrieved from cache")
        return embed

    def _create_queue_embed(self, queue_size: int) -> disnake.Embed:
        """Create queue position embed"""
        embed = disnake.Embed(
            title="⏳ Image Generation Queued",
            description="Your request has been added to the queue",
            color=disnake.Color.blue()
        )
        embed.add_field(name="Position", value=f"#{queue_size}", inline=True)
        embed.add_field(name="Est. Wait", value=f"~{queue_size * 30}s", inline=True)
        embed.set_footer(text="You'll be notified when it's ready")
        return embed

    # ============ CACHE ============
    def _cache_key(self, prompt: str, size: str, quality: str) -> str:
        """Generate cache key"""
        return str(hash(f"{prompt}:{size}:{quality}"))

    # ============ API HELPERS ============
    def _get_openai_headers(self) -> Dict[str, str]:
        """Get common OpenAI API headers"""
        return {
            "Authorization": f"Bearer {self.bot.config.OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

    # ============ API GENERATION ============
    async def _generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "hd"
    ) -> Dict:
        """Call DALL-E API with retry logic"""
        headers = self._get_openai_headers()

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
        # DALL-E API can take 15-30 seconds for generation, so use a generous timeout
        REQUEST_TIMEOUT = 45  # seconds - enough for DALL-E generation
        
        self.logger.debug(f"Generating DALL-E image: prompt_length={len(prompt)}, size={size}, quality={quality}")
        
        for attempt in range(self.max_retries):
            try:
                session = await self.bot.http_mgr.get_session()
                
                # Use request-level timeout to ensure proper timeout handling
                # (session timeout might be different if session was reused)
                request_timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                
                async with session.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=request_timeout
                ) as resp:

                    if resp.status == 200:
                        try:
                            result = await resp.json()
                        except Exception as json_err:
                            self.logger.error(f"Failed to parse JSON response: {json_err}")
                            if attempt < self.max_retries - 1:
                                continue
                            return {"success": False, "error": "Invalid API response format"}
                        
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
                        try:
                            error_data = await resp.json()
                            error_msg = error_data.get("error", {}).get("message", "Bad request")
                        except Exception:
                            error_msg = "Bad request (could not parse error message)"
                        
                        if "content_policy" in error_msg.lower():
                            return {"success": False, "error": "🚫 Content policy violation"}
                        return {"success": False, "error": f"Invalid request: {error_msg}"}

                    elif resp.status == 401:
                        self.logger.error("API authentication failed - check OPENAI_API_KEY")
                        return {"success": False, "error": "🔒 API authentication failed"}

                    else:
                        self.logger.warning(f"DALL-E API returned status {resp.status}")
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
                        embed = self._create_loading_embed(job.prompt, job.size, job.quality)
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
                embed = self._create_error_embed(result.get("error", "Unknown error"), elapsed)
                await job.interaction.edit_original_response(embed=embed)
                return

            image_url = result["data"]["data"][0]["url"]
            embed = self._create_success_embed(image_url, job.prompt, job.quality, elapsed)
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

            embed = self._create_cache_embed(cached)
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

            embed = self._create_queue_embed(queue_size)
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
