import asyncio
import os
import random
import re
import tempfile
import time
from typing import Dict, Optional, List, Tuple, Any
from collections import OrderedDict
import hashlib
import uuid

import aiohttp
import disnake
from disnake.ext import commands

# Constants
EMOJI_REGEX = re.compile(r"<:(\w+):\d+>")
MAX_RETRIES, RETRY_DELAY, MAX_QUEUE_SIZE = 3, 0.1, 100  # Faster retry
PRIORITY_USERS, MAX_TEXT_LENGTH, CACHE_SIZE = [], 2000, 300  # Larger cache
RATE_LIMIT_CLEANUP_INTERVAL, CONNECTION_TIMEOUT = 300, 10.0  # Faster connection timeout


class QueuedMessage:
    """Represents a message with its processing state and order"""
    __slots__ = ('message', 'text', 'audio_data', 'timestamp', 'message_id', 'created_at', 'priority', 'queue_position',
                 'sequence_id')

    def __init__(self, message: disnake.Message, text: str):
        self.message, self.text, self.audio_data = message, text, None
        self.timestamp, self.message_id = time.time(), message.id
        self.created_at = message.created_at.timestamp()
        self.priority = message.author.id in PRIORITY_USERS
        self.queue_position, self.sequence_id = 0, uuid.uuid4().int


def sanitize_text(text: str) -> str:
    """Sanitize text for TTS including emoji conversion - OPTIMIZED"""
    # Faster text processing
    text = text.strip()
    if not text:
        return ""

    # Single pass replacements for speed
    text = EMOJI_REGEX.sub(r":\1:", text)
    text = text.replace('@', 'at ').replace('#', 'hash ')
    text = re.sub(r'\s+', ' ', text)

    return text[:MAX_TEXT_LENGTH]


class VoiceClientManager:
    """Optimized voice connection manager for speed"""

    def __init__(self, bot: commands.Bot, logger):
        self.bot, self.logger = bot, logger
        self.voice_clients: Dict[int, disnake.VoiceClient] = {}
        self.connection_locks: Dict[int, asyncio.Lock] = {}
        self.last_activity: Dict[int, float] = {}
        self.connection_cache: Dict[int, disnake.VoiceChannel] = {}  # Cache last channel

    def _get_connection_lock(self, guild_id: int) -> asyncio.Lock:
        """Get or create connection lock for guild"""
        return self.connection_locks.setdefault(guild_id, asyncio.Lock())

    async def get_voice_client(self, guild_id: int, channel: disnake.VoiceChannel) -> Optional[disnake.VoiceClient]:
        """Get or create voice client - OPTIMIZED for speed"""
        async with self._get_connection_lock(guild_id):
            guild = self.bot.get_guild(guild_id)
            if not guild:
                self.logger.error(f"Guild {guild_id} not found")
                return None

            existing_vc = guild.voice_client
            current_time = time.time()
            self.last_activity[guild_id] = current_time

            # FAST PATH: Already connected to correct channel
            if (existing_vc and isinstance(existing_vc, disnake.VoiceClient) and
                    existing_vc.is_connected() and
                    existing_vc.channel and
                    existing_vc.channel.id == channel.id):
                self.voice_clients[guild_id] = existing_vc
                return existing_vc

            # MEDIUM PATH: Connected but wrong channel - try fast move
            if (existing_vc and isinstance(existing_vc, disnake.VoiceClient) and
                    existing_vc.is_connected()):
                try:
                    await existing_vc.move_to(channel)
                    self.voice_clients[guild_id] = existing_vc
                    self.logger.info(f"🔀 Fast moved to {channel.name}")
                    return existing_vc
                except (disnake.ClientException, asyncio.TimeoutError):
                    # Move failed, continue to create new connection
                    pass

            # SLOW PATH: Create new connection with optimized timeout
            try:
                self.logger.info(f"🔊 Fast connecting to {channel.name}...")
                vc = await asyncio.wait_for(
                    channel.connect(reconnect=True, timeout=20.0),
                    timeout=25.0
                )

                if vc and vc.is_connected():
                    self.voice_clients[guild_id] = vc
                    self.last_activity[guild_id] = current_time
                    self.logger.info(f"✅ Connected to {channel.name}")
                    return vc

            except (asyncio.TimeoutError, disnake.ClientException, Exception) as e:
                self.logger.error(f"Connection failed: {e}")

            return None

    async def disconnect(self, guild_id: int):
        """Fast disconnect from voice channel"""
        async with self._get_connection_lock(guild_id):
            if guild_id in self.voice_clients:
                vc = self.voice_clients[guild_id]
                try:
                    if vc and vc.is_connected():
                        await vc.disconnect(force=True)
                except Exception:
                    pass  # Silent cleanup
                finally:
                    if guild_id in self.voice_clients:
                        del self.voice_clients[guild_id]
                    if guild_id in self.last_activity:
                        del self.last_activity[guild_id]

    async def disconnect_all(self):
        """Fast disconnect all voice clients"""
        tasks = [self.disconnect(guild_id) for guild_id in list(self.voice_clients.keys())]
        await asyncio.gather(*tasks, return_exceptions=True)

    def is_connected(self, guild_id: int) -> bool:
        """Fast connection check"""
        if guild_id in self.voice_clients:
            vc = self.voice_clients[guild_id]
            return vc and vc.is_connected()
        return False

    async def health_check(self):
        """Fast health check"""
        current_time = time.time()
        for guild_id, vc in list(self.voice_clients.items()):
            if not vc or not vc.is_connected():
                await self.disconnect(guild_id)
            elif current_time - self.last_activity.get(guild_id, 0) > 1800:  # 30 minutes
                await self.disconnect(guild_id)


