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
MAX_RETRIES, RETRY_DELAY, MAX_QUEUE_SIZE = 3, 0.1, 100
PRIORITY_USERS, MAX_TEXT_LENGTH, CACHE_SIZE = [], 2000, 300
RATE_LIMIT_CLEANUP_INTERVAL, CONNECTION_TIMEOUT = 300, 10.0


class VoiceClientManager:
    """SUPER ROBUST voice connection manager - ENHANCED STATE DETECTION"""

    def __init__(self, bot: commands.Bot, logger):
        self.bot, self.logger = bot, logger
        self.voice_clients: Dict[int, disnake.VoiceClient] = {}
        self.connection_locks: Dict[int, asyncio.Lock] = {}
        self.last_activity: Dict[int, float] = {}
        self.connection_attempts: Dict[int, int] = {}  # Track failed attempts

    def _get_connection_lock(self, guild_id: int) -> asyncio.Lock:
        """Get or create connection lock for guild"""
        return self.connection_locks.setdefault(guild_id, asyncio.Lock())

    def _is_actually_connected(self, guild_id: int, target_channel_id: int = None) -> bool:
        """SUPER ACCURATE connection check - uses Discord's actual state"""
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            return False

        vc = guild.voice_client
        is_connected = (
                vc and
                isinstance(vc, disnake.VoiceClient) and
                vc.is_connected()
        )

        # If we're checking a specific channel, verify we're in the right one
        if is_connected and target_channel_id:
            return vc.channel and vc.channel.id == target_channel_id

        return is_connected

    async def _verify_connection_health(self, vc: disnake.VoiceClient) -> bool:
        """Verify the connection is actually working"""
        try:
            # Check if we can get the WS connection state
            return (vc.is_connected() and
                    hasattr(vc, 'ws') and
                    vc.ws and
                    not vc.ws.closed)
        except Exception:
            return False

    async def _force_cleanup_connection(self, guild_id: int):
        """FORCE cleanup of any connection state"""
        guild = self.bot.get_guild(guild_id)
        if guild and guild.voice_client:
            try:
                await guild.voice_client.disconnect(force=True)
                self.logger.info("🛑 Force disconnected existing connection")
            except Exception as e:
                self.logger.debug(f"Force disconnect had issue: {e}")
            await asyncio.sleep(0.5)  # Give time for cleanup

    async def get_voice_client(self, guild_id: int, channel: disnake.VoiceChannel) -> Optional[disnake.VoiceClient]:
        """SUPER ROBUST voice client getter with state verification"""
        async with self._get_connection_lock(guild_id):
            guild = self.bot.get_guild(guild_id)
            if not guild:
                self.logger.error(f"Guild {guild_id} not found")
                return None

            # ENHANCED: Double-check Discord's actual voice state
            bot_member = guild.get_member(self.bot.user.id)
            if bot_member and bot_member.voice and bot_member.voice.channel:
                actual_channel = bot_member.voice.channel
                if actual_channel.id == channel.id:
                    self.logger.info(f"✅ Already connected to correct channel: {channel.name}")
                    self.voice_clients[guild_id] = guild.voice_client
                    self.last_activity[guild_id] = time.time()
                    return guild.voice_client
                else:
                    self.logger.info(f"🔄 Actually in different channel: {actual_channel.name} vs {channel.name}")

            # STEP 1: Check if we're ALREADY connected to the RIGHT channel
            if self._is_actually_connected(guild_id, channel.id):
                self.logger.info(f"✅ Already connected to {channel.name}")
                self.voice_clients[guild_id] = guild.voice_client
                self.last_activity[guild_id] = time.time()
                return guild.voice_client

            # STEP 2: If connected to WRONG channel, try to move
            if self._is_actually_connected(guild_id):
                try:
                    self.logger.info(f"🔀 Moving from {guild.voice_client.channel.name} to {channel.name}")
                    await guild.voice_client.move_to(channel)
                    self.voice_clients[guild_id] = guild.voice_client
                    self.last_activity[guild_id] = time.time()
                    self.logger.info(f"✅ Moved to {channel.name}")
                    return guild.voice_client
                except Exception as e:
                    self.logger.warning(f"Move failed: {e}. Force reconnecting...")
                    await self._force_cleanup_connection(guild_id)

            # STEP 3: Clean connection attempt
            try:
                # Ensure any stale connections are cleaned up
                if guild.voice_client:
                    await self._force_cleanup_connection(guild_id)

                self.logger.info(f"🔊 Connecting to {channel.name}...")
                vc = await channel.connect(reconnect=True, timeout=15.0)

                if vc and vc.is_connected():
                    self.voice_clients[guild_id] = vc
                    self.last_activity[guild_id] = time.time()
                    self.logger.info(f"✅ Successfully connected to {channel.name}")
                    return vc

            except disnake.ClientException as e:
                if "Already connected" in str(e):
                    self.logger.warning("🔄 Bot thinks it's already connected somewhere else")
                    # Force cleanup and retry once
                    await self._force_cleanup_connection(guild_id)
                    try:
                        vc = await channel.connect(reconnect=True, timeout=15.0)
                        if vc and vc.is_connected():
                            self.voice_clients[guild_id] = vc
                            self.last_activity[guild_id] = time.time()
                            self.logger.info(f"✅ Reconnected to {channel.name}")
                            return vc
                    except Exception as retry_error:
                        self.logger.error(f"❌ Reconnection failed: {retry_error}")
                else:
                    self.logger.error(f"❌ Connection failed: {e}")

            except (asyncio.TimeoutError, Exception) as e:
                self.logger.error(f"❌ Connection failed: {e}")

            # Clean up on complete failure
            if guild_id in self.voice_clients:
                del self.voice_clients[guild_id]

            return None

    async def disconnect(self, guild_id: int):
        """Force disconnect from voice channel"""
        async with self._get_connection_lock(guild_id):
            await self._force_cleanup_connection(guild_id)

            if guild_id in self.voice_clients:
                del self.voice_clients[guild_id]
            if guild_id in self.last_activity:
                del self.last_activity[guild_id]
            if guild_id in self.connection_attempts:
                del self.connection_attempts[guild_id]

    async def disconnect_all(self):
        """Disconnect from all voice channels"""
        for guild_id in list(self.voice_clients.keys()):
            await self.disconnect(guild_id)

    def is_connected(self, guild_id: int) -> bool:
        """ACCURATE connection check using Discord's state"""
        return self._is_actually_connected(guild_id)

    async def cleanup_all_stale_connections(self):
        """Clean up ALL stale connections across all guilds"""
        for guild in self.bot.guilds:
            if guild.voice_client and guild.voice_client.is_connected():
                # Check if this is a stale connection (not in our managed list)
                if guild.id not in self.voice_clients:
                    self.logger.info(f"🧹 Cleaning up stale connection in {guild.name}")
                    try:
                        await guild.voice_client.disconnect(force=True)
                    except Exception:
                        pass


