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
MAX_RETRIES = 3
RETRY_DELAY = 0.2
MAX_QUEUE_SIZE = 100
PRIORITY_USERS = []
MAX_TEXT_LENGTH = 2000
CACHE_SIZE = 200
RATE_LIMIT_CLEANUP_INTERVAL = 300  # 5 minutes
CONNECTION_TIMEOUT = 15.0  # Increased to 15 seconds


class QueuedMessage:
    """Represents a message with its processing state and order"""

    def __init__(self, message: disnake.Message, text: str):
        self.message = message
        self.text = text
        self.audio_data = None
        self.timestamp = time.time()
        self.message_id = message.id
        self.created_at = message.created_at.timestamp()
        self.priority = message.author.id in PRIORITY_USERS
        self.queue_position = 0
        self.sequence_id = uuid.uuid4().int


def sanitize_emojis(text: str) -> str:
    """Convert custom emojis to text"""
    return EMOJI_REGEX.sub(lambda m: f":{m.group(1)}:", text)


def sanitize_text(text: str) -> str:
    """Sanitize text for TTS"""
    text = re.sub(r'\s+', ' ', text.strip())
    text = text.replace('@', 'at ').replace('#', 'hash ')
    return text[:MAX_TEXT_LENGTH]


class VoiceClientManager:
    """Simplified voice connection manager"""

    def __init__(self, bot: commands.Bot, logger):
        self.bot = bot
        self.logger = logger
        self.voice_clients: Dict[int, disnake.VoiceClient] = {}
        self.connection_locks: Dict[int, asyncio.Lock] = {}

    def _get_connection_lock(self, guild_id: int) -> asyncio.Lock:
        """Get or create connection lock for guild"""
        if guild_id not in self.connection_locks:
            self.connection_locks[guild_id] = asyncio.Lock()
        return self.connection_locks[guild_id]

    async def get_voice_client(self, guild_id: int, channel: disnake.VoiceChannel) -> Optional[disnake.VoiceClient]:
        """Get or create voice client with better error recovery"""
        lock = self._get_connection_lock(guild_id)

        async with lock:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                self.logger.error(f"Guild {guild_id} not found")
                return None

            # Check existing connection with health verification
            if guild.voice_client and guild.voice_client.is_connected():
                vc = guild.voice_client
                try:
                    # Test if connection is actually alive
                    if vc.channel and vc.channel.id == channel.id:  # Fixed: Added None check
                        self.logger.info(f"✅ Using existing guild connection to {channel.name}")
                        self.voice_clients[guild_id] = vc
                        return vc
                    else:
                        # Fixed: Added type assertion for move_to
                        await vc.move_to(channel)  # type: ignore
                        self.logger.info(f"🔀 Moved to {channel.name}")
                        self.voice_clients[guild_id] = vc
                        return vc
                except Exception as e:
                    self.logger.warning(f"Existing connection issue: {e}, creating new connection")
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass

            # Clean up stale tracking
            if guild_id in self.voice_clients:
                stale_vc = self.voice_clients[guild_id]
                if stale_vc and (stale_vc != guild.voice_client or not stale_vc.is_connected()):
                    del self.voice_clients[guild_id]

            # Create new connection with retry logic
            for attempt in range(2):
                try:
                    self.logger.info(f"🔊 Connecting to {channel.name} (attempt {attempt + 1})...")
                    vc = await channel.connect()

                    # Wait for connection
                    for _ in range(30):  # 3 second timeout
                        if vc.is_connected():
                            break
                        await asyncio.sleep(0.1)

                    if vc.is_connected():
                        self.voice_clients[guild_id] = vc
                        self.logger.info(f"✅ Connected to {channel.name}")
                        return vc
                    else:
                        await vc.disconnect(force=True)

                except Exception as e:
                    self.logger.error(f"❌ Connection attempt {attempt + 1} failed: {e}")
                    if attempt == 0:
                        await asyncio.sleep(1)  # Brief delay before retry

            return None

    async def disconnect(self, guild_id: int):
        """Disconnect from voice channel"""
        lock = self._get_connection_lock(guild_id)

        async with lock:
            if guild_id in self.voice_clients:
                vc = self.voice_clients[guild_id]
                try:
                    if vc and vc.is_connected():
                        await vc.disconnect(force=True)
                    del self.voice_clients[guild_id]
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
            return vc and vc.is_connected()
        return False


