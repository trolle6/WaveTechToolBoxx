import asyncio
import datetime
import json
import os
import random
import re
import tempfile
import time
from typing import Dict, Optional, List, Deque, Tuple
from collections import deque
import hashlib

import aiohttp
import disnake
from disnake.ext import commands

# Constants
EMOJI_REGEX = re.compile(r"<:(\w+):\d+>")
MAX_RETRIES = 3
RETRY_DELAY = 0.5
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "tts_history.json")
MAX_QUEUE_SIZE = 100
PRIORITY_USERS = []
MAX_TEXT_LENGTH = 2000
CACHE_SIZE = 200


def sanitize_emojis(text: str) -> str:
    """Convert custom emojis to text"""
    return EMOJI_REGEX.sub(lambda m: f":{m.group(1)}:", text)


def sanitize_text(text: str) -> str:
    """Sanitize text for TTS"""
    # Remove excessive whitespace and special characters that might break TTS
    text = re.sub(r'\s+', ' ', text.strip())
    # Remove or replace problematic characters
    text = text.replace('@', 'at ').replace('#', 'hash ')
    return text[:MAX_TEXT_LENGTH]


def load_tts_history() -> Dict[str, List[Dict]]:
    """Load TTS history from file"""
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    return {}


def save_tts_history(history: Dict[str, List[Dict]]):
    """Save TTS history to file"""
    try:
        with open(HISTORY_PATH, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to save TTS history: {e}")


def add_to_history(user_id: int, text: str, voice: str):
    """Add TTS request to history"""
    try:
        history = load_tts_history()
        user_key = str(user_id)

        if user_key not in history:
            history[user_key] = []

        history[user_key].append({
            "timestamp": datetime.datetime.now().isoformat(),
            "text": text[:500],
            "voice": voice
        })

        # Keep only last 50 entries per user
        if len(history[user_key]) > 50:
            history[user_key] = history[user_key][-50:]

        save_tts_history(history)
    except Exception as e:
        print(f"Failed to add to history: {e}")


class VoiceClientManager:
    """Manages voice client connections with connection pooling"""

    def __init__(self, bot: commands.Bot, logger):
        self.bot = bot
        self.logger = logger
        self.voice_clients: Dict[int, disnake.VoiceClient] = {}
        self.connection_lock = asyncio.Lock()
        self.connection_timeout = 10.0

    async def get_voice_client(self, guild_id: int, channel: disnake.VoiceChannel) -> Optional[disnake.VoiceClient]:
        """Get or create voice client for guild"""
        async with self.connection_lock:
            # Check if we have a valid existing connection
            if guild_id in self.voice_clients:
                vc = self.voice_clients[guild_id]
                if (vc and vc.is_connected() and vc.channel and
                        vc.channel.id == channel.id):
                    return vc
                else:
                    # Clean up invalid connection
                    if vc and vc.is_connected():
                        try:
                            await vc.disconnect(force=True)
                        except:
                            pass
                    del self.voice_clients[guild_id]

            # Create new connection
            try:
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    return None

                # Clean up any existing voice client for this guild
                if guild.voice_client and guild.voice_client.is_connected():
                    try:
                        await guild.voice_client.disconnect(force=True)
                        await asyncio.sleep(0.5)
                    except:
                        pass

                # Connect to voice channel
                vc = await channel.connect(
                    timeout=self.connection_timeout,
                    reconnect=True
                )

                # Wait for connection to be ready
                for _ in range(50):  # 5 second timeout
                    if vc.is_connected():
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise TimeoutError("Voice client failed to connect")

                self.voice_clients[guild_id] = vc
                self.logger.info(f"Connected to voice channel: {channel.name}")
                return vc

            except Exception as e:
                self.logger.error(f"Failed to connect to voice channel: {e}")
                return None

    async def disconnect(self, guild_id: int):
        """Disconnect from voice channel"""
        async with self.connection_lock:
            if guild_id in self.voice_clients:
                vc = self.voice_clients[guild_id]
                try:
                    if vc.is_connected():
                        await vc.disconnect(force=True)
                except Exception as e:
                    self.logger.error(f"Error disconnecting: {e}")
                finally:
                    del self.voice_clients[guild_id]

    async def disconnect_all(self):
        """Disconnect all voice clients"""
        async with self.connection_lock:
            for guild_id in list(self.voice_clients.keys()):
                await self.disconnect(guild_id)


class TTSProcessor:
    """Handles TTS API calls and audio processing"""

    def __init__(self, config, logger):
        self.tts_url = config.tts.api_url
        self.tts_token = config.tts.bearer_token
        self.tts_model = config.tts.engine
        self.default_voice = config.tts.default_voice
        self.available_voices = config.tts.voices.get("available_voices", [])
        self.user_voice_map = config.tts.voices.get("user_voice_mappings", {})
        self.retry_limit = config.tts.retry_limit
        self.logger = logger
        self.http_session = None
        self.audio_cache: Dict[str, bytes] = {}
        self.cache_lock = asyncio.Lock()

    async def get_http_session(self) -> aiohttp.ClientSession:
        """Get HTTP session with connection pooling"""
        if self.http_session is None or self.http_session.closed:
            timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
            connector = aiohttp.TCPConnector(limit_per_host=4, keepalive_timeout=30)
            self.http_session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={"User-Agent": "Discord-TTS-Bot/2.0"}
            )
        return self.http_session

    async def generate_audio(self, text: str, user_id: int) -> Optional[bytes]:
        """Generate audio from text with caching"""
        # Get voice for user
        voice_id = self.user_voice_map.get(str(user_id), self.default_voice)
        if not voice_id and self.available_voices:
            voice_id = random.choice(self.available_voices)

        # Create cache key
        text_hash = hashlib.md5(f"{voice_id}:{text}".encode()).hexdigest()
        cache_key = f"{voice_id}:{text_hash}"

        # Check cache
        async with self.cache_lock:
            if cache_key in self.audio_cache:
                self.logger.info(f"Using cached audio for: {text[:50]}...")
                return self.audio_cache[cache_key]

        # Generate new audio
        audio_data = await self._call_tts_api(text, voice_id)
        if audio_data:
            # Cache the result
            async with self.cache_lock:
                if len(self.audio_cache) >= CACHE_SIZE:
                    # Remove oldest entry
                    self.audio_cache.pop(next(iter(self.audio_cache)))
                self.audio_cache[cache_key] = audio_data

            add_to_history(user_id, text, voice_id)

        return audio_data

    async def _call_tts_api(self, text: str, voice_id: str) -> Optional[bytes]:
        """Make TTS API call with retry logic"""
        headers = {
            "Authorization": f"Bearer {self.tts_token}",
            "Content-Type": "application/json",
            "Accept": "audio/mpeg"
        }

        payload = {
            "voice": voice_id,
            "input": text,
            "model": self.tts_model,
            "response_format": "mp3",
            "speed": 1.0
        }

        for attempt in range(self.retry_limit + 1):
            try:
                http = await self.get_http_session()

                async with http.post(
                        self.tts_url,
                        json=payload,
                        headers=headers,
                        raise_for_status=True
                ) as response:

                    if response.status == 200:
                        audio_data = await response.read()
                        self.logger.info(f"TTS generated: {text[:50]}... ({len(audio_data)} bytes)")
                        return audio_data

            except aiohttp.ClientResponseError as e:
                if e.status == 429:  # Rate limited
                    wait_time = int(e.headers.get('Retry-After', 5))
                    self.logger.warning(f"Rate limited, waiting {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    self.logger.error(f"TTS API error {e.status}: {e.message}")
            except asyncio.TimeoutError:
                self.logger.error(f"TTS request timeout (attempt {attempt + 1})")
            except Exception as e:
                self.logger.error(f"TTS error (attempt {attempt + 1}): {e}")

            if attempt < self.retry_limit:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))

        self.logger.error("All TTS attempts failed")
        return None

    async def close(self):
        """Clean up resources"""
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()


