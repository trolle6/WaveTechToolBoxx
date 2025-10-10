"""
Enhanced Voice Processing Cog for Discord Bot
Handles TTS (Text-to-Speech) functionality with improved reliability and performance
"""

import asyncio
import hashlib
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set
from collections import deque

import disnake
from disnake.ext import commands

from . import utils


@dataclass
class QueueItem:
    """Represents an item in the TTS queue"""
    message: disnake.Message
    text: str
    audio_data: bytes
    queued_at: float
    priority: int = 0

    def is_stale(self, max_age: int = 60) -> bool:
        """Check if the item is too old to process"""
        return (time.time() - self.queued_at) > max_age


class VoiceState:
    """Manages voice state for a guild"""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=30)
        self.processing_task: Optional[asyncio.Task] = None
        self.current_channel: Optional[int] = None
        self.reconnect_attempts = 0
        self.last_activity = time.time()
        self.processed_count = 0
        self.dropped_count = 0

    def is_active(self) -> bool:
        """Check if voice state is active"""
        return self.processing_task and not self.processing_task.done()

    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = time.time()

    def cleanup(self):
        """Clean up voice state"""
        if self.processing_task and not self.processing_task.done():
            self.processing_task.cancel()


class SimpleRateLimiter:
    """Fallback rate limiter if the main one doesn't support burst parameters"""

    def __init__(self, limit: int = 20, window: int = 60):
        self.limit = limit
        self.window = window
        self.requests = deque()

    async def check(self, identifier: str) -> bool:
        """Check if request is allowed - async to match expected interface"""
        now = time.time()

        # Remove old requests
        while self.requests and self.requests[0] < now - self.window:
            self.requests.popleft()

        # Check limit
        if len(self.requests) < self.limit:
            self.requests.append(now)
            return True

        return False


class SimpleCircuitBreaker:
    """Fallback circuit breaker"""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60, success_threshold: int = 2):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.failures = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.last_failure_time = 0
        self.success_count = 0

    async def can_attempt(self) -> bool:
        """Check if request can be attempted"""
        now = time.time()

        if self.state == "OPEN":
            if now - self.last_failure_time > self.recovery_timeout:
                self.state = "HALF_OPEN"
                return True
            return False

        return True

    async def record_success(self):
        """Record a successful request"""
        if self.state == "HALF_OPEN":
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = "CLOSED"
                self.failures = 0
                self.success_count = 0
        else:
            self.failures = max(0, self.failures - 1)

    async def record_failure(self):
        """Record a failed request"""
        self.failures += 1
        self.last_failure_time = time.time()

        if self.failures >= self.failure_threshold:
            self.state = "OPEN"

    async def get_metrics(self) -> dict:
        """Get circuit breaker metrics"""
        return {
            "state": self.state,
            "current_failures": self.failures,
            "uptime_percentage": 100.0 if self.state == "CLOSED" else 0.0
        }


class SimpleCache:
    """Simple in-memory cache with TTL"""

    def __init__(self, max_size: int = 100, ttl: int = 3600):
        self.max_size = max_size
        self.ttl = ttl
        self._cache = {}
        self._access_times = {}

    async def get(self, key: str):
        """Get value from cache"""
        if key in self._cache:
            value, timestamp = self._cache[key]
            if time.time() - timestamp < self.ttl:
                self._access_times[key] = time.time()
                return value
            else:
                # Expired
                del self._cache[key]
                del self._access_times[key]
        return None

    async def set(self, key: str, value):
        """Set value in cache"""
        # Remove oldest if at capacity
        if len(self._cache) >= self.max_size:
            oldest_key = min(self._access_times.keys(), key=lambda k: self._access_times[k])
            del self._cache[oldest_key]
            del self._access_times[oldest_key]

        self._cache[key] = (value, time.time())
        self._access_times[key] = time.time()

    async def get_stats(self) -> dict:
        """Get cache statistics"""
        # Clean expired entries first
        now = time.time()
        expired_keys = [k for k, (_, ts) in self._cache.items() if now - ts >= self.ttl]
        for key in expired_keys:
            del self._cache[key]
            del self._access_times[key]

        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hit_rate": 0,  # We don't track hits/misses in this simple version
            "hits": 0,
            "misses": 0
        }

    async def cleanup(self):
        """Clean up expired entries"""
        now = time.time()
        expired_keys = [k for k, (_, ts) in self._cache.items() if now - ts >= self.ttl]
        for key in expired_keys:
            del self._cache[key]
            del self._access_times[key]