def enhance_short_messages(text: str) -> str:
    """Optimized short message enhancement"""
    words = text.strip().split()
    if len(words) <= 2:
        # Fast lookup dictionary
        short_responses = {
            'yes': 'yes.', 'no': 'no.', 'ok': 'okay.', 'k': 'okay.',
            'lol': 'laughing out loud', 'lmao': 'laughing my ass off',
            'brb': 'be right back', 'omg': 'oh my god',
            'gg': 'good game', 'wp': 'well played', 'gl': 'good luck',
            'hf': 'have fun', 'ty': 'thank you', 'thx': 'thanks',
            'np': 'no problem', 'afk': 'away from keyboard'
        }
        lower_word = words[0].lower() if words else ""
        if len(words) == 1 and lower_word in short_responses:
            return short_responses[lower_word]
        return f"{text}." if text else text
    return text


class LRUCache:
    """Optimized LRU Cache for speed"""

    def __init__(self, max_size: int):
        self.max_size, self.cache = max_size, OrderedDict()

    def get(self, key: str) -> Optional[bytes]:
        """Fast cache get"""
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def set(self, key: str, value: bytes):
        """Fast cache set"""
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    def __contains__(self, key: str) -> bool:
        return key in self.cache

    def __len__(self) -> int:
        return len(self.cache)


