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
MAX_RETRIES, RETRY_DELAY, MAX_QUEUE_SIZE = 3, 0.2, 100
PRIORITY_USERS, MAX_TEXT_LENGTH, CACHE_SIZE = [], 2000, 200
RATE_LIMIT_CLEANUP_INTERVAL, CONNECTION_TIMEOUT = 300, 15.0


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
    """Sanitize text for TTS including emoji conversion"""
    text = EMOJI_REGEX.sub(lambda m: f":{m.group(1)}:", text.strip())
    text = re.sub(r'\s+', ' ', text.replace('@', 'at ').replace('#', 'hash '))
    return text[:MAX_TEXT_LENGTH]


class VoiceClientManager:
    """Improved voice connection manager with health checks"""

    def __init__(self, bot: commands.Bot, logger):
        self.bot, self.logger = bot, logger
        self.voice_clients: Dict[int, disnake.VoiceClient] = {}
        self.connection_locks: Dict[int, asyncio.Lock] = {}
        self.last_activity: Dict[int, float] = {}  # Track last activity time

    def _get_connection_lock(self, guild_id: int) -> asyncio.Lock:
        """Get or create connection lock for guild"""
        return self.connection_locks.setdefault(guild_id, asyncio.Lock())

    async def get_voice_client(self, guild_id: int, channel: disnake.VoiceChannel) -> Optional[disnake.VoiceClient]:
        """Get or create voice client with health checks"""
        async with self._get_connection_lock(guild_id):
            guild = self.bot.get_guild(guild_id)
            if not guild:
                self.logger.error(f"Guild {guild_id} not found")
                return None

            existing_vc = guild.voice_client
            current_time = time.time()

            # Update activity timestamp
            self.last_activity[guild_id] = current_time

            # Check if existing connection is healthy
            if existing_vc and isinstance(existing_vc, disnake.VoiceClient):
                if not existing_vc.is_connected():
                    self.logger.warning(f"Existing voice client for guild {guild_id} is not connected, cleaning up")
                    try:
                        await existing_vc.disconnect(force=True)
                    except Exception as e:
                        self.logger.debug(f"Error cleaning up disconnected client: {e}")
                    existing_vc = None
                elif existing_vc.channel and existing_vc.channel.id == channel.id:
                    self.logger.info(f"✅ Using existing healthy connection to {channel.name}")
                    self.voice_clients[guild_id] = existing_vc
                    return existing_vc
                else:
                    # Try to move to correct channel
                    try:
                        await existing_vc.move_to(channel)
                        self.logger.info(f"🔀 Moved to {channel.name}")
                        self.voice_clients[guild_id] = existing_vc
                        return existing_vc
                    except (disnake.ClientException, asyncio.TimeoutError) as e:
                        self.logger.warning(f"Move failed: {e}, creating new connection")
                        try:
                            await existing_vc.disconnect(force=True)
                        except Exception:
                            pass
                        existing_vc = None

            # Clean up stale tracking
            if guild_id in self.voice_clients:
                stale_vc = self.voice_clients[guild_id]
                if stale_vc and (stale_vc != guild.voice_client or not stale_vc.is_connected()):
                    del self.voice_clients[guild_id]

            # Create new connection with retry logic
            for attempt in range(2):
                try:
                    self.logger.info(f"🔊 Connecting to {channel.name} (attempt {attempt + 1})...")
                    vc = await channel.connect(reconnect=True, timeout=30.0)
                    if isinstance(vc, disnake.VoiceClient):
                        # Wait for connection to stabilize
                        for _ in range(50):  # 5 second timeout
                            if vc.is_connected():
                                break
                            await asyncio.sleep(0.1)
                        else:
                            self.logger.warning("Voice connection timeout, retrying...")
                            await vc.disconnect(force=True)
                            continue

                        if vc.is_connected():
                            self.voice_clients[guild_id] = vc
                            self.last_activity[guild_id] = current_time
                            self.logger.info(f"✅ Connected to {channel.name}")
                            return vc
                        else:
                            await vc.disconnect(force=True)
                except (disnake.ClientException, asyncio.TimeoutError, Exception) as e:
                    self.logger.error(f"❌ Connection attempt {attempt + 1} failed: {e}")
                    if attempt == 0:
                        await asyncio.sleep(1)
            return None

    async def disconnect(self, guild_id: int):
        """Disconnect from voice channel"""
        async with self._get_connection_lock(guild_id):
            if guild_id in self.voice_clients:
                vc = self.voice_clients[guild_id]
                try:
                    if vc and isinstance(vc, disnake.VoiceClient):
                        await vc.disconnect(force=True)
                    del self.voice_clients[guild_id]
                    if guild_id in self.last_activity:
                        del self.last_activity[guild_id]
                    self.logger.info(f"🔇 Disconnected from guild {guild_id}")
                except Exception as e:
                    self.logger.error(f"Error disconnecting: {e}")
                    if guild_id in self.voice_clients:
                        del self.voice_clients[guild_id]

    async def disconnect_all(self):
        """Disconnect all voice clients"""
        for guild_id in list(self.voice_clients.keys()):
            await self.disconnect(guild_id)

    def is_connected(self, guild_id: int) -> bool:
        """Check if bot is connected to voice in guild"""
        if guild_id in self.voice_clients:
            vc = self.voice_clients[guild_id]
            return vc and isinstance(vc, disnake.VoiceClient) and vc.is_connected()
        return False

    async def health_check(self):
        """Periodic health check for voice connections"""
        current_time = time.time()
        disconnected_guilds = []

        for guild_id, vc in list(self.voice_clients.items()):
            if not vc or not vc.is_connected():
                disconnected_guilds.append(guild_id)
                continue

            # Check if connection has been inactive for too long (30 minutes)
            last_active = self.last_activity.get(guild_id, 0)
            if current_time - last_active > 1800:  # 30 minutes
                self.logger.info(f"Disconnecting inactive voice connection in guild {guild_id}")
                disconnected_guilds.append(guild_id)

        for guild_id in disconnected_guilds:
            await self.disconnect(guild_id)