def enhance_short_messages(text: str) -> str:
    """Add context for short messages"""
    words = text.strip().split()

    if len(words) <= 2:
        if len(words) == 1:
            short_responses = {
                'yes': 'yes.', 'no': 'no.', 'ok': 'okay.', 'k': 'okay.',
                'lol': 'laughing out loud', 'lmao': 'laughing my ass off',
                'brb': 'be right back', 'omg': 'oh my god'
            }
            if words[0].lower() in short_responses:
                return short_responses[words[0].lower()]
            return f"{words[0]}."
        elif len(words) == 2:
            return f"{text}."
    return text


class LRUCache:
    """LRU Cache implementation with size limit"""

    def __init__(self, max_size: int):
        self.max_size = max_size
        self.cache = OrderedDict()

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
    """TTS processor with optimized audio format"""

    def __init__(self, config, logger):
        self.tts_url = config.tts.api_url
        self.tts_token = config.tts.bearer_token
        self.tts_model = config.tts.engine
        self.default_voice = config.tts.default_voice
        self.available_voices = config.tts.voices.get("available_voices", [])
        self.user_voice_map = config.tts.voices.get("user_voice_mappings", {})
        self.retry_limit = config.tts.retry_limit
        self.logger = logger
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.audio_cache = LRUCache(CACHE_SIZE)
        self.cache_lock = asyncio.Lock()

        # Use WAV format for better quality and Discord compatibility
        self.preferred_format = "wav"
        self.file_extension = ".wav"

        # Validate configuration
        self._validate_config()

    def _validate_config(self):
        """Validate TTS configuration"""
        if not self.tts_url:
            raise ValueError("TTS API URL is not configured")
        if not self.tts_token:
            raise ValueError("TTS bearer token is not configured")
        if not self.default_voice and not self.available_voices:
            self.logger.warning("No default voice or available voices configured")

    async def get_http_session(self) -> aiohttp.ClientSession:
        """Get HTTP session"""
        if self.http_session is None or self.http_session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.http_session = aiohttp.ClientSession(timeout=timeout)
        return self.http_session

    async def generate_audio(self, text: str, user_id: int) -> Optional[bytes]:
        """Generate TTS audio"""
        enhanced_text = self._optimize_text_for_tts(text)

        # Fixed voice selection logic
        voice_id = self.user_voice_map.get(str(user_id)) or self.default_voice
        if not voice_id and self.available_voices:
            voice_id = random.choice(self.available_voices)

        if not voice_id:
            self.logger.error(f"No voice ID available for user {user_id}")
            return None

        # Validate voice ID
        if self.available_voices and voice_id not in self.available_voices:
            self.logger.warning(f"Voice ID {voice_id} not in available voices, using default")
            voice_id = self.default_voice or (self.available_voices[0] if self.available_voices else None)

        # Cache check
        text_hash = hashlib.md5(f"{voice_id}:{enhanced_text}".encode()).hexdigest()
        cache_key = f"{voice_id}:{text_hash}"

        # Thread-safe cache access
        async with self.cache_lock:
            cached_audio = self.audio_cache.get(cache_key)
            if cached_audio:
                self.logger.debug(f"⚡ Cache hit for: {enhanced_text[:30]}...")
                return cached_audio

        # Generate new audio - ACTUALLY CALL THE API FUNCTION
        audio_data = await self._call_tts_api(enhanced_text, voice_id)

        # Cache the result if successful
        if audio_data:
            async with self.cache_lock:
                self.audio_cache.set(cache_key, audio_data)

        return audio_data

    async def _call_tts_api(self, text: str, voice_id: str) -> Optional[bytes]:
        """Call TTS API - OPTIMIZED WITH WAV FORMAT"""

        self.logger.info(f"🔧 TTS DEBUG: URL={self.tts_url}, Voice={voice_id}, Text='{text[:50]}...'")

        headers = {
            "Authorization": f"Bearer {self.tts_token}",
            "Content-Type": "application/json",
        }

        # Use WAV format for better quality and Discord compatibility
        payload = {
            "model": self.tts_model,  # "tts-1" or "tts-1-hd"
            "input": text,  # The text to convert
            "voice": voice_id,  # "alloy", "shimmer", "echo", etc.
            "response_format": self.preferred_format,  # WAV format for better quality
        }

        self.logger.info(f"🔧 TTS DEBUG: Payload={payload}")

        for attempt in range(self.retry_limit + 1):
            try:
                http = await self.get_http_session()
                async with http.post(self.tts_url, json=payload, headers=headers) as response:
                    self.logger.info(f"🔧 TTS DEBUG: Response Status={response.status}")

                    if response.status == 200:
                        audio_data = await response.read()
                        self.logger.info(f"✅ TTS generated ({len(audio_data)} bytes): {text[:50]}...")
                        return audio_data
                    else:
                        error_text = await response.text()
                        self.logger.error(f"❌ TTS API error {response.status}: {error_text}")
                        if 400 <= response.status < 500:
                            break

            except aiohttp.ClientError as e:
                self.logger.error(f"🌐 TTS network error (attempt {attempt + 1}): {e}")
            except Exception as e:
                self.logger.error(f"💥 TTS unexpected error (attempt {attempt + 1}): {e}")

            if attempt < self.retry_limit:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))

        self.logger.error("💀 All TTS attempts failed")
        return None

    def _optimize_text_for_tts(self, text: str) -> str:
        """Optimize text for TTS - FIXED VERSION"""
        text = text.strip()

        # Remove excessive punctuation first
        text = re.sub(r'[.!?]+$', '', text)

        # Add single appropriate punctuation if missing
        if not any(text.endswith(p) for p in ['.', '!', '?']):
            text += '.'

        text = enhance_short_messages(text)
        return text

    async def close(self):
        """Clean up"""
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()