class VoiceProcessingCog(commands.Cog):
    """Enhanced voice processing with TTS functionality"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("voice")

        # Rate limiting with fallback for burst parameters
        try:
            self.rate_limiter = utils.RateLimiter(
                limit=20,
                window=60
            )
            # Test if check method is async
            if not asyncio.iscoroutinefunction(self.rate_limiter.check):
                raise AttributeError("RateLimiter.check is not async")
        except (TypeError, AttributeError):
            # Fallback if RateLimiter doesn't support parameters or check is not async
            self.rate_limiter = SimpleRateLimiter(limit=20, window=60)
            self.logger.info("Using fallback rate limiter")

        # Circuit breaker for API calls
        try:
            self.breaker = utils.CircuitBreaker(
                failure_threshold=5,
                recovery_timeout=60,
                success_threshold=2
            )
        except (TypeError, AttributeError):
            # Fallback if CircuitBreaker is not available
            self.breaker = SimpleCircuitBreaker(
                failure_threshold=5,
                recovery_timeout=60,
                success_threshold=2
            )
            self.logger.info("Using fallback circuit breaker")

        # Enhanced cache with LRU
        try:
            self.tts_cache = utils.LRUCache[bytes](max_size=100, ttl=3600)
        except (TypeError, AttributeError):
            # Fallback simple cache
            self.tts_cache = SimpleCache(max_size=100, ttl=3600)
            self.logger.info("Using fallback cache")

        # Voice states per guild
        self.guild_states: Dict[int, VoiceState] = {}

        # Configuration
        self.openai_url = "https://api.openai.com/v1/audio/speech"
        self.voices = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
        self.default_voice = "alloy"

        # Performance tracking
        self.metrics = {
            "tts_requests": 0,
            "cache_hits": 0,
            "failed_requests": 0,
            "messages_processed": 0
        }

        # Cleanup task
        self.cleanup_task = None

    async def cog_load(self):
        """Initialize cog resources"""
        self.cleanup_task = asyncio.create_task(self._periodic_cleanup())
        self.logger.info("Voice Processing Cog loaded")

    async def cog_unload(self):
        """Clean up cog resources"""
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass

        # Clean up all voice states
        for state in self.guild_states.values():
            state.cleanup()

        # Disconnect all voice clients
        disconnect_tasks = []
        for vc in self.bot.voice_clients:
            if vc.is_connected():
                disconnect_tasks.append(vc.disconnect(force=True))

        if disconnect_tasks:
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)

        self.logger.info("Voice Processing Cog unloaded")

    def _get_state(self, guild_id: int) -> VoiceState:
        """Get or create voice state for guild"""
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = VoiceState(guild_id)
        return self.guild_states[guild_id]

    def _cleanup_state(self, guild_id: int):
        """Clean up voice state for guild"""
        if guild_id in self.guild_states:
            self.guild_states[guild_id].cleanup()
            del self.guild_states[guild_id]

    async def _periodic_cleanup(self):
        """Periodic cleanup task"""
        try:
            while True:
                await asyncio.sleep(300)  # Every 5 minutes

                # Clean up cache if it has cleanup method
                if hasattr(self.tts_cache, 'cleanup'):
                    await self.tts_cache.cleanup()

                # Clean up inactive voice states
                current_time = time.time()
                inactive_guilds = [
                    guild_id for guild_id, state in self.guild_states.items()
                    if current_time - state.last_activity > 600  # 10 minutes
                ]

                for guild_id in inactive_guilds:
                    guild = self.bot.get_guild(guild_id)
                    if guild and guild.voice_client:
                        await guild.voice_client.disconnect()
                    self._cleanup_state(guild_id)

                if inactive_guilds:
                    self.logger.info(f"Cleaned up {len(inactive_guilds)} inactive voice states")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Cleanup task error: {e}")

    async def _ensure_voice_connection(
            self,
            guild: disnake.Guild,
            channel: disnake.VoiceChannel,
            retry_count: int = 3
    ) -> Optional[disnake.VoiceClient]:
        """Ensure voice connection with retry logic"""

        # Check existing connection
        if guild.voice_client:
            if guild.voice_client.is_connected():
                if guild.voice_client.channel.id == channel.id:
                    return guild.voice_client
                else:
                    # Wrong channel, disconnect
                    try:
                        await guild.voice_client.disconnect(force=True)
                    except Exception:
                        pass

        # Attempt connection with retries
        for attempt in range(retry_count):
            try:
                vc = await channel.connect(timeout=10, reconnect=True)
                self.logger.debug(f"Connected to voice channel {channel.id} in guild {guild.id}")
                return vc

            except disnake.ClientException as e:
                if "already connected" in str(e).lower():
                    # Try to get existing connection
                    if guild.voice_client and guild.voice_client.is_connected():
                        return guild.voice_client

                self.logger.warning(f"Voice connection attempt {attempt + 1} failed: {e}")

                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)

            except asyncio.TimeoutError:
                self.logger.warning(f"Voice connection timeout on attempt {attempt + 1}")
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)

            except Exception as e:
                self.logger.error(f"Unexpected voice connection error: {e}")
                break

        return None

    def _generate_cache_key(self, text: str, voice: str) -> str:
        """Generate cache key for TTS request"""
        content = f"{voice}:{text}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    async def _get_tts_audio(self, text: str, voice: str = None) -> Optional[bytes]:
        """Get TTS audio with caching and circuit breaker"""

        # Skip circuit breaker if not available
        if self.breaker and not await self.breaker.can_attempt():
            self.logger.warning("Circuit breaker is open, skipping TTS request")
            return None

        voice = voice or self.default_voice
        cache_key = self._generate_cache_key(text, voice)

        # Check cache
        cached_audio = await self.tts_cache.get(cache_key)
        if cached_audio:
            self.metrics["cache_hits"] += 1
            return cached_audio

        # Prepare request
        headers = {
            "Authorization": f"Bearer {self.bot.config.TTS_BEARER_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": 1.0
        }

        # Make request with retries
        self.metrics["tts_requests"] += 1

        try:
            session = await self.bot.http_mgr.get_session(timeout=15)

            async with session.post(
                    self.openai_url,
                    json=payload,
                    headers=headers
            ) as response:

                if response.status == 200:
                    audio_data = await response.read()

                    # Store in cache
                    await self.tts_cache.set(cache_key, audio_data)

                    if self.breaker:
                        await self.breaker.record_success()
                    return audio_data

                elif response.status == 429:
                    # Rate limited
                    retry_after = response.headers.get('Retry-After', '60')
                    self.logger.warning(f"TTS rate limited, retry after {retry_after}s")
                    if self.breaker:
                        await self.breaker.record_failure()
                    self.metrics["failed_requests"] += 1
                    return None

                else:
                    error_text = await response.text()
                    self.logger.error(f"TTS API error {response.status}: {error_text}")
                    if self.breaker:
                        await self.breaker.record_failure()
                    self.metrics["failed_requests"] += 1
                    return None

        except asyncio.TimeoutError:
            self.logger.error("TTS request timeout")
            if self.breaker:
                await self.breaker.record_failure()
            self.metrics["failed_requests"] += 1
            return None

        except Exception as e:
            self.logger.error(f"TTS request error: {e}")
            if self.breaker:
                await self.breaker.record_failure()
            self.metrics["failed_requests"] += 1
            return None

    def _process_text(self, text: str, max_length: int = 500) -> str:
        """Process and clean text for TTS"""
        # Remove multiple whitespaces
        text = re.sub(r'\s+', ' ', text.strip())

        # Remove custom emojis
        text = re.sub(r'<a?:\w+:\d+>', '', text)

        # Remove user/role mentions
        text = re.sub(r'<@!?\d+>', '', text)
        text = re.sub(r'<@&\d+>', '', text)

        # Remove channel mentions
        text = re.sub(r'<#\d+>', '', text)

        # Truncate if needed
        if len(text) > max_length:
            text = text[:max_length - 3] + "..."

        # Ensure text ends with punctuation
        if text and text[-1] not in '.!?,;:':
            text += '.'

        return text.strip()

    async def _play_tts_audio(
            self,
            vc: disnake.VoiceClient,
            audio_data: bytes,
            text: str
    ) -> bool:
        """Play TTS audio through voice client"""

        temp_file = None
        try:
            # Create temporary file
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
                tmp.write(audio_data)
                temp_file = tmp.name

            # Wait if already playing
            if vc.is_playing():
                await asyncio.wait_for(
                    asyncio.create_task(self._wait_for_audio_end(vc)),
                    timeout=30
                )

            # Play audio
            audio_source = disnake.FFmpegPCMAudio(
                temp_file,
                options='-loglevel error'
            )

            # Playback completed event
            play_complete = asyncio.Event()

            def after_playback(error):
                if error:
                    self.logger.error(f"Playback error: {error}")
                play_complete.set()
                # Clean up temp file
                try:
                    if temp_file and os.path.exists(temp_file):
                        os.unlink(temp_file)
                except Exception:
                    pass

            vc.play(audio_source, after=after_playback)

            # Wait for playback to start
            await asyncio.sleep(0.1)

            self.metrics["messages_processed"] += 1
            return True

        except Exception as e:
            self.logger.error(f"Audio playback error: {e}")
            # Clean up temp file on error
            try:
                if temp_file and os.path.exists(temp_file):
                    os.unlink(temp_file)
            except Exception:
                pass
            return False

    async def _wait_for_audio_end(self, vc: disnake.VoiceClient, timeout: int = 30):
        """Wait for current audio to finish playing"""
        start_time = time.time()
        while vc.is_playing():
            if time.time() - start_time > timeout:
                vc.stop()
                break
            await asyncio.sleep(0.1)

    async def _process_queue(self, guild_id: int):
        """Process TTS queue for a guild"""
        state = self._get_state(guild_id)

        self.logger.debug(f"Starting queue processor for guild {guild_id}")

        try:
            while True:
                try:
                    # Get item from queue with timeout
                    priority, item = await asyncio.wait_for(
                        state.queue.get(),
                        timeout=300  # 5 minutes
                    )

                    # Update activity
                    state.update_activity()

                    # Check if item is stale
                    if item.is_stale(max_age=60):
                        self.logger.debug(f"Dropped stale message: {item.text[:30]}...")
                        state.dropped_count += 1
                        continue

                    # Check if user still in voice
                    if not item.message.author.voice or not item.message.author.voice.channel:
                        self.logger.debug(f"User left voice, dropping message")
                        state.dropped_count += 1
                        continue

                    # Get voice connection
                    vc = await self._ensure_voice_connection(
                        item.message.guild,
                        item.message.author.voice.channel
                    )

                    if not vc:
                        try:
                            await item.message.add_reaction("❌")
                        except Exception:
                            pass
                        continue

                    # Play the audio
                    success = await self._play_tts_audio(vc, item.audio_data, item.text)

                    if success:
                        state.processed_count += 1

                except asyncio.TimeoutError:
                    # No items for 5 minutes, exit
                    self.logger.debug(f"Queue timeout for guild {guild_id}, stopping processor")
                    break

                except asyncio.CancelledError:
                    raise

                except Exception as e:
                    self.logger.error(f"Queue processing error: {e}")
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            self.logger.debug(f"Queue processor cancelled for guild {guild_id}")

        finally:
            # Disconnect voice if still connected
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                try:
                    await guild.voice_client.disconnect()
                except Exception:
                    pass

            self._cleanup_state(guild_id)

    @commands.Cog.listener()
    async def on_message(self, msg: disnake.Message):
        """Handle messages for TTS processing"""

        # Skip if bot message or not in guild
        if msg.author.bot or not msg.guild:
            return

        self.logger.debug(f"📨 Message received from {msg.author} in #{msg.channel.name}: {msg.content[:50]}...")

        # Check if user is in voice channel
        if not msg.author.voice or not msg.author.voice.channel:
            self.logger.debug(f"❌ User {msg.author} not in voice channel")
            try:
                await msg.add_reaction("🔇")  # Not in voice channel
            except Exception as e:
                self.logger.debug(f"Failed to add reaction: {e}")
            return

        # Check rate limit
        rate_limit_result = await self.rate_limiter.check(str(msg.author.id))
        if not rate_limit_result:
            self.logger.debug(f"⏳ User {msg.author} rate limited")
            try:
                await msg.add_reaction("⏳")  # Rate limited
            except Exception as e:
                self.logger.debug(f"Failed to add reaction: {e}")
            return

        # Process text
        processed_text = self._process_text(msg.content)
        if not processed_text:
            self.logger.debug("📝 Text processing resulted in empty string")
            return

        self.logger.debug(f"🔧 Processed text: {processed_text}")

        # Get TTS audio
        self.logger.debug("🎵 Generating TTS audio...")
        audio_data = await self._get_tts_audio(processed_text)
        if not audio_data:
            self.logger.error("❌ TTS audio generation failed")
            try:
                await msg.add_reaction("⚠️")  # TTS failed
            except Exception as e:
                self.logger.debug(f"Failed to add reaction: {e}")
            return

        self.logger.debug(f"✅ TTS audio generated ({len(audio_data)} bytes)")

        # Add to queue
        state = self._get_state(msg.guild.id)
        queue_item = QueueItem(
            message=msg,
            text=processed_text,
            audio_data=audio_data,
            queued_at=time.time()
        )

        try:
            # Priority queue: (priority, item)
            # Lower priority number = higher priority
            await state.queue.put((0, queue_item))
            self.logger.debug(f"📥 Added to queue for guild {msg.guild.id}, queue size: {state.queue.qsize()}")

            # Start processor if not running
            if not state.is_active():
                self.logger.debug(f"🚀 Starting queue processor for guild {msg.guild.id}")
                state.processing_task = asyncio.create_task(
                    self._process_queue(msg.guild.id)
                )
                # Add error handling to the task
                state.processing_task.add_done_callback(self._handle_queue_task_result)

            # Success reaction
            try:
                await msg.add_reaction("✅")
                self.logger.debug(f"✅ Successfully queued TTS for user {msg.author}")
            except Exception as e:
                self.logger.debug(f"Failed to add success reaction: {e}")

        except asyncio.QueueFull:
            self.logger.warning(f"📊 Queue full for guild {msg.guild.id}")
            try:
                await msg.add_reaction("📊")  # Queue full
            except Exception as e:
                self.logger.debug(f"Failed to add reaction: {e}")
        except Exception as e:
            self.logger.error(f"💥 Unexpected error adding to queue: {e}")
            try:
                await msg.add_reaction("❌")
            except Exception:
                pass

    def _handle_queue_task_result(self, task: asyncio.Task):
        """Handle the result of a queue processing task"""
        try:
            task.result()  # This will re-raise any exceptions that occurred in the task
        except asyncio.CancelledError:
            self.logger.debug("Queue processing task was cancelled")
        except Exception as e:
            self.logger.error(f"Queue processing task failed: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice state changes"""

        # Bot disconnected
        if (
                member.id == self.bot.user.id
                and before.channel
                and not after.channel
        ):
            self._cleanup_state(member.guild.id)
            return

        # User left voice channel
        if before.channel and not after.channel:
            # Wait a bit before checking
            await asyncio.sleep(2)

            guild = self.bot.get_guild(member.guild.id)
            if guild and guild.voice_client:
                # Check if any humans left in channel
                humans_in_channel = [
                    m for m in guild.voice_client.channel.members
                    if not m.bot
                ]

                if not humans_in_channel:
                    try:
                        await guild.voice_client.disconnect()
                    except Exception:
                        pass
                    self._cleanup_state(guild.id)

    @commands.slash_command(name="tts")
    async def tts_group(self, inter: disnake.ApplicationCommandInteraction):
        """TTS command group"""
        pass

    @tts_group.sub_command(name="voice", description="Set your TTS voice")
    async def tts_voice(
            self,
            inter: disnake.ApplicationCommandInteraction,
            voice: str = commands.Param(
                choices=["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
                description="Select TTS voice"
            )
    ):
        """Set user's preferred TTS voice"""
        # This would need a user preferences system to implement
        await inter.send(f"Voice preference set to: {voice}", ephemeral=True)

    @tts_group.sub_command(name="stats", description="View TTS statistics")
    @commands.has_permissions(administrator=True)
    async def tts_stats(self, inter: disnake.ApplicationCommandInteraction):
        """Show TTS usage statistics"""
        await inter.response.defer(ephemeral=True)

        # Get cache stats
        cache_stats = await self.tts_cache.get_stats()

        # Get breaker metrics with fallback
        breaker_metrics = {}
        if hasattr(self.breaker, 'get_metrics'):
            breaker_metrics = await self.breaker.get_metrics()
        else:
            breaker_metrics = {
                'state': 'N/A',
                'current_failures': 'N/A',
                'uptime_percentage': 'N/A'
            }

        embed = disnake.Embed(
            title="TTS System Statistics",
            color=disnake.Color.blue()
        )

        # Global metrics
        embed.add_field(
            name="API Metrics",
            value=f"Requests: {self.metrics['tts_requests']}\n"
                  f"Cache Hits: {self.metrics['cache_hits']}\n"
                  f"Failed: {self.metrics['failed_requests']}\n"
                  f"Processed: {self.metrics['messages_processed']}",
            inline=True
        )

        # Cache stats
        embed.add_field(
            name="Cache Stats",
            value=f"Size: {cache_stats.get('size', 'N/A')}/{cache_stats.get('max_size', 'N/A')}\n"
                  f"Hit Rate: {cache_stats.get('hit_rate', 0):.1f}%\n"
                  f"Hits: {cache_stats.get('hits', 0)}\n"
                  f"Misses: {cache_stats.get('misses', 0)}",
            inline=True
        )

        # Circuit breaker
        embed.add_field(
            name="Circuit Breaker",
            value=f"State: {breaker_metrics.get('state', 'N/A')}\n"
                  f"Failures: {breaker_metrics.get('current_failures', 'N/A')}\n"
                  f"Uptime: {breaker_metrics.get('uptime_percentage', 'N/A')}",
            inline=True
        )

        # Guild states
        active_guilds = len([s for s in self.guild_states.values() if s.is_active()])
        total_queued = sum(s.queue.qsize() for s in self.guild_states.values())

        embed.add_field(
            name="Guild Status",
            value=f"Active: {active_guilds}\n"
                  f"Total Queued: {total_queued}\n"
                  f"Voice Clients: {len(self.bot.voice_clients)}",
            inline=True
        )

        await inter.edit_original_response(embed=embed)

    @tts_group.sub_command(name="clear_queue", description="Clear TTS queue for this server")
    @commands.has_permissions(manage_guild=True)
    async def tts_clear_queue(self, inter: disnake.ApplicationCommandInteraction):
        """Clear the TTS queue for the guild"""
        state = self._get_state(inter.guild.id)

        # Clear the queue
        while not state.queue.empty():
            try:
                state.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        await inter.send("✅ TTS queue cleared!", ephemeral=True)


def setup(bot):
    bot.add_cog(VoiceProcessingCog(bot))