def enhance_short_messages(text: str) -> str:
    """Add context for short messages"""
    words = text.strip().split()
    if len(words) <= 2:
        short_responses = {
            'yes': 'yes.', 'no': 'no.', 'ok': 'okay.', 'k': 'okay.',
            'lol': 'laughing out loud', 'lmao': 'laughing my ass off',
            'brb': 'be right back', 'omg': 'oh my god'
        }
        if len(words) == 1 and words[0].lower() in short_responses:
            return short_responses[words[0].lower()]
        return f"{text}." if len(words) == 2 else f"{words[0]}." if words else text
    return text


class LRUCache:
    """LRU Cache implementation with size limit"""

    def __init__(self, max_size: int):
        self.max_size, self.cache = max_size, OrderedDict()

    def get(self, key: str) -> Optional[bytes]:
        """Get value from cache, mark as recently used"""
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def set(self, key: str, value: bytes):
        """Set value in cache, evict oldest if needed"""
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
    """TTS processor with session management"""

    def __init__(self, config, logger):
        self.logger = logger
        self.tts_url, self.tts_token = config.tts.api_url, config.tts.bearer_token
        self.tts_model, self.default_voice = config.tts.engine, config.tts.default_voice
        self.available_voices = config.tts.voices.get("available_voices", [])
        self.user_voice_map = config.tts.voices.get("user_voice_mappings", {})
        self.retry_limit = config.tts.retry_limit

        # Use WAV format for better quality and Discord compatibility
        self.preferred_format, self.file_extension = "wav", ".wav"
        self.audio_cache, self.cache_lock = LRUCache(CACHE_SIZE), asyncio.Lock()
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.session_creation_lock = asyncio.Lock()

        # Validate configuration
        if not self.tts_url or not self.tts_token:
            raise ValueError("TTS API URL and bearer token must be configured")

    async def get_http_session(self) -> aiohttp.ClientSession:
        """Get HTTP session with proper cleanup"""
        async with self.session_creation_lock:
            if self.http_session is None or self.http_session.closed:
                # Close old session if it exists
                if self.http_session and not self.http_session.closed:
                    await self.http_session.close()

                # Create new session with reasonable timeouts
                timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_connect=10, sock_read=30)
                connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=30)
                self.http_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
                self.logger.info("Created new HTTP session for TTS")
            return self.http_session

    async def close_http_session(self):
        """Close HTTP session"""
        async with self.session_creation_lock:
            if self.http_session and not self.http_session.closed:
                await self.http_session.close()
                self.http_session = None
                self.logger.info("Closed TTS HTTP session")

    async def generate_audio(self, text: str, user_id: int) -> Optional[bytes]:
        """Generate TTS audio with caching and session recovery"""
        enhanced_text = self._optimize_text_for_tts(text)
        voice_id = self.user_voice_map.get(str(user_id)) or self.default_voice or (
            random.choice(self.available_voices) if self.available_voices else None)

        if not voice_id:
            self.logger.error(f"No voice ID available for user {user_id}")
            return None

        # Cache check
        text_hash = hashlib.md5(f"{voice_id}:{enhanced_text}".encode()).hexdigest()
        cache_key = f"{voice_id}:{text_hash}"

        async with self.cache_lock:
            if cached_audio := self.audio_cache.get(cache_key):
                self.logger.debug(f"⚡ Cache hit for: {enhanced_text[:30]}...")
                return cached_audio

        # Generate new audio with session recovery
        for attempt in range(self.retry_limit + 1):
            try:
                audio_data = await self._call_tts_api(enhanced_text, voice_id)
                if audio_data:
                    async with self.cache_lock:
                        self.audio_cache.set(cache_key, audio_data)
                    return audio_data
            except aiohttp.ClientError as e:
                self.logger.warning(f"TTS client error (attempt {attempt + 1}): {e}")
                # Close session on client errors to force fresh connection
                await self.close_http_session()
                if attempt < self.retry_limit:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))

        self.logger.error("💀 All TTS attempts failed")
        return None

    async def _call_tts_api(self, text: str, voice_id: str) -> Optional[bytes]:
        """Call TTS API with WAV format"""
        headers = {"Authorization": f"Bearer {self.tts_token}", "Content-Type": "application/json"}
        payload = {"model": self.tts_model, "input": text, "voice": voice_id, "response_format": self.preferred_format}

        http = await self.get_http_session()
        async with http.post(self.tts_url, json=payload, headers=headers) as response:
            if response.status == 200:
                audio_data = await response.read()
                self.logger.info(f"✅ TTS generated ({len(audio_data)} bytes): {text[:50]}...")
                return audio_data
            else:
                error_text = await response.text()
                self.logger.error(f"❌ TTS API error {response.status}: {error_text}")
                if response.status == 401:
                    raise aiohttp.ClientError("Authentication failed - check TTS token")
                return None

    @staticmethod
    def _optimize_text_for_tts(text: str) -> str:
        """Optimize text for TTS"""
        text = re.sub(r'[.!?]+$', '', text.strip())
        if not any(text.endswith(p) for p in ['.', '!', '?']):
            text += '.'
        return enhance_short_messages(text)