class AudioPlayer:
    """Handles audio playback with proper resource management"""

    def __init__(self, logger, delay_between: float = 0.5):
        self.logger = logger
        self.delay_between = delay_between
        self.ffmpeg_options = {
            'before_options': '-nostdin',
            'options': '-vn -af "volume=0.8" -threads 1'
        }

    async def play_audio(self, vc: disnake.VoiceClient, audio_data: bytes) -> bool:
        """Play audio through voice client"""
        if not vc or not vc.is_connected():
            self.logger.error("Voice client not connected for playback")
            return False

        tmp_file = None
        try:
            # Create temporary file
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                f.write(audio_data)
                tmp_file = f.name

            # Create audio source
            source = disnake.FFmpegPCMAudio(tmp_file, **self.ffmpeg_options)

            # Play with completion tracking
            play_future = asyncio.Future()

            def after_playing(error):
                # Clean up temp file
                try:
                    if tmp_file and os.path.exists(tmp_file):
                        os.unlink(tmp_file)
                except Exception as e:
                    self.logger.warning(f"Temp file cleanup error: {e}")

                if error:
                    if not play_future.done():
                        play_future.set_exception(error)
                else:
                    if not play_future.done():
                        play_future.set_result(True)

            # Start playback
            vc.play(source, after=after_playing)

            # Wait for completion with timeout
            audio_duration = len(audio_data) / 16000  # Rough estimate
            timeout = min(max(audio_duration * 3, 10), 60)  # 10-60 second timeout

            try:
                await asyncio.wait_for(play_future, timeout=timeout)
                self.logger.info("Playback completed successfully")
                return True

            except asyncio.TimeoutError:
                self.logger.warning("Playback timeout")
                if vc.is_playing():
                    vc.stop()
                return False

        except Exception as e:
            self.logger.error(f"Playback error: {e}")
            # Clean up temp file on error
            try:
                if tmp_file and os.path.exists(tmp_file):
                    os.unlink(tmp_file)
            except:
                pass
            return False
        finally:
            # Small delay between messages
            if self.delay_between > 0:
                await asyncio.sleep(self.delay_between)