class VoiceProcessingCog(commands.Cog):
    """Simplified TTS Processing System with Optimized Audio"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Fixed: Add type hints for custom attributes
        self.cfg = getattr(bot, 'config', None)
        self.logger = getattr(bot, 'logger', None)

        if not self.cfg or not self.logger:
            raise ValueError("Bot is missing required 'config' or 'logger' attributes")

        # Validate configuration
        self._validate_config()

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

        # Rate limiting
        self.user_requests: Dict[int, List[float]] = {}
        self.rate_limit = 10
        self.rate_window = 60
        self.rate_cleanup_task: Optional[asyncio.Task] = None

        # Performance monitoring
        self.queue_stats: Dict[int, Dict[str, Any]] = {}
        self.health_monitor_task: Optional[asyncio.Task] = None

        self.logger.info("VoiceProcessingCog initialized with optimized WAV audio format")

    def _validate_config(self):
        """Validate bot configuration"""
        if not hasattr(self.cfg, 'tts'):
            raise ValueError("TTS configuration is missing")
        if not hasattr(self.cfg.tts, 'api_url') or not self.cfg.tts.api_url:
            raise ValueError("TTS API URL is not configured")
        if not hasattr(self.cfg.tts, 'bearer_token') or not self.cfg.tts.bearer_token:
            raise ValueError("TTS bearer token is not configured")

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

    async def _monitor_queue_health(self):
        """Monitor queue health and auto-cleanup stuck queues"""
        while True:
            await asyncio.sleep(60)  # Check every minute
            try:
                current_time = time.time()
                for guild_id in list(self.guild_processing_tasks.keys()):
                    task = self.guild_processing_tasks.get(guild_id)
                    if task and task.done():
                        # Task finished unexpectedly, check if we should restart
                        queue = self.guild_queues.get(guild_id)
                        if queue and not queue.empty():
                            self.logger.warning(f"🔄 Restarting finished queue processor for guild {guild_id}")
                            self.guild_processing_tasks[guild_id] = asyncio.create_task(
                                self._process_guild_queue(guild_id)
                            )
            except Exception as e:
                self.logger.error(f"Queue health monitor error: {e}")

    def _get_guild_queue(self, guild_id: int) -> asyncio.Queue:
        """Get or create queue for guild"""
        if guild_id not in self.guild_queues:
            self.guild_queues[guild_id] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        return self.guild_queues[guild_id]

    def _get_guild_order_lock(self, guild_id: int) -> asyncio.Lock:
        """Get or create order lock for guild"""
        if guild_id not in self.guild_order_locks:
            self.guild_order_locks[guild_id] = asyncio.Lock()
        return self.guild_order_locks[guild_id]

    def _get_next_sequence_id(self, guild_id: int) -> int:
        """Get next sequence ID for guild"""
        if guild_id not in self.next_sequence_id:
            self.next_sequence_id[guild_id] = 0
        self.next_sequence_id[guild_id] += 1
        return self.next_sequence_id[guild_id]

    async def _process_message(self, message: disnake.Message):
        """Process a single message with proper ordering"""
        # Fixed: Initialize sequence_id at the start
        sequence_id = 0
        try:
            # Sanitize text
            text = sanitize_text(sanitize_emojis(message.content))
            if not text or len(text.strip()) < 1:
                return

            # Rate limit check
            if not self._check_rate_limit(message.author.id):
                self.logger.warning(f"Rate limit exceeded for user {message.author.id}")
                return

            # Check if user is in voice channel
            if not message.author.voice or not message.author.voice.channel:
                self.logger.error(f"User {message.author} not in voice channel")
                return

            guild_id = message.guild.id

            # Create ordered future for this message
            sequence_id = self._get_next_sequence_id(guild_id)
            future = asyncio.Future()

            # Add to guild's ordered processing queue
            async with self._get_guild_order_lock(guild_id):
                if guild_id not in self.guild_message_order:
                    self.guild_message_order[guild_id] = []
                self.guild_message_order[guild_id].append((sequence_id, future))
                self.logger.info(f"📝 Message {sequence_id} added to order queue for guild {guild_id}")

                # If this is the first message in queue, start processing immediately
                if len(self.guild_message_order[guild_id]) == 1:
                    future.set_result(True)
                    self.logger.info(f"🚀 Starting processing for first message {sequence_id}")

            # Wait for our turn in the sequence
            await future

            # Generate TTS audio
            self.logger.info(f"🔄 Generating TTS for message {sequence_id}: {text[:50]}...")
            audio_data = await self.tts_processor.generate_audio(text, message.author.id)
            if not audio_data:
                self.logger.error(f"TTS generation failed for message {sequence_id}")
                await self._cleanup_order_queue(guild_id, sequence_id)
                return

            # Add to guild-specific processing queue
            queue = self._get_guild_queue(guild_id)
            try:
                queue.put_nowait((message, text, audio_data, sequence_id))
                self.logger.info(f"📥 Added message {sequence_id} to guild {guild_id} queue: {text[:50]}...")

                # Start processor for this guild if not running
                if (guild_id not in self.guild_processing or
                        not self.guild_processing[guild_id] or
                        self.guild_processing_tasks.get(guild_id) is None or
                        self.guild_processing_tasks[guild_id].done()):
                    self.guild_processing_tasks[guild_id] = asyncio.create_task(
                        self._process_guild_queue(guild_id)
                    )
                    self.logger.info(f"🎯 Started queue processor for guild {guild_id}")

            except asyncio.QueueFull:
                self.logger.warning(f"Queue is full for guild {guild_id}, message {sequence_id} dropped")
                await self._cleanup_order_queue(guild_id, sequence_id)

        except Exception as e:
            self.logger.error(f"Error processing message: {e}")
            try:
                guild_id = message.guild.id
                await self._cleanup_order_queue(guild_id, sequence_id)
            except Exception:
                pass

    async def _cleanup_order_queue(self, guild_id: int, current_sequence_id: int):
        """Clean up order queue and trigger next message"""
        async with self._get_guild_order_lock(guild_id):
            if guild_id in self.guild_message_order:
                # Find and remove the current message
                remaining_messages = []
                found_current = False

                for seq, fut in self.guild_message_order[guild_id]:
                    if seq == current_sequence_id:
                        found_current = True
                        continue  # Skip the current message
                    else:
                        remaining_messages.append((seq, fut))

                # Update the queue
                self.guild_message_order[guild_id] = remaining_messages

                # Trigger the next message if there is one
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
        """Process messages for a specific guild with health checks"""
        queue = self._get_guild_queue(guild_id)
        self.guild_processing[guild_id] = True

        self.logger.info(f"🚀 Starting queue processor for guild {guild_id}")

        while True:
            try:
                # Health check - ensure we're still supposed to be processing
                if not self.guild_processing.get(guild_id, False):
                    break

                # Get the next message from the queue with timeout
                try:
                    message, text, audio_data, sequence_id = await asyncio.wait_for(
                        queue.get(),
                        timeout=30.0  # Prevent hanging if queue is empty
                    )
                except asyncio.TimeoutError:
                    # No messages for 30 seconds, check if we should stop
                    if queue.empty():
                        self.logger.info(f"🛑 Stopping idle queue processor for guild {guild_id}")
                        break
                    continue

                self.logger.info(f"🔊 Processing message {sequence_id} from queue: {text[:50]}...")

                # Get voice client
                voice_channel = message.author.voice.channel
                try:
                    vc = await asyncio.wait_for(
                        self.voice_manager.get_voice_client(guild_id, voice_channel),
                        timeout=CONNECTION_TIMEOUT
                    )
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

                # Wait for current playback to finish
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

                # FIXED: Use FFmpegPCMAudio for WAV files
                tmp_file = None
                try:
                    # Create temporary file
                    tmp_file = tempfile.mktemp(suffix=self.tts_processor.file_extension)
                    with open(tmp_file, 'wb') as f:
                        f.write(audio_data)

                    # Use FFmpegPCMAudio instead of PCMAudio for WAV files
                    source = disnake.FFmpegPCMAudio(
                        tmp_file,
                        options="-loglevel warning"  # Reduce FFmpeg logging
                    )

                    # Get the bot's event loop for the after_playing callback
                    bot_loop = self.bot.loop

                    def after_playing(error: Optional[Exception]) -> None:
                        # Clean up temp file first
                        try:
                            if tmp_file and os.path.exists(tmp_file):
                                os.unlink(tmp_file)
                        except Exception as e:
                            self.logger.debug(f"Error cleaning temp file: {e}")

                        # Schedule cleanup in the bot's event loop
                        if bot_loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self._cleanup_playback(guild_id, sequence_id, error),
                                bot_loop
                            )

                    vc.play(source, after=after_playing)
                    self.logger.info(f"🎵 Playing message {sequence_id} (WAV format): {text[:50]}...")

                    # Wait briefly to ensure playback starts
                    await asyncio.sleep(0.5)

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

    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        """Handle new messages"""
        if message.author.bot:
            return
        if not message.guild:
            return

        # Channel filter
        if hasattr(self.cfg.discord, 'channel_id') and self.cfg.discord.channel_id:
            if message.channel.id != self.cfg.discord.channel_id:
                return

        # Ignore commands
        if message.content.startswith(('!', '/', '.')):
            return

        self.logger.info(f"📨 Received message from {message.author}: {message.content[:50]}...")
        await self._process_message(message)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member,
                                    before: disnake.VoiceState,
                                    after: disnake.VoiceState):
        """Handle voice state changes"""
        if member.bot:
            return

        guild_id = member.guild.id

        # Bot left voice
        if member.id == self.bot.user.id:
            if before.channel and not after.channel:
                await self.voice_manager.disconnect(guild_id)
            return

        # User left voice - check if channel is empty
        if before.channel and not after.channel:
            if self.voice_manager.is_connected(guild_id):
                vc = self.voice_manager.voice_clients.get(guild_id)
                if vc and vc.channel and vc.channel.id == before.channel.id:
                    human_members = [m for m in before.channel.members if not m.bot]
                    if not human_members:
                        self.logger.info("Channel empty, disconnecting")
                        await asyncio.sleep(2)
                        if (self.voice_manager.is_connected(guild_id) and
                                vc.channel and
                                not [m for m in vc.channel.members if not m.bot]):
                            await self.voice_manager.disconnect(guild_id)

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics"""
        stats = {
            "active_guild_queues": len([q for q in self.guild_queues.values() if not q.empty()]),
            "active_voice_connections": len(self.voice_manager.voice_clients),
            "cache_hit_rate": f"{len(self.tts_processor.audio_cache)}/{CACHE_SIZE}",
            "total_ordered_messages": sum(len(q) for q in self.guild_message_order.values()),
            "rate_limited_users": len(self.user_requests),
            "audio_format": self.tts_processor.preferred_format.upper(),
        }
        return stats

    @commands.slash_command(name="tts_stats", description="Check TTS system statistics")
    async def tts_stats(self, inter: disnake.ApplicationCommandInteraction):
        """Display TTS system statistics"""
        stats = self.get_performance_stats()

        embed = disnake.Embed(title="TTS System Statistics", color=disnake.Color.blue())
        for key, value in stats.items():
            embed.add_field(
                name=key.replace('_', ' ').title(),
                value=str(value),
                inline=True
            )

        await inter.response.send_message(embed=embed)

    async def cog_load(self):
        """Start background tasks when cog loads"""
        self.rate_cleanup_task = asyncio.create_task(self._cleanup_rate_limits())
        self.health_monitor_task = asyncio.create_task(self._monitor_queue_health())

    async def cog_unload(self):
        """Clean shutdown with better task management"""
        self.logger.info("Shutting down VoiceProcessingCog...")

        # Stop all processing first
        self.guild_processing.clear()

        # Cancel order futures
        for guild_id, message_list in self.guild_message_order.items():
            for sequence_id, future in message_list:
                if not future.done():
                    future.cancel()

        # Cancel background tasks
        tasks_to_cancel = []
        if self.rate_cleanup_task:
            tasks_to_cancel.append(self.rate_cleanup_task)
        if self.health_monitor_task:
            tasks_to_cancel.append(self.health_monitor_task)

        # Cancel guild processors
        for guild_id, task in self.guild_processing_tasks.items():
            if task:
                tasks_to_cancel.append(task)

        # Cancel all tasks
        for task in tasks_to_cancel:
            if not task.done():
                task.cancel()

        # Wait for cancellation
        if tasks_to_cancel:
            try:
                await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
            except Exception as e:
                self.logger.debug(f"Task cancellation: {e}")

        # Clear queues
        for queue in self.guild_queues.values():
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except Exception:
                    pass

        # Disconnect voice
        await self.voice_manager.disconnect_all()

        # Close TTS
        await self.tts_processor.close()

        self.logger.info("VoiceProcessingCog shutdown complete")


def setup(bot: commands.Bot):
    bot.add_cog(VoiceProcessingCog(bot))