class VoiceProcessingCog(commands.Cog):
    """Robust TTS Processing System with Health Monitoring"""

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

        # Message ordering and rate limiting
        self.guild_message_order: Dict[int, List[Tuple[int, asyncio.Future]]] = {}
        self.guild_order_locks: Dict[int, asyncio.Lock] = {}
        self.next_sequence_id: Dict[int, int] = {}
        self.user_requests: Dict[int, List[float]] = {}
        self.rate_limit, self.rate_window = 10, 60

        # Background tasks
        self.rate_cleanup_task: Optional[asyncio.Task] = None
        self.health_monitor_task: Optional[asyncio.Task] = None
        self.voice_health_task: Optional[asyncio.Task] = None

        self.logger.info("VoiceProcessingCog initialized with improved reliability")

    def _check_rate_limit(self, user_id: int) -> bool:
        """Check rate limit"""
        now = time.time()
        if user_id not in self.user_requests:
            self.user_requests[user_id] = []
        self.user_requests[user_id] = [t for t in self.user_requests[user_id] if now - t < self.rate_window]

        if len(self.user_requests[user_id]) < self.rate_limit:
            self.user_requests[user_id].append(now)
            return True
        return False

    async def _cleanup_rate_limits(self):
        """Periodically clean up old rate limit entries"""
        while True:
            try:
                await asyncio.sleep(RATE_LIMIT_CLEANUP_INTERVAL)
                now = time.time()
                expired_users = []
                for user_id, timestamps in self.user_requests.items():
                    recent_timestamps = [t for t in timestamps if now - t < self.rate_window * 2]
                    if recent_timestamps:
                        self.user_requests[user_id] = recent_timestamps
                    else:
                        expired_users.append(user_id)
                for user_id in expired_users:
                    del self.user_requests[user_id]
                self.logger.debug(f"Rate limit cleanup: {len(expired_users)} users removed")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Rate limit cleanup error: {e}")

    async def _monitor_system_health(self):
        """Comprehensive system health monitoring"""
        while True:
            await asyncio.sleep(60)  # Check every minute
            try:
                # Check voice connections
                await self.voice_manager.health_check()

                # Check queue processors
                for guild_id, task in list(self.guild_processing_tasks.items()):
                    if task and task.done():
                        queue = self.guild_queues.get(guild_id)
                        if queue and not queue.empty():
                            self.logger.warning(f"🔄 Restarting finished queue processor for guild {guild_id}")
                            self.guild_processing_tasks[guild_id] = asyncio.create_task(
                                self._process_guild_queue(guild_id))

                # Log system status periodically
                self.logger.debug(
                    f"System status - Active queues: {len([q for q in self.guild_queues.values() if not q.empty()])}, "
                    f"Voice connections: {len(self.voice_manager.voice_clients)}")

            except Exception as e:
                self.logger.error(f"Health monitor error: {e}")

    def _get_guild_queue(self, guild_id: int) -> asyncio.Queue:
        """Get or create queue for guild"""
        return self.guild_queues.setdefault(guild_id, asyncio.Queue(maxsize=MAX_QUEUE_SIZE))

    def _get_guild_order_lock(self, guild_id: int) -> asyncio.Lock:
        """Get or create order lock for guild"""
        return self.guild_order_locks.setdefault(guild_id, asyncio.Lock())

    def _get_next_sequence_id(self, guild_id: int) -> int:
        """Get next sequence ID for guild"""
        self.next_sequence_id[guild_id] = self.next_sequence_id.get(guild_id, 0) + 1
        return self.next_sequence_id[guild_id]

    async def _process_message(self, message: disnake.Message):
        """Process a single message with proper ordering and error recovery"""
        sequence_id = 0
        try:
            text = sanitize_text(message.content)
            if not text or len(text.strip()) < 1:
                return

            if not self._check_rate_limit(message.author.id):
                self.logger.warning(f"Rate limit exceeded for user {message.author.id}")
                return

            if not message.author.voice or not message.author.voice.channel:
                self.logger.error(f"User {message.author} not in voice channel")
                return

            guild_id = message.guild.id
            sequence_id = self._get_next_sequence_id(guild_id)
            future = asyncio.Future()

            async with self._get_guild_order_lock(guild_id):
                if guild_id not in self.guild_message_order:
                    self.guild_message_order[guild_id] = []
                self.guild_message_order[guild_id].append((sequence_id, future))
                self.logger.info(f"📝 Message {sequence_id} added to order queue for guild {guild_id}")

                if len(self.guild_message_order[guild_id]) == 1:
                    future.set_result(True)
                    self.logger.info(f"🚀 Starting processing for first message {sequence_id}")

            await future

            self.logger.info(f"🔄 Generating TTS for message {sequence_id}: {text[:50]}...")
            audio_data = await self.tts_processor.generate_audio(text, message.author.id)
            if not audio_data:
                self.logger.error(f"TTS generation failed for message {sequence_id}")
                await self._cleanup_order_queue(guild_id, sequence_id)
                return

            queue = self._get_guild_queue(guild_id)
            try:
                queue.put_nowait((message, text, audio_data, sequence_id))
                self.logger.info(f"📥 Added message {sequence_id} to guild {guild_id} queue: {text[:50]}...")

                if (guild_id not in self.guild_processing or not self.guild_processing[guild_id] or
                        self.guild_processing_tasks.get(guild_id) is None or self.guild_processing_tasks[
                            guild_id].done()):
                    self.guild_processing_tasks[guild_id] = asyncio.create_task(self._process_guild_queue(guild_id))
                    self.logger.info(f"🎯 Started queue processor for guild {guild_id}")

            except asyncio.QueueFull:
                self.logger.warning(f"Queue is full for guild {guild_id}, message {sequence_id} dropped")
                await self._cleanup_order_queue(guild_id, sequence_id)

        except Exception as e:
            self.logger.error(f"Error processing message: {e}")
            try:
                await self._cleanup_order_queue(message.guild.id, sequence_id)
            except Exception:
                pass

    async def _cleanup_order_queue(self, guild_id: int, current_sequence_id: int):
        """Clean up order queue and trigger next message"""
        async with self._get_guild_order_lock(guild_id):
            if guild_id in self.guild_message_order:
                remaining_messages = [(seq, fut) for seq, fut in self.guild_message_order[guild_id] if
                                      seq != current_sequence_id]
                self.guild_message_order[guild_id] = remaining_messages
                if remaining_messages and not remaining_messages[0][1].done():
                    next_seq, next_fut = remaining_messages[0]
                    next_fut.set_result(True)
                    self.logger.info(f"🔓 Triggered next message {next_seq} after {current_sequence_id}")
                self.logger.info(
                    f"📋 Order queue updated for guild {guild_id}, {len(remaining_messages)} messages remaining")

    async def _cleanup_playback(self, guild_id: int, sequence_id: int, error: Optional[Exception]):
        """Handle playback completion cleanup"""
        if error:
            self.logger.error(f"Playback error for message {sequence_id}: {error}")
        else:
            self.logger.info(f"✅ Finished playing message {sequence_id}")
        await self._cleanup_order_queue(guild_id, sequence_id)

    async def _process_guild_queue(self, guild_id: int):
        """Process messages for a specific guild with comprehensive error handling"""
        queue = self._get_guild_queue(guild_id)
        self.guild_processing[guild_id] = True
        self.logger.info(f"🚀 Starting queue processor for guild {guild_id}")

        while True:
            try:
                if not self.guild_processing.get(guild_id, False):
                    break

                try:
                    message, text, audio_data, sequence_id = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    if queue.empty():
                        self.logger.info(f"🛑 Stopping idle queue processor for guild {guild_id}")
                        break
                    continue

                self.logger.info(f"🔊 Processing message {sequence_id} from queue: {text[:50]}...")
                voice_channel = message.author.voice.channel

                # Check if this is an initial connection
                is_initial_connection = not self.voice_manager.is_connected(guild_id)

                try:
                    vc = await asyncio.wait_for(self.voice_manager.get_voice_client(guild_id, voice_channel),
                                                timeout=CONNECTION_TIMEOUT)
                except asyncio.TimeoutError:
                    self.logger.error(f"Connection timeout for guild {guild_id}")
                    await self._cleanup_order_queue(guild_id, sequence_id)
                    queue.task_done()
                    continue

                if not vc:
                    self.logger.error("Failed to connect to voice channel")
                    await self._cleanup_order_queue(guild_id, sequence_id)
                    queue.task_done()
                    continue

                # Add delay only for initial connection
                if is_initial_connection:
                    self.logger.info(f"🕒 Initial connection detected, adding 3-second delay for guild {guild_id}")
                    await asyncio.sleep(3.0)
                    self.logger.info("✅ Initial connection delay completed")

                # Wait for current playback to finish with timeout
                if vc.is_playing():
                    self.logger.debug("Waiting for current playback to finish...")
                    for _ in range(300):  # 30 second timeout
                        if not vc.is_playing():
                            break
                        await asyncio.sleep(0.1)
                    else:
                        self.logger.warning("Playback timeout, forcing stop")
                        vc.stop()
                        await asyncio.sleep(0.5)

                tmp_file = None
                try:
                    # Create temporary file
                    tmp_file = tempfile.mktemp(suffix=self.tts_processor.file_extension)
                    with open(tmp_file, 'wb') as f:
                        f.write(audio_data)

                    # Use FFmpeg with better options for problematic WAV files
                    source = disnake.FFmpegPCMAudio(
                        tmp_file,
                        before_options="-fflags +genpts -err_detect ignore_err",
                        options="-f wav -ac 2 -ar 48000 -loglevel warning"
                    )

                    bot_loop = self.bot.loop

                    def after_playing(error: Optional[Exception]) -> None:
                        # Clean up temp file
                        try:
                            if tmp_file and os.path.exists(tmp_file):
                                os.unlink(tmp_file)
                        except Exception as e:
                            self.logger.debug(f"Error cleaning temp file: {e}")

                        # Schedule cleanup
                        if bot_loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self._cleanup_playback(guild_id, sequence_id, error),
                                bot_loop
                            )

                    vc.play(source, after=after_playing)
                    self.logger.info(f"🎵 Playing message {sequence_id} (WAV format): {text[:50]}...")

                    # Wait for playback to start
                    await asyncio.sleep(1)

                except Exception as e:
                    self.logger.error(f"Playback error for message {sequence_id}: {e}")
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
                self.logger.error(f"Guild queue processor error: {e}")
                await asyncio.sleep(1)

        self.guild_processing[guild_id] = False
        self.logger.info(f"🛑 Queue processor stopped for guild {guild_id}")

    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        """Handle new messages"""
        if (message.author.bot or not message.guild or
                (hasattr(self.cfg.discord, 'channel_id') and self.cfg.discord.channel_id and
                 message.channel.id != self.cfg.discord.channel_id) or
                message.content.startswith(('!', '/', '.'))):
            return

        self.logger.info(f"📨 Received message from {message.author}: {message.content[:50]}...")
        await self._process_message(message)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState,
                                    after: disnake.VoiceState):
        """Handle voice state changes"""
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
                    human_members = [m for m in before.channel.members if not m.bot]
                    if not human_members:
                        self.logger.info("Channel empty, disconnecting")
                        await asyncio.sleep(2)
                        if (self.voice_manager.is_connected(guild_id) and vc.channel and
                                not [m for m in vc.channel.members if not m.bot]):
                            await self.voice_manager.disconnect(guild_id)

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics"""
        return {
            "active_guild_queues": len([q for q in self.guild_queues.values() if not q.empty()]),
            "active_voice_connections": len(self.voice_manager.voice_clients),
            "cache_hit_rate": f"{len(self.tts_processor.audio_cache)}/{CACHE_SIZE}",
            "total_ordered_messages": sum(len(q) for q in self.guild_message_order.values()),
            "rate_limited_users": len(self.user_requests),
            "audio_format": self.tts_processor.preferred_format.upper(),
        }

    @commands.slash_command(name="tts_stats", description="Check TTS system statistics")
    async def tts_stats(self, inter: disnake.ApplicationCommandInteraction):
        """Display TTS system statistics"""
        stats = self.get_performance_stats()
        embed = disnake.Embed(title="TTS System Statistics", color=disnake.Color.blue())
        for key, value in stats.items():
            embed.add_field(name=key.replace('_', ' ').title(), value=str(value), inline=True)
        await inter.response.send_message(embed=embed)

    @commands.slash_command(name="tts_reset", description="Reset TTS system for this guild")
    async def tts_reset(self, inter: disnake.ApplicationCommandInteraction):
        """Reset TTS system to recover from stuck state"""
        guild_id = inter.guild_id

        # Disconnect voice
        await self.voice_manager.disconnect(guild_id)

        # Clear queues
        if guild_id in self.guild_queues:
            queue = self.guild_queues[guild_id]
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except Exception:
                    pass

        # Clear order queue
        async with self._get_guild_order_lock(guild_id):
            if guild_id in self.guild_message_order:
                for seq, future in self.guild_message_order[guild_id]:
                    if not future.done():
                        future.cancel()
                self.guild_message_order[guild_id].clear()

        # Reset processing state
        self.guild_processing[guild_id] = False
        if guild_id in self.guild_processing_tasks:
            task = self.guild_processing_tasks[guild_id]
            if not task.done():
                task.cancel()
            del self.guild_processing_tasks[guild_id]

        await inter.response.send_message("✅ TTS system has been reset for this guild.", ephemeral=True)

    async def cog_load(self):
        """Start background tasks when cog loads"""
        self.rate_cleanup_task = asyncio.create_task(self._cleanup_rate_limits())
        self.health_monitor_task = asyncio.create_task(self._monitor_system_health())
        self.logger.info("VoiceProcessingCog background tasks started")

    async def cog_unload(self):
        """Clean shutdown with comprehensive cleanup"""
        self.logger.info("Shutting down VoiceProcessingCog...")

        # Stop all processing
        self.guild_processing.clear()

        # Cancel order futures
        for guild_id, message_list in self.guild_message_order.items():
            for sequence_id, future in message_list:
                if not future.done():
                    future.cancel()

        # Cancel all background tasks
        tasks_to_cancel = []
        if self.rate_cleanup_task:
            tasks_to_cancel.append(self.rate_cleanup_task)
        if self.health_monitor_task:
            tasks_to_cancel.append(self.health_monitor_task)
        for task in self.guild_processing_tasks.values():
            if task and not task.done():
                tasks_to_cancel.append(task)

        # Cancel tasks
        for task in tasks_to_cancel:
            task.cancel()

        # Wait for cancellation with timeout
        if tasks_to_cancel:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks_to_cancel, return_exceptions=True), timeout=5.0)
            except (asyncio.TimeoutError, Exception) as e:
                self.logger.debug(f"Task cancellation completed with: {e}")

        # Clear queues
        for queue in self.guild_queues.values():
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except Exception:
                    pass

        # Close TTS HTTP session
        await self.tts_processor.close_http_session()

        # Disconnect all voice clients
        await self.voice_manager.disconnect_all()

        self.logger.info("VoiceProcessingCog shutdown complete")


def setup(bot: commands.Bot):
    bot.add_cog(VoiceProcessingCog(bot))