class VoiceProcessingCog(commands.Cog):
    """Optimized and reliable TTS Processing System"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = bot.config
        self.logger = bot.logger

        # Discord config
        d_cfg = self.cfg.discord
        self.guild_id = d_cfg.guild_id
        self.text_channel_id = d_cfg.channel_id
        self.no_mic_role_id = d_cfg.no_mic_role_id

        # Initialize components
        self.voice_manager = VoiceClientManager(bot, self.logger)
        self.tts_processor = TTSProcessor(self.cfg, self.logger)
        self.audio_player = AudioPlayer(self.logger, self.cfg.tts.delay_between_messages)

        # Queue management
        self.message_queues: Dict[int, Deque[disnake.Message]] = {}
        self.queue_locks: Dict[int, asyncio.Lock] = {}
        self.active_queues: set[int] = set()
        self.queue_processor_tasks: Dict[int, asyncio.Task] = {}

        # Rate limiting
        self.rate_limits: Dict[int, List[float]] = {}
        self.rate_limit = 10  # messages per minute
        self.rate_limit_window = 60

        # Shutdown flag
        self._shutting_down = False

        self.logger.info("VoiceProcessingCog initialized")

    def _check_rate_limit(self, user_id: int) -> bool:
        """Check if user is rate limited"""
        now = time.time()
        user_key = str(user_id)

        if user_key not in self.rate_limits:
            self.rate_limits[user_key] = []
            return True

        # Clean old requests
        self.rate_limits[user_key] = [
            t for t in self.rate_limits[user_key]
            if now - t < self.rate_limit_window
        ]

        if len(self.rate_limits[user_key]) < self.rate_limit:
            self.rate_limits[user_key].append(now)
            return True

        return False

    async def _add_to_queue(self, message: disnake.Message):
        """Add message to processing queue"""
        guild_id = message.guild.id

        # Initialize queue and lock for guild if needed
        if guild_id not in self.message_queues:
            self.message_queues[guild_id] = deque(maxlen=MAX_QUEUE_SIZE)
            self.queue_locks[guild_id] = asyncio.Lock()

        async with self.queue_locks[guild_id]:
            self.message_queues[guild_id].append(message)

        # Start queue processor if not already running
        if guild_id not in self.active_queues and not self._shutting_down:
            self.active_queues.add(guild_id)
            self.queue_processor_tasks[guild_id] = asyncio.create_task(
                self._process_guild_queue(guild_id),
                name=f"tts_queue_{guild_id}"
            )
            self.logger.info(f"Started queue processor for guild {guild_id}")

    async def _process_guild_queue(self, guild_id: int):
        """Process messages for a specific guild with persistent monitoring"""
        self.logger.info(f"Starting persistent queue processor for guild {guild_id}")

        consecutive_failures = 0
        max_failures = 5
        empty_queue_count = 0
        max_empty_checks = 60  # Wait up to 5 minutes before stopping (5s intervals)

        while (not self._shutting_down and
               consecutive_failures < max_failures and
               guild_id in self.active_queues):

            try:
                # Check if we have messages to process
                async with self.queue_locks[guild_id]:
                    has_messages = bool(self.message_queues.get(guild_id))

                if not has_messages:
                    empty_queue_count += 1
                    # Stop if queue has been empty for too long
                    if empty_queue_count >= max_empty_checks:
                        self.logger.info(f"Queue empty for too long, stopping processor for guild {guild_id}")
                        break

                    # Wait briefly before checking again
                    await asyncio.sleep(5)
                    continue

                # Reset empty counter since we found messages
                empty_queue_count = 0

                # Get next message from queue
                async with self.queue_locks[guild_id]:
                    if not self.message_queues[guild_id]:
                        continue
                    message = self.message_queues[guild_id].popleft()

                # Process the message with timeout
                try:
                    await asyncio.wait_for(
                        self._process_single_message(message),
                        timeout=45.0
                    )
                    consecutive_failures = 0

                except asyncio.TimeoutError:
                    self.logger.error(f"Message processing timeout for guild {guild_id}")
                    consecutive_failures += 1
                    await asyncio.sleep(1)

            except Exception as e:
                self.logger.error(f"Queue processing error: {e}")
                consecutive_failures += 1
                await asyncio.sleep(1)

        # Clean up
        self.active_queues.discard(guild_id)
        if guild_id in self.queue_processor_tasks:
            del self.queue_processor_tasks[guild_id]

        self.logger.info(f"Queue processor stopped for guild {guild_id}")

    async def _process_single_message(self, message: disnake.Message):
        """Process a single TTS message"""
        member = message.author
        guild_id = message.guild.id

        # Basic validation
        if not member or not member.voice or not member.voice.channel:
            return

        # Rate limiting
        if not self._check_rate_limit(member.id):
            self.logger.warning(f"Rate limit exceeded for user {member.id}")
            try:
                await message.channel.send(
                    f"{member.mention} Please wait before sending more messages.",
                    delete_after=5
                )
            except:
                pass
            return

        # Add no-mic role if configured
        if self.no_mic_role_id:
            try:
                role = message.guild.get_role(self.no_mic_role_id)
                if role and role not in member.roles:
                    await member.add_roles(role)
            except Exception as e:
                self.logger.warning(f"Failed to add no-mic role: {e}")

        # Sanitize and validate text
        text = sanitize_text(sanitize_emojis(message.content))
        if not text or len(text.strip()) < 1:
            return

        # Generate audio
        self.logger.info(f"Processing TTS for {member.display_name}: {text[:50]}...")
        audio_data = await self.tts_processor.generate_audio(text, member.id)

        if not audio_data:
            self.logger.error("Failed to generate audio")
            return

        # Get voice client and play audio
        voice_channel = member.voice.channel
        vc = await self.voice_manager.get_voice_client(guild_id, voice_channel)

        if not vc:
            self.logger.error("Failed to get voice client")
            return

        # Play audio
        success = await self.audio_player.play_audio(vc, audio_data)
        if not success:
            self.logger.warning("Audio playback failed")

    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        """Handle new messages for TTS processing"""
        if (self._shutting_down or message.author.bot or
                not message.guild or message.guild.id != self.guild_id or
                message.channel.id != self.text_channel_id or
                not message.content.strip()):
            return

        try:
            await self._add_to_queue(message)
        except Exception as e:
            self.logger.error(f"Failed to add message to queue: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member,
                                    before: disnake.VoiceState,
                                    after: disnake.VoiceState):
        """Handle voice state changes"""
        if self._shutting_down:
            return

        guild_id = member.guild.id

        # Bot voice state changes
        if member.id == self.bot.user.id:
            if before.channel and not after.channel:
                # Bot was disconnected
                await self.voice_manager.disconnect(guild_id)
                self.logger.info(f"Bot disconnected from voice in guild {guild_id}")
            return

        # User left voice channel
        if before.channel and not after.channel:
            if guild_id in self.voice_manager.voice_clients:
                vc = self.voice_manager.voice_clients[guild_id]
                if vc and vc.channel and vc.channel.id == before.channel.id:
                    # Check if channel is empty (only bot remains)
                    if len(vc.channel.members) == 1:
                        self.logger.info("Scheduling leave from empty channel")
                        await asyncio.sleep(15)  # Wait to see if someone rejoins

                        # Re-check after delay
                        if (vc.is_connected() and vc.channel and
                                len(vc.channel.members) == 1):
                            self.logger.info("Leaving empty voice channel")
                            await self.voice_manager.disconnect(guild_id)

    @commands.slash_command(name="voice_debug", description="Debug voice connection")
    async def voice_debug(self, inter: disnake.ApplicationCommandInteraction):
        """Debug voice connection status"""
        if not inter.guild:
            return await inter.response.send_message("This command only works in servers")

        guild_id = inter.guild.id
        vc = self.voice_manager.voice_clients.get(guild_id)

        status_lines = [
            f"**Voice Status for {inter.guild.name}**",
            f"Connected: {vc is not None and vc.is_connected()}",
            f"Playing: {vc is not None and vc.is_playing()}",
            f"Channel: {vc.channel.name if vc and vc.channel else 'None'}",
            f"Queue size: {len(self.message_queues.get(guild_id, []))}",
            f"Active queues: {len(self.active_queues)}",
            f"Voice clients: {len(self.voice_manager.voice_clients)}"
        ]

        await inter.response.send_message("\n".join(status_lines))

    @commands.slash_command(name="test_tts", description="Test TTS API connection")
    async def test_tts(self, inter: disnake.ApplicationCommandInteraction, text: str = "Test message"):
        """Test TTS API connection"""
        await inter.response.defer()

        audio_data = await self.tts_processor.generate_audio(text, inter.author.id)
        if audio_data:
            await inter.followup.send("✅ TTS API is working correctly!")
        else:
            await inter.followup.send("❌ TTS API failed. Check configuration.")

    @commands.slash_command(name="clear_queue", description="Clear the TTS queue")
    @commands.has_permissions(manage_messages=True)
    async def clear_queue(self, inter: disnake.ApplicationCommandInteraction):
        """Clear the TTS queue"""
        guild_id = inter.guild.id
        if guild_id in self.message_queues:
            async with self.queue_locks[guild_id]:
                queue_size = len(self.message_queues[guild_id])
                self.message_queues[guild_id].clear()
            await inter.response.send_message(f"✅ Cleared {queue_size} messages from queue")
        else:
            await inter.response.send_message("✅ Queue is already empty")

    async def cog_unload(self):
        """Clean up resources on cog unload"""
        self._shutting_down = True
        self.logger.info("Shutting down VoiceProcessingCog...")

        # Cancel all queue processor tasks
        for task in self.queue_processor_tasks.values():
            task.cancel()

        # Wait for tasks to complete
        if self.queue_processor_tasks:
            await asyncio.wait(
                list(self.queue_processor_tasks.values()),
                timeout=5.0,
                return_when=asyncio.ALL_COMPLETED
            )

        # Disconnect all voice clients
        await self.voice_manager.disconnect_all()

        # Close TTS processor
        await self.tts_processor.close()

        self.logger.info("VoiceProcessingCog shutdown complete")


def setup(bot: commands.Bot):
    bot.add_cog(VoiceProcessingCog(bot))