class EnhancedTTSProcessor:
    """ULTRA-FAST TTS processor with cost optimization"""

    def __init__(self, config, logger):
        self.logger = logger
        self.tts_url, self.tts_token = config.tts.api_url, config.tts.bearer_token
        self.tts_model, self.default_voice = config.tts.engine, config.tts.default_voice
        self.available_voices = config.tts.voices.get("available_voices", [])
        self.user_voice_map = config.tts.voices.get("user_voice_mappings", {})
        self.retry_limit = 2  # Reduced for speed

        # Optimized for Discord compatibility and speed
        self.preferred_format, self.file_extension = "wav", ".wav"
        self.audio_cache, self.cache_lock = LRUCache(CACHE_SIZE), asyncio.Lock()
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.session_creation_lock = asyncio.Lock()

        # Cost optimization - track usage
        self.total_requests = 0
        self.cache_hits = 0

    async def get_http_session(self) -> aiohttp.ClientSession:
        """Get optimized HTTP session"""
        async with self.session_creation_lock:
            if self.http_session is None or self.http_session.closed:
                # Optimized for speed - shorter timeouts
                timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_connect=5, sock_read=10)
                connector = aiohttp.TCPConnector(limit=5, keepalive_timeout=15)  # Smaller pool for speed
                self.http_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
            return self.http_session

    async def close_http_session(self):
        """Fast session close"""
        async with self.session_creation_lock:
            if self.http_session and not self.http_session.closed:
                await self.http_session.close()
                self.http_session = None

    async def generate_audio(self, text: str, user_id: int) -> Optional[bytes]:
        """ULTRA-FAST TTS generation with cost tracking"""
        # FAST: Check voice channel FIRST to avoid wasted API calls
        enhanced_text = self._optimize_text_for_tts(text)
        if not enhanced_text:
            return None

        voice_id = self.user_voice_map.get(str(user_id)) or self.default_voice or (
            random.choice(self.available_voices) if self.available_voices else None)

        if not voice_id:
            return None

        # FAST: Cache check with cost tracking
        text_hash = hashlib.md5(f"{voice_id}:{enhanced_text}".encode()).hexdigest()
        cache_key = f"{voice_id}:{text_hash}"

        async with self.cache_lock:
            if cached_audio := self.audio_cache.get(cache_key):
                self.cache_hits += 1
                self.logger.debug(f"⚡ CACHE HIT: {enhanced_text[:30]}...")
                return cached_audio

        # Generate new audio - FAST with single attempt for most cases
        self.total_requests += 1
        audio_data = await self._call_tts_api(enhanced_text, voice_id)

        if audio_data:
            async with self.cache_lock:
                self.audio_cache.set(cache_key, audio_data)

        # Log cost efficiency
        cache_efficiency = (self.cache_hits / self.total_requests) * 100 if self.total_requests > 0 else 0
        if self.total_requests % 10 == 0:  # Log every 10 requests
            self.logger.info(
                f"💰 Cost Efficiency: {cache_efficiency:.1f}% cache hits ({self.cache_hits}/{self.total_requests})")

        return audio_data

    async def _call_tts_api(self, text: str, voice_id: str) -> Optional[bytes]:
        """Fast TTS API call with single retry"""
        headers = {"Authorization": f"Bearer {self.tts_token}", "Content-Type": "application/json"}
        payload = {
            "model": self.tts_model,
            "input": text,
            "voice": voice_id,
            "response_format": self.preferred_format,
            "speed": 1.0  # Standard speed for reliability
        }

        for attempt in range(2):  # Single retry for speed
            try:
                http = await self.get_http_session()
                async with http.post(self.tts_url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        audio_data = await response.read()
                        self.logger.info(f"✅ TTS generated: {text[:40]}...")
                        return audio_data
                    else:
                        if response.status >= 400 and response.status < 500:
                            error_text = await response.text()
                            self.logger.error(f"❌ TTS API error {response.status}: {error_text}")
                            break  # Don't retry client errors
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                self.logger.warning(f"🌐 TTS network error (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(0.1)  # Very short delay

        return None

    @staticmethod
    def _optimize_text_for_tts(text: str) -> str:
        """Fast text optimization"""
        text = text.strip()
        if not text:
            return ""

        # Fast punctuation check and add
        if text and text[-1] not in '.!?':
            text += '.'

        return enhance_short_messages(text)

    def get_cost_stats(self) -> Dict[str, Any]:
        """Get cost efficiency statistics"""
        efficiency = (self.cache_hits / self.total_requests) * 100 if self.total_requests > 0 else 0
        return {
            "total_requests": self.total_requests,
            "cache_hits": self.cache_hits,
            "cache_efficiency": f"{efficiency:.1f}%",
            "api_calls_saved": self.cache_hits,
            "current_cache_size": len(self.audio_cache)
        }


class VoiceProcessingCog(commands.Cog):
    """ULTRA-FAST TTS Processing System"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = getattr(bot, 'config', None)
        self.logger = getattr(bot, 'logger', None)

        if not self.cfg or not self.logger:
            raise ValueError("Bot is missing required 'config' or 'logger' attributes")

        # Initialize optimized components
        self.voice_manager = VoiceClientManager(bot, self.logger)
        self.tts_processor = EnhancedTTSProcessor(self.cfg, self.logger)

        # Optimized queue management
        self.guild_queues: Dict[int, asyncio.Queue] = {}
        self.guild_processing: Dict[int, bool] = {}
        self.guild_processing_tasks: Dict[int, asyncio.Task] = {}

        # Fast message ordering
        self.guild_message_order: Dict[int, List[Tuple[int, asyncio.Future]]] = {}
        self.guild_order_locks: Dict[int, asyncio.Lock] = {}
        self.next_sequence_id: Dict[int, int] = {}
        self.user_requests: Dict[int, List[float]] = {}
        self.rate_limit, self.rate_window = 15, 60  # Slightly higher limit for speed

        # Background tasks
        self.rate_cleanup_task: Optional[asyncio.Task] = None
        self.health_monitor_task: Optional[asyncio.Task] = None

        self.logger.info("🚀 ULTRA-FAST VoiceProcessingCog initialized!")

    def _check_rate_limit(self, user_id: int) -> bool:
        """Fast rate limit check"""
        now = time.time()
        if user_id not in self.user_requests:
            self.user_requests[user_id] = []

        # Fast cleanup and check
        self.user_requests[user_id] = [t for t in self.user_requests[user_id] if now - t < self.rate_window]

        if len(self.user_requests[user_id]) < self.rate_limit:
            self.user_requests[user_id].append(now)
            return True
        return False

    async def _cleanup_rate_limits(self):
        """Fast rate limit cleanup"""
        while True:
            await asyncio.sleep(RATE_LIMIT_CLEANUP_INTERVAL)
            now = time.time()
            for user_id in list(self.user_requests.keys()):
                self.user_requests[user_id] = [t for t in self.user_requests[user_id] if now - t < self.rate_window * 2]
                if not self.user_requests[user_id]:
                    del self.user_requests[user_id]

    async def _monitor_system_health(self):
        """Fast health monitoring"""
        while True:
            await asyncio.sleep(30)  # More frequent but faster checks
            try:
                await self.voice_manager.health_check()

                # Fast queue processor restart
                for guild_id, task in list(self.guild_processing_tasks.items()):
                    if task and task.done():
                        queue = self.guild_queues.get(guild_id)
                        if queue and not queue.empty():
                            self.guild_processing_tasks[guild_id] = asyncio.create_task(
                                self._process_guild_queue(guild_id))

            except Exception as e:
                self.logger.debug(f"Health monitor: {e}")

    def _get_guild_queue(self, guild_id: int) -> asyncio.Queue:
        """Fast queue get/create"""
        return self.guild_queues.setdefault(guild_id, asyncio.Queue(maxsize=MAX_QUEUE_SIZE))

    def _get_guild_order_lock(self, guild_id: int) -> asyncio.Lock:
        """Fast lock get/create"""
        return self.guild_order_locks.setdefault(guild_id, asyncio.Lock())

    def _get_next_sequence_id(self, guild_id: int) -> int:
        """Fast sequence ID"""
        self.next_sequence_id[guild_id] = self.next_sequence_id.get(guild_id, 0) + 1
        return self.next_sequence_id[guild_id]

    async def _process_message(self, message: disnake.Message):
        """ULTRA-FAST message processing"""
        sequence_id = 0
        try:
            # 🚀 CRITICAL OPTIMIZATION: Check VC FIRST to save API costs!
            if not message.author.voice or not message.author.voice.channel:
                self.logger.debug(f"🚫 User {message.author} not in VC - skipping")
                return

            text = sanitize_text(message.content)
            if not text or len(text.strip()) < 1:
                return

            if not self._check_rate_limit(message.author.id):
                return

            guild_id = message.guild.id
            sequence_id = self._get_next_sequence_id(guild_id)
            future = asyncio.Future()

            async with self._get_guild_order_lock(guild_id):
                if guild_id not in self.guild_message_order:
                    self.guild_message_order[guild_id] = []
                self.guild_message_order[guild_id].append((sequence_id, future))

                if len(self.guild_message_order[guild_id]) == 1:
                    future.set_result(True)

            await future

            # Generate TTS (now that we know user is in VC)
            audio_data = await self.tts_processor.generate_audio(text, message.author.id)
            if not audio_data:
                await self._cleanup_order_queue(guild_id, sequence_id)
                return

            queue = self._get_guild_queue(guild_id)
            try:
                queue.put_nowait((message, text, audio_data, sequence_id))

                if (guild_id not in self.guild_processing or not self.guild_processing[guild_id] or
                        self.guild_processing_tasks.get(guild_id) is None or self.guild_processing_tasks[
                            guild_id].done()):
                    self.guild_processing_tasks[guild_id] = asyncio.create_task(self._process_guild_queue(guild_id))

            except asyncio.QueueFull:
                await self._cleanup_order_queue(guild_id, sequence_id)

        except Exception as e:
            self.logger.debug(f"Message processing error: {e}")
            try:
                await self._cleanup_order_queue(message.guild.id, sequence_id)
            except Exception:
                pass

    async def _cleanup_order_queue(self, guild_id: int, current_sequence_id: int):
        """Fast order queue cleanup"""
        async with self._get_guild_order_lock(guild_id):
            if guild_id in self.guild_message_order:
                self.guild_message_order[guild_id] = [
                    (seq, fut) for seq, fut in self.guild_message_order[guild_id]
                    if seq != current_sequence_id
                ]
                if self.guild_message_order[guild_id] and not self.guild_message_order[guild_id][0][1].done():
                    self.guild_message_order[guild_id][0][1].set_result(True)

    async def _cleanup_playback(self, guild_id: int, sequence_id: int, error: Optional[Exception]):
        """Fast playback cleanup"""
        if error:
            self.logger.debug(f"Playback error: {error}")
        await self._cleanup_order_queue(guild_id, sequence_id)

    async def _process_guild_queue(self, guild_id: int):
        """ULTRA-FAST guild queue processing"""
        queue = self._get_guild_queue(guild_id)
        self.guild_processing[guild_id] = True

        while True:
            try:
                if not self.guild_processing.get(guild_id, False):
                    break

                try:
                    message, text, audio_data, sequence_id = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    if queue.empty():
                        break
                    continue

                voice_channel = message.author.voice.channel

                # Fast voice connection
                vc = await asyncio.wait_for(
                    self.voice_manager.get_voice_client(guild_id, voice_channel),
                    timeout=CONNECTION_TIMEOUT
                )

                if not vc:
                    await self._cleanup_order_queue(guild_id, sequence_id)
                    queue.task_done()
                    continue

                # Fast playback with minimal delay
                if vc.is_playing():
                    for _ in range(150):  # 15 second timeout (reduced)
                        if not vc.is_playing():
                            break
                        await asyncio.sleep(0.1)
                    else:
                        vc.stop()

                # Fast audio playback
                tmp_file = None
                try:
                    tmp_file = tempfile.mktemp(suffix=self.tts_processor.file_extension)
                    with open(tmp_file, 'wb') as f:
                        f.write(audio_data)

                    source = disnake.FFmpegPCMAudio(
                        tmp_file,
                        options="-f wav -ac 2 -ar 48000 -loglevel quiet"  # Quieter for speed
                    )

                    def after_playing(error: Optional[Exception]) -> None:
                        try:
                            if tmp_file and os.path.exists(tmp_file):
                                os.unlink(tmp_file)
                        except Exception:
                            pass
                        if self.bot.loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self._cleanup_playback(guild_id, sequence_id, error),
                                self.bot.loop
                            )

                    vc.play(source, after=after_playing)

                except Exception as e:
                    self.logger.debug(f"Playback error: {e}")
                    if tmp_file and os.path.exists(tmp_file):
                        try:
                            os.unlink(tmp_file)
                        except Exception:
                            pass
                    await self._cleanup_order_queue(guild_id, sequence_id)

                queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.debug(f"Queue processor error: {e}")
                await asyncio.sleep(0.5)

        self.guild_processing[guild_id] = False

    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        """Fast message handler"""
        if (message.author.bot or not message.guild or
                (hasattr(self.cfg.discord, 'channel_id') and self.cfg.discord.channel_id and
                 message.channel.id != self.cfg.discord.channel_id) or
                message.content.startswith(('!', '/', '.'))):
            return

        # Fast async processing without await
        asyncio.create_task(self._process_message(message))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState,
                                    after: disnake.VoiceState):
        """Fast voice state handler"""
        if member.bot:
            return

        guild_id = member.guild.id

        if member.id == self.bot.user.id:
            if before.channel and not after.channel:
                await self.voice_manager.disconnect(guild_id)
            return

        if before.channel and not after.channel:
            if self.voice_manager.is_connected(guild_id):
                vc = self.voice_manager.voice_clients.get(guild_id)
                if vc and vc.channel and vc.channel.id == before.channel.id:
                    if not any(not m.bot for m in before.channel.members):
                        await asyncio.sleep(1)
                        if self.voice_manager.is_connected(guild_id) and not any(not m.bot for m in vc.channel.members):
                            await self.voice_manager.disconnect(guild_id)

    def get_performance_stats(self) -> Dict[str, Any]:
        """Comprehensive performance and cost statistics"""
        tts_stats = self.tts_processor.get_cost_stats()
        stats = {
            "active_queues": len([q for q in self.guild_queues.values() if not q.empty()]),
            "voice_connections": len(self.voice_manager.voice_clients),
            "rate_limited_users": len(self.user_requests),
            **tts_stats
        }
        return stats

    @commands.slash_command(name="tts_stats", description="Check TTS performance and cost statistics")
    async def tts_stats(self, inter: disnake.ApplicationCommandInteraction):
        """Display performance and cost statistics"""
        stats = self.get_performance_stats()
        embed = disnake.Embed(title="🚀 TTS Performance & Cost Stats", color=disnake.Color.green())

        for key, value in stats.items():
            embed.add_field(name=key.replace('_', ' ').title(), value=str(value), inline=True)

        await inter.response.send_message(embed=embed)

    @commands.slash_command(name="tts_reset", description="Reset TTS system for this guild")
    async def tts_reset(self, inter: disnake.ApplicationCommandInteraction):
        """Fast reset"""
        guild_id = inter.guild_id

        await self.voice_manager.disconnect(guild_id)

        if guild_id in self.guild_queues:
            queue = self.guild_queues[guild_id]
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except Exception:
                    pass

        async with self._get_guild_order_lock(guild_id):
            if guild_id in self.guild_message_order:
                for seq, future in self.guild_message_order[guild_id]:
                    if not future.done():
                        future.cancel()
                self.guild_message_order[guild_id].clear()

        self.guild_processing[guild_id] = False
        if guild_id in self.guild_processing_tasks:
            task = self.guild_processing_tasks[guild_id]
            if not task.done():
                task.cancel()
            del self.guild_processing_tasks[guild_id]

        await inter.response.send_message("✅ TTS system reset!", ephemeral=True)

    async def cog_load(self):
        """Fast cog load"""
        self.rate_cleanup_task = asyncio.create_task(self._cleanup_rate_limits())
        self.health_monitor_task = asyncio.create_task(self._monitor_system_health())

    async def cog_unload(self):
        """Fast cog unload"""
        self.guild_processing.clear()

        for guild_id, message_list in self.guild_message_order.items():
            for sequence_id, future in message_list:
                if not future.done():
                    future.cancel()

        tasks_to_cancel = []
        if self.rate_cleanup_task:
            tasks_to_cancel.append(self.rate_cleanup_task)
        if self.health_monitor_task:
            tasks_to_cancel.append(self.health_monitor_task)
        for task in self.guild_processing_tasks.values():
            if task and not task.done():
                tasks_to_cancel.append(task)

        for task in tasks_to_cancel:
            task.cancel()

        if tasks_to_cancel:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks_to_cancel, return_exceptions=True), timeout=3.0)
            except Exception:
                pass

        for queue in self.guild_queues.values():
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except Exception:
                    pass

        await self.tts_processor.close_http_session()
        await self.voice_manager.disconnect_all()


def setup(bot: commands.Bot):
    bot.add_cog(VoiceProcessingCog(bot))