def sanitize_text(text: str) -> str:
    """Sanitize text for TTS"""
    text = text.strip()
    if not text:
        return ""

    text = EMOJI_REGEX.sub(r":\1:", text)
    text = text.replace('@', 'at ').replace('#', 'hash ')
    text = re.sub(r'\s+', ' ', text)

    return text[:MAX_TEXT_LENGTH]


def enhance_short_messages(text: str) -> str:
    """Enhance short messages"""
    words = text.strip().split()
    if len(words) <= 2:
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
    """LRU Cache"""

    def __init__(self, max_size: int):
        self.max_size, self.cache = max_size, OrderedDict()

    def get(self, key: str) -> Optional[bytes]:
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def set(self, key: str, value: bytes):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)


class EnhancedTTSProcessor:
    """TTS processor"""

    def __init__(self, config, logger):
        self.logger = logger
        self.tts_url, self.tts_token = config.tts.api_url, config.tts.bearer_token
        self.tts_model, self.default_voice = config.tts.engine, config.tts.default_voice
        self.available_voices = config.tts.voices.get("available_voices", [])
        self.user_voice_map = config.tts.voices.get("user_voice_mappings", {})

        self.preferred_format, self.file_extension = "wav", ".wav"
        self.audio_cache, self.cache_lock = LRUCache(CACHE_SIZE), asyncio.Lock()
        self.http_session: Optional[aiohttp.ClientSession] = None

        self.total_requests = 0
        self.cache_hits = 0

    async def get_http_session(self) -> aiohttp.ClientSession:
        if self.http_session is None or self.http_session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_connect=5, sock_read=10)
            self.http_session = aiohttp.ClientSession(timeout=timeout)
        return self.http_session

    async def close_http_session(self):
        """Close HTTP session"""
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()

    async def generate_audio(self, text: str, user_id: int) -> Optional[bytes]:
        enhanced_text = self._optimize_text_for_tts(text)
        if not enhanced_text:
            return None

        voice_id = self.user_voice_map.get(str(user_id)) or self.default_voice or (
            random.choice(self.available_voices) if self.available_voices else None)

        if not voice_id:
            return None

        text_hash = hashlib.md5(f"{voice_id}:{enhanced_text}".encode()).hexdigest()
        cache_key = f"{voice_id}:{text_hash}"

        async with self.cache_lock:
            if cached_audio := self.audio_cache.get(cache_key):
                self.cache_hits += 1
                self.logger.debug(f"⚡ CACHE HIT: {enhanced_text[:30]}...")
                return cached_audio

        self.total_requests += 1

        # Enhanced TTS generation with retries
        max_retries = 2
        audio_data = None

        for attempt in range(max_retries):
            audio_data = await self._call_tts_api(enhanced_text, voice_id)
            if audio_data:
                break
            elif attempt < max_retries - 1:
                self.logger.warning(f"🔄 TTS generation failed, retrying... ({attempt + 1}/{max_retries})")
                await asyncio.sleep(0.5)

        if audio_data:
            async with self.cache_lock:
                self.audio_cache.set(cache_key, audio_data)

        return audio_data

    async def _call_tts_api(self, text: str, voice_id: str) -> Optional[bytes]:
        headers = {"Authorization": f"Bearer {self.tts_token}", "Content-Type": "application/json"}
        payload = {
            "model": self.tts_model,
            "input": text,
            "voice": voice_id,
            "response_format": self.preferred_format,
            "speed": 1.0
        }

        for attempt in range(2):
            try:
                http = await self.get_http_session()
                async with http.post(self.tts_url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        audio_data = await response.read()
                        self.logger.info(f"✅ TTS generated: {text[:40]}...")
                        return audio_data
                    else:
                        error_text = await response.text()
                        self.logger.error(f"❌ TTS API error {response.status}: {error_text}")
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                self.logger.warning(f"🌐 TTS network error (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(0.1)

        return None

    @staticmethod
    def _optimize_text_for_tts(text: str) -> str:
        text = text.strip()
        if not text:
            return ""

        if text and text[-1] not in '.!?':
            text += '.'

        return enhance_short_messages(text)


class VoiceProcessingCog(commands.Cog):
    """TTS Processing System - ULTRA ROBUST VERSION"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = getattr(bot, 'config', None)
        self.logger = getattr(bot, 'logger', None)

        if not self.cfg or not self.logger:
            raise ValueError("Bot is missing required 'config' or 'logger' attributes")

        # Initialize components
        self.voice_manager = VoiceClientManager(bot, self.logger)
        self.tts_processor = EnhancedTTSProcessor(self.cfg, self.logger)

        # Queue management
        self.guild_queues: Dict[int, asyncio.Queue] = {}
        self.guild_processing: Dict[int, bool] = {}
        self.guild_processing_tasks: Dict[int, asyncio.Task] = {}

        # Message ordering
        self.guild_message_order: Dict[int, List[Tuple[int, asyncio.Future]]] = {}
        self.guild_order_locks: Dict[int, asyncio.Lock] = {}
        self.next_sequence_id: Dict[int, int] = {}
        self.user_requests: Dict[int, List[float]] = {}
        self.rate_limit, self.rate_window = 15, 60

        # Background tasks
        self.rate_cleanup_task: Optional[asyncio.Task] = None
        self.health_monitor_task: Optional[asyncio.Task] = None
        self.connection_cleanup_task: Optional[asyncio.Task] = None

        self.logger.info("🚀 ULTRA ROBUST VoiceProcessingCog initialized!")

    def _check_rate_limit(self, user_id: int) -> bool:
        now = time.time()
        if user_id not in self.user_requests:
            self.user_requests[user_id] = []

        self.user_requests[user_id] = [t for t in self.user_requests[user_id] if now - t < self.rate_window]

        if len(self.user_requests[user_id]) < self.rate_limit:
            self.user_requests[user_id].append(now)
            return True
        return False

    def _get_guild_queue(self, guild_id: int) -> asyncio.Queue:
        return self.guild_queues.setdefault(guild_id, asyncio.Queue(maxsize=MAX_QUEUE_SIZE))

    def _get_guild_order_lock(self, guild_id: int) -> asyncio.Lock:
        return self.guild_order_locks.setdefault(guild_id, asyncio.Lock())

    def _get_next_sequence_id(self, guild_id: int) -> int:
        self.next_sequence_id[guild_id] = self.next_sequence_id.get(guild_id, 0) + 1
        return self.next_sequence_id[guild_id]

    async def _cleanup_order_queue(self, guild_id: int, current_sequence_id: int):
        async with self._get_guild_order_lock(guild_id):
            if guild_id in self.guild_message_order:
                self.guild_message_order[guild_id] = [
                    (seq, fut) for seq, fut in self.guild_message_order[guild_id]
                    if seq != current_sequence_id
                ]
                if self.guild_message_order[guild_id] and not self.guild_message_order[guild_id][0][1].done():
                    self.guild_message_order[guild_id][0][1].set_result(True)

    async def _process_message(self, message: disnake.Message):
        """Message processing with connection state verification"""
        sequence_id = 0
        try:
            # Check if user is in voice channel
            if not message.author.voice or not message.author.voice.channel:
                self.logger.debug(f"🚫 User {message.author} not in VC")
                return

            text = sanitize_text(message.content)
            if not text or len(text.strip()) < 1:
                return

            if not self._check_rate_limit(message.author.id):
                self.logger.debug(f"🚫 Rate limited: {message.author}")
                return

            guild_id = message.guild.id

            # Generate TTS with enhanced retry logic
            self.logger.info(f"🎯 Generating TTS for: {text[:30]}...")
            audio_data = await self.tts_processor.generate_audio(text, message.author.id)
            if not audio_data:
                self.logger.warning(f"❌ Failed to generate TTS for: {text[:30]}...")
                return

            # Setup sequencing
            sequence_id = self._get_next_sequence_id(guild_id)
            future = asyncio.Future()

            async with self._get_guild_order_lock(guild_id):
                if guild_id not in self.guild_message_order:
                    self.guild_message_order[guild_id] = []
                self.guild_message_order[guild_id].append((sequence_id, future))

                if len(self.guild_message_order[guild_id]) == 1:
                    future.set_result(True)

            await future

            # Queue the message
            queue = self._get_guild_queue(guild_id)
            try:
                queue.put_nowait((message, text, audio_data, sequence_id))
                self.logger.info(f"✅ Queued message: {text[:30]}...")

                # Start processor if not running
                if (guild_id not in self.guild_processing or not self.guild_processing[guild_id] or
                        self.guild_processing_tasks.get(guild_id) is None or self.guild_processing_tasks[
                            guild_id].done()):
                    self.guild_processing_tasks[guild_id] = asyncio.create_task(self._process_guild_queue(guild_id))

            except asyncio.QueueFull:
                self.logger.warning(f"🚫 Queue full, dropping message: {text[:30]}...")
                await self._cleanup_order_queue(guild_id, sequence_id)

        except Exception as e:
            self.logger.error(f"❌ Message processing error: {e}")
            try:
                await self._cleanup_order_queue(message.guild.id, sequence_id)
            except Exception:
                pass

    async def _process_guild_queue(self, guild_id: int):
        """Guild queue processing with robust connection handling and failure recovery"""
        queue = self._get_guild_queue(guild_id)
        self.guild_processing[guild_id] = True
        self.logger.info(f"🎯 Starting queue processor for guild {guild_id}")

        consecutive_failures = 0
        max_consecutive_failures = 3

        while self.guild_processing.get(guild_id, False):
            try:
                try:
                    message, text, audio_data, sequence_id = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    if queue.empty():
                        self.logger.debug(f"🕒 Queue empty, stopping processor for guild {guild_id}")
                        break
                    continue

                # Check if user is still in voice channel
                if not message.author.voice or not message.author.voice.channel:
                    self.logger.debug(f"🚫 User left VC, skipping message")
                    await self._cleanup_order_queue(guild_id, sequence_id)
                    queue.task_done()
                    continue

                voice_channel = message.author.voice.channel

                # Get voice connection with robust error handling
                try:
                    self.logger.info(f"🔊 Attempting voice connection to {voice_channel.name}...")
                    vc = await self.voice_manager.get_voice_client(guild_id, voice_channel)
                except Exception as e:
                    self.logger.error(f"❌ Voice connection error: {e}")
                    await self._cleanup_order_queue(guild_id, sequence_id)
                    queue.task_done()
                    consecutive_failures += 1
                    continue

                if not vc:
                    self.logger.error(f"❌ Failed to get voice client for {voice_channel.name}")
                    await self._cleanup_order_queue(guild_id, sequence_id)
                    queue.task_done()
                    consecutive_failures += 1
                    continue

                # Wait for current playback to finish
                if vc.is_playing():
                    self.logger.debug("⏳ Waiting for current playback to finish...")
                    for _ in range(150):  # 15 second timeout
                        if not vc.is_playing():
                            break
                        await asyncio.sleep(0.1)
                    else:
                        self.logger.warning("⏰ Playback timeout, forcing stop")
                        vc.stop()

                # Play audio
                tmp_file = None
                try:
                    tmp_file = tempfile.mktemp(suffix=self.tts_processor.file_extension)
                    with open(tmp_file, 'wb') as f:
                        f.write(audio_data)

                    source = disnake.FFmpegPCMAudio(
                        tmp_file,
                        options="-f wav -ac 2 -ar 48000 -loglevel quiet"
                    )

                    def after_playing(error: Optional[Exception]) -> None:
                        # Clean up temp file
                        if tmp_file and os.path.exists(tmp_file):
                            try:
                                os.unlink(tmp_file)
                            except Exception:
                                pass
                        # Clean up queue
                        if self.bot.loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self._cleanup_order_queue(guild_id, sequence_id),
                                self.bot.loop
                            )

                    vc.play(source, after=after_playing)
                    self.logger.info(f"🔊 Playing TTS: {text[:30]}...")

                    # Reset failure counter on success
                    consecutive_failures = 0

                except Exception as e:
                    self.logger.error(f"❌ Playback error: {e}")
                    if tmp_file and os.path.exists(tmp_file):
                        try:
                            os.unlink(tmp_file)
                        except Exception:
                            pass
                    await self._cleanup_order_queue(guild_id, sequence_id)
                    consecutive_failures += 1

                queue.task_done()

            except asyncio.CancelledError:
                self.logger.info(f"🛑 Queue processor cancelled for guild {guild_id}")
                break
            except Exception as e:
                consecutive_failures += 1
                self.logger.error(f"❌ Queue processor error ({consecutive_failures}/{max_consecutive_failures}): {e}")

                if consecutive_failures >= max_consecutive_failures:
                    self.logger.error(f"🛑 Too many consecutive failures, stopping processor for guild {guild_id}")
                    break

                await asyncio.sleep(1.0 * consecutive_failures)  # Exponential backoff

        self.guild_processing[guild_id] = False
        self.logger.info(f"🛑 Queue processor stopped for guild {guild_id}")

    async def _periodic_connection_cleanup(self):
        """Periodically clean up stale connections"""
        while True:
            await asyncio.sleep(60)  # Every minute
            try:
                await self.voice_manager.cleanup_all_stale_connections()
            except Exception as e:
                self.logger.debug(f"Connection cleanup error: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        """Message handler"""
        if message.author.bot or not message.guild:
            return

        if message.content.startswith(('!', '/', '.')):
            return

        self.logger.info(f"📨 Received message from {message.author}: {message.content[:50]}...")

        # Process message
        asyncio.create_task(self._process_message(message))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState,
                                    after: disnake.VoiceState):
        """Enhanced voice state handler - AUTO DISCONNECT WHEN EMPTY"""
        if member.bot:
            return

        guild_id = member.guild.id

        # Handle bot being disconnected or moved
        if member.id == self.bot.user.id:
            if before.channel and not after.channel:
                # Bot was disconnected
                self.logger.info("🔌 Bot was disconnected from voice channel")
                await self.voice_manager.disconnect(guild_id)
            elif before.channel and after.channel and before.channel.id != after.channel.id:
                # Bot was moved to different channel
                self.logger.info(f"🔀 Bot was moved to {after.channel.name}")
                # Update our tracking
                self.voice_manager.voice_clients[guild_id] = member.guild.voice_client
            return

        # Enhanced auto-disconnect logic
        if before.channel and not after.channel:
            await asyncio.sleep(3)  # Longer delay for stability

            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client and guild.voice_client.channel:
                current_channel = guild.voice_client.channel
                human_users = [m for m in current_channel.members if not m.bot]

                if not human_users:
                    self.logger.info(f"🎯 Auto-disconnecting from empty channel: {current_channel.name}")
                    await self.voice_manager.disconnect(guild_id)
                else:
                    self.logger.debug(f"👥 Channel not empty: {len(human_users)} users remaining")

    async def cog_load(self):
        """Cog load - cleanup any stale connections"""
        await self.voice_manager.cleanup_all_stale_connections()
        self.connection_cleanup_task = asyncio.create_task(self._periodic_connection_cleanup())
        self.logger.info("VoiceProcessingCog background tasks started")

    async def cog_unload(self):
        """Cog unload"""
        self.guild_processing.clear()

        # Cancel tasks
        tasks_to_cancel = []
        if self.rate_cleanup_task:
            tasks_to_cancel.append(self.rate_cleanup_task)
        if self.health_monitor_task:
            tasks_to_cancel.append(self.health_monitor_task)
        if self.connection_cleanup_task:
            tasks_to_cancel.append(self.connection_cleanup_task)
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

        await self.tts_processor.close_http_session()
        await self.voice_manager.disconnect_all()
        self.logger.info("VoiceProcessingCog unloaded completely")


def setup(bot: commands.Bot):
    bot.add_cog(VoiceProcessingCog(bot))