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
RETRY_DELAY = 0.2
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "tts_history.json")
MAX_QUEUE_SIZE = 100
PRIORITY_USERS = []
MAX_TEXT_LENGTH = 2000
CACHE_SIZE = 200


class QueuedMessage:
    """Represents a message with its processing state and order"""

    def __init__(self, message: disnake.Message, text: str):
        self.message = message
        self.text = text
        self.audio_data = None
        self.processed = asyncio.Event()
        self.processing = False
        self.timestamp = time.time()
        self.message_id = message.id
        self.created_at = message.created_at.timestamp()


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
        """Get or create voice client"""
        lock = self._get_connection_lock(guild_id)

        async with lock:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                self.logger.error(f"Guild {guild_id} not found")
                return None

            # Check existing connection
            existing_vc = self.voice_clients.get(guild_id)
            if existing_vc and existing_vc.is_connected():
                if existing_vc.channel and existing_vc.channel.id == channel.id:
                    self.logger.info(f"✅ Using existing connection to {channel.name}")
                    return existing_vc
                else:
                    try:
                        await existing_vc.move_to(channel)
                        self.logger.info(f"🔀 Moved to {channel.name}")
                        return existing_vc
                    except Exception as e:
                        self.logger.warning(f"Failed to move: {e}")

            # Clean up stale connection
            if guild_id in self.voice_clients:
                stale_vc = self.voice_clients[guild_id]
                if stale_vc and stale_vc.is_connected():
                    try:
                        await stale_vc.disconnect(force=True)
                    except Exception as e:
                        self.logger.debug(f"Error cleaning: {e}")
                del self.voice_clients[guild_id]

            # Create new connection
            try:
                self.logger.info(f"🔊 Connecting to {channel.name}...")

                # Use guild's voice_client if available
                if guild.voice_client and guild.voice_client.is_connected():
                    vc = guild.voice_client
                    await vc.move_to(channel)
                    self.voice_clients[guild_id] = vc
                    self.logger.info(f"🔀 Moved existing client to {channel.name}")
                    return vc

                # Create brand new connection
                vc = await channel.connect(timeout=10.0, reconnect=True)

                # Wait for connection
                for _ in range(20):  # 2 second timeout
                    if vc.is_connected():
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise TimeoutError("Voice connection timeout")

                self.voice_clients[guild_id] = vc
                self.logger.info(f"✅ Connected to {channel.name}")
                return vc

            except Exception as e:
                self.logger.error(f"❌ Connection failed: {e}")
                return None

    async def disconnect(self, guild_id: int):
        """Disconnect from voice channel"""
        lock = self._get_connection_lock(guild_id)

        async with lock:
            if guild_id in self.voice_clients:
                vc = self.voice_clients[guild_id]
                try:
                    if vc.is_connected():
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


class EnhancedTTSProcessor:
    """TTS processor"""

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
        """Get HTTP session"""
        if self.http_session is None or self.http_session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.http_session = aiohttp.ClientSession(timeout=timeout)
        return self.http_session

    async def generate_audio(self, text: str, user_id: int) -> Optional[bytes]:
        """Generate TTS audio"""
        enhanced_text = self._optimize_text_for_tts(text)
        voice_id = self.user_voice_map.get(str(user_id), self.default_voice)
        if not voice_id and self.available_voices:
            voice_id = random.choice(self.available_voices)

        # Cache check
        text_hash = hashlib.md5(f"{voice_id}:{enhanced_text}".encode()).hexdigest()
        cache_key = f"{voice_id}:{text_hash}"

        async with self.cache_lock:
            if cache_key in self.audio_cache:
                self.logger.debug(f"⚡ Cache hit for: {enhanced_text[:30]}...")
                return self.audio_cache[cache_key]

        # Generate new audio
        audio_data = await self._call_tts_api(enhanced_text, voice_id)

        if audio_data:
            async with self.cache_lock:
                if len(self.audio_cache) >= CACHE_SIZE:
                    # Remove oldest
                    oldest_key = next(iter(self.audio_cache))
                    del self.audio_cache[oldest_key]
                self.audio_cache[cache_key] = audio_data

        return audio_data

    async def _call_tts_api(self, text: str, voice_id: str) -> Optional[bytes]:
        """Call TTS API"""
        headers = {
            "Authorization": f"Bearer {self.tts_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "voice": voice_id,
            "input": text,
            "model": self.tts_model,
            "response_format": "mp3",
            "speed": 1.0,
        }

        for attempt in range(self.retry_limit + 1):
            try:
                http = await self.get_http_session()
                async with http.post(self.tts_url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        audio_data = await response.read()
                        self.logger.info(f"TTS generated: {text[:50]}...")
                        return audio_data
                    else:
                        self.logger.error(f"TTS API error {response.status}: {await response.text()}")

            except Exception as e:
                self.logger.error(f"TTS error (attempt {attempt + 1}): {e}")

            if attempt < self.retry_limit:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))

        self.logger.error("All TTS attempts failed")
        return None

    def _optimize_text_for_tts(self, text: str) -> str:
        """Optimize text for TTS"""
        text = text.strip()
        if not any(text.endswith(p) for p in ['.', '!', '?']):
            text += '.'
        text = enhance_short_messages(text)
        return text

    async def close(self):
        """Clean up"""
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()


class VoiceProcessingCog(commands.Cog):
    """Simplified TTS Processing System"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = bot.config
        self.logger = bot.logger

        # Initialize components
        self.voice_manager = VoiceClientManager(bot, self.logger)
        self.tts_processor = EnhancedTTSProcessor(self.cfg, self.logger)

        # Queue management
        self.message_queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self.is_processing = False
        self.processing_task: Optional[asyncio.Task] = None

        # Rate limiting
        self.user_requests: Dict[int, List[float]] = {}
        self.rate_limit = 10  # messages per minute
        self.rate_window = 60

        self.logger.info("VoiceProcessingCog initialized")

    def _check_rate_limit(self, user_id: int) -> bool:
        """Check rate limit"""
        now = time.time()
        if user_id not in self.user_requests:
            self.user_requests[user_id] = []

        # Clean old requests
        self.user_requests[user_id] = [t for t in self.user_requests[user_id] if now - t < self.rate_window]

        if len(self.user_requests[user_id]) < self.rate_limit:
            self.user_requests[user_id].append(now)
            return True
        return False

    async def _process_message(self, message: disnake.Message):
        """Process a single message"""
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
                await message.reply("❌ You need to be in a voice channel to use TTS!", ephemeral=True)
                return

            # Get voice client
            voice_channel = message.author.voice.channel
            vc = await self.voice_manager.get_voice_client(message.guild.id, voice_channel)
            if not vc:
                self.logger.error("Failed to connect to voice channel")
                await message.reply("❌ Failed to connect to voice channel.", ephemeral=True)
                return

            # Generate TTS audio
            self.logger.info(f"🔄 Generating TTS for: {text[:50]}...")
            audio_data = await self.tts_processor.generate_audio(text, message.author.id)
            if not audio_data:
                self.logger.error("TTS generation failed")
                await message.reply("❌ Failed to generate TTS audio.", ephemeral=True)
                return

            # Wait for current playback to finish
            if vc.is_playing():
                self.logger.debug("Waiting for current playback...")
                for _ in range(60):  # 6 second timeout
                    if not vc.is_playing():
                        break
                    await asyncio.sleep(0.1)
                else:
                    self.logger.warning("Playback timeout, stopping audio")
                    vc.stop()

            # Play audio
            self.logger.info(f"🎵 Playing TTS: {text[:50]}...")
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                f.write(audio_data)
                tmp_file = f.name

            try:
                source = disnake.FFmpegPCMAudio(tmp_file)
                play_complete = asyncio.Event()

                def after_playing(error):
                    try:
                        if os.path.exists(tmp_file):
                            os.unlink(tmp_file)
                    except:
                        pass
                    play_complete.set()

                vc.play(source, after=after_playing)

                # Wait for playback completion
                try:
                    await asyncio.wait_for(play_complete.wait(), timeout=30.0)
                    self.logger.info("✅ Playback completed")
                except asyncio.TimeoutError:
                    self.logger.warning("Playback timeout")
                    if vc.is_playing():
                        vc.stop()

            except Exception as e:
                self.logger.error(f"Playback error: {e}")
                try:
                    if os.path.exists(tmp_file):
                        os.unlink(tmp_file)
                except:
                    pass

        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    async def _queue_processor(self):
        """Process messages from queue"""
        self.logger.info("🚀 Starting queue processor")
        while True:
            try:
                message = await self.message_queue.get()
                await self._process_message(message)
                self.message_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Queue processor error: {e}")
                await asyncio.sleep(1)

    async def _add_to_queue(self, message: disnake.Message):
        """Add message to queue"""
        try:
            self.message_queue.put_nowait(message)
            self.logger.info(f"📥 Added message to queue: {message.content[:50]}...")

            # Start processor if not running
            if not self.is_processing or self.processing_task is None or self.processing_task.done():
                self.is_processing = True
                self.processing_task = asyncio.create_task(self._queue_processor())

        except asyncio.QueueFull:
            self.logger.warning("Queue is full, message dropped")
        except Exception as e:
            self.logger.error(f"Error adding to queue: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        """Handle new messages"""
        # Basic filters
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
        await self._add_to_queue(message)

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
            if guild_id in self.voice_manager.voice_clients:
                vc = self.voice_manager.voice_clients[guild_id]
                if vc and vc.channel and vc.channel.id == before.channel.id:
                    # Check if only bot remains
                    human_members = [m for m in before.channel.members if not m.bot]
                    if not human_members:
                        self.logger.info("Channel empty, disconnecting")
                        await asyncio.sleep(2)  # Brief delay
                        # Re-check
                        if (vc.is_connected() and vc.channel and
                                not [m for m in vc.channel.members if not m.bot]):
                            await self.voice_manager.disconnect(guild_id)

    @commands.slash_command(name="join", description="Make the bot join your voice channel")
    async def join_voice(self, inter: disnake.ApplicationCommandInteraction):
        """Force join voice channel"""
        if not inter.author.voice:
            await inter.response.send_message("❌ You need to be in a voice channel!", ephemeral=True)
            return

        vc = await self.voice_manager.get_voice_client(inter.guild.id, inter.author.voice.channel)
        if vc:
            await inter.response.send_message(f"✅ Joined {inter.author.voice.channel.name}", ephemeral=True)
        else:
            await inter.response.send_message("❌ Failed to join voice channel", ephemeral=True)

    @commands.slash_command(name="leave", description="Make the bot leave voice channel")
    async def leave_voice(self, inter: disnake.ApplicationCommandInteraction):
        """Force leave voice channel"""
        await self.voice_manager.disconnect(inter.guild.id)
        await inter.response.send_message("✅ Left voice channel", ephemeral=True)

    async def cog_unload(self):
        """Clean shutdown"""
        self.logger.info("Shutting down VoiceProcessingCog...")

        # Cancel processing task
        if self.processing_task and not self.processing_task.done():
            self.processing_task.cancel()
            try:
                await self.processing_task
            except asyncio.CancelledError:
                pass

        # Disconnect voice clients
        await self.voice_manager.disconnect_all()

        # Close TTS processor
        await self.tts_processor.close()

        self.logger.info("VoiceProcessingCog shutdown complete")


def setup(bot: commands.Bot):
    bot.add_cog(VoiceProcessingCog(bot))