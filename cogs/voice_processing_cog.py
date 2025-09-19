import asyncio
import datetime
import json
import os
import random
import re
import tempfile
import time
from typing import Dict, Optional, List
from collections import deque
import io

import aiohttp
import disnake
from disnake.ext import commands

# Constants
EMOJI_REGEX = re.compile(r"<:(\w+):\d+>")
MAX_RETRIES = 3
RETRY_DELAY = 0.5
PLAYBACK_TIMEOUT = 30
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "tts_history.json")
MAX_QUEUE_SIZE = 100
PRIORITY_USERS = []


def sanitize_emojis(text: str) -> str:
    """Convert custom emojis to text"""
    return EMOJI_REGEX.sub(lambda m: m.group(1), text)


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
    with open(HISTORY_PATH, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2)


def add_to_history(user_id: int, text: str, voice: str):
    """Add TTS request to history"""
    history = load_tts_history()
    user_key = str(user_id)

    if user_key not in history:
        history[user_key] = []

    history[user_key].append({
        "timestamp": datetime.datetime.now().isoformat(),
        "text": text[:500],
        "voice": voice
    })

    if len(history[user_key]) > 50:
        history[user_key] = history[user_key][-50:]

    save_tts_history(history)


class VoiceProcessingCog(commands.Cog):
    """Optimized TTS Processing with In-Memory Streaming"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = bot.config
        self.logger = bot.logger

        # TTS config
        tts_cfg = self.cfg.tts
        self.tts_url = tts_cfg.api_url
        self.tts_token = tts_cfg.bearer_token
        self.tts_model = tts_cfg.engine
        self.default_voice = tts_cfg.default_voice
        self.available_voices = tts_cfg.voices.get("available_voices", [])
        self.user_voice_map = tts_cfg.voices.get("user_voice_mappings", {})
        self.retry_limit = tts_cfg.retry_limit
        self.delay_between = tts_cfg.delay_between_messages
        self.rate_limit = 10
        self.last_requests = {}

        # Discord config
        d_cfg = self.cfg.discord
        self.guild_id = d_cfg.guild_id
        self.text_channel_id = d_cfg.channel_id
        self.no_mic_role_id = d_cfg.no_mic_role_id

        # Runtime state - optimized
        self.http = None
        self.guild_queues = {}
        self.guild_locks = {}
        self._shutdown_event = asyncio.Event()
        self.active_channels = set()

        # Audio cache to avoid re-processing the same text
        self.audio_cache = {}
        self.cache_size = 100
        self.cache_lock = asyncio.Lock()

        # Voice client tracking
        self.voice_clients = {}

        # Add audio format configuration
        self.audio_format = "mp3"  # Changed from default format
        # Fix FFmpeg options
        self.ffmpeg_options = {
            'before_options': '-nostdin',
            'options': '-vn -filter:a "volume=0.8"'
        }


    async def _get_http(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self.http and not self.http.closed:
            return self.http

        self.http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            connector=aiohttp.TCPConnector(limit_per_host=10)
        )
        return self.http

    def _check_rate_limit(self, user_id: int) -> bool:
        """Check if user is rate limited"""
        now = time.time()
        user_key = str(user_id)

        if user_key not in self.last_requests:
            self.last_requests[user_key] = []
            return True

        # Remove old requests
        self.last_requests[user_key] = [
            t for t in self.last_requests[user_key]
            if now - t < 60
        ]

        if len(self.last_requests[user_key]) < self.rate_limit:
            self.last_requests[user_key].append(now)
            return True

        return False

    async def _validate_voice_client(self, vc: disnake.VoiceClient) -> bool:
        """Validate voice client connection"""
        try:
            return vc and vc.is_connected() and not vc.is_playing()
        except Exception:
            return False

    async def _ensure_voice_client(self, guild_id: int, channel: disnake.VoiceChannel):
        """Ensure we have a working voice client"""
        try:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return None

            # Check if we already have a voice client for this guild
            if guild_id in self.voice_clients:
                vc = self.voice_clients[guild_id]
                if await self._validate_voice_client(vc):
                    if vc.channel and vc.channel.id != channel.id:
                        await vc.move_to(channel)
                    return vc
                else:
                    # Remove invalid voice client
                    del self.voice_clients[guild_id]

            vc = disnake.utils.get(self.bot.voice_clients, guild=guild)

            if vc and await self._validate_voice_client(vc):
                if vc.channel and vc.channel.id != channel.id:
                    await vc.move_to(channel)
                self.voice_clients[guild_id] = vc
                return vc

            # Create new connection with optimized settings
            vc = await channel.connect(
                timeout=15.0,
                reconnect=True
            )

            # Wait briefly for connection
            await asyncio.sleep(0.5)

            # Self-deafen
            await vc.guild.change_voice_state(channel=channel, self_deaf=True)

            # Store the voice client
            self.voice_clients[guild_id] = vc

            return vc

        except Exception as e:
            self.logger.error(f"Voice connection failed: {e}")
            return None

    async def _process_message(self, msg: disnake.Message):
        """Process a single message"""
        try:
            member = msg.author
            guild_id = msg.guild.id

            if not (member.voice and member.voice.channel):
                return

            # Get or create guild queue
            if guild_id not in self.guild_queues:
                self.guild_queues[guild_id] = deque(maxlen=MAX_QUEUE_SIZE)
                self.guild_locks[guild_id] = asyncio.Lock()

            # Add to queue with timestamp for ordering
            priority = 1 if member.id in PRIORITY_USERS else 0
            queue_entry = (time.monotonic(), priority, msg)
            self.guild_queues[guild_id].append(queue_entry)

            # Start processor if not running
            if guild_id not in self.active_channels:
                self.active_channels.add(guild_id)
                asyncio.create_task(self._process_guild_queue(guild_id))

        except Exception as e:
            self.logger.error(f"Queue error: {e}")

    async def _process_guild_queue(self, guild_id: int):
        """Process messages for a specific guild"""
        while self.guild_queues.get(guild_id) and not self._shutdown_event.is_set():
            try:
                async with self.guild_locks[guild_id]:
                    if not self.guild_queues[guild_id]:
                        await asyncio.sleep(0.1)
                        continue

                    # Get the highest priority message
                    sorted_queue = sorted(self.guild_queues[guild_id], key=lambda x: (-x[1], x[0]))
                    _, _, msg = sorted_queue[0]

                    # Remove the message from queue before processing
                    self.guild_queues[guild_id] = deque(
                        [item for item in self.guild_queues[guild_id] if item[2].id != msg.id],
                        maxlen=MAX_QUEUE_SIZE
                    )

                await self._handle_message(msg)
                await asyncio.sleep(0.05)

            except Exception as e:
                self.logger.error(f"Queue processing error: {e}")
                await asyncio.sleep(0.5)

        self.active_channels.discard(guild_id)

    async def _handle_message(self, msg: disnake.Message):
        """Handle a single message"""
        try:
            member = msg.author
            guild_id = msg.guild.id
            channel = member.voice.channel

            # Add no-mic role if needed
            if role := msg.guild.get_role(self.no_mic_role_id):
                try:
                    if role not in member.roles:
                        await member.add_roles(role)
                except Exception as e:
                    self.logger.warning(f"Failed to add no-mic role: {e}")

            # Check rate limit
            if not self._check_rate_limit(member.id):
                self.logger.warning(f"Rate limit exceeded for user {member.id}")
                try:
                    await msg.channel.send(
                        f"{member.mention} You're sending too many messages too quickly! "
                        f"Please wait before sending more.",
                        delete_after=5
                    )
                except:
                    pass
                return

            # Get voice ID
            voice_id = self.user_voice_map.get(str(member.id))
            if not voice_id:
                voice_id = random.choice(self.available_voices) if self.available_voices else self.default_voice
                self.user_voice_map[str(member.id)] = voice_id

            # Process text
            text = sanitize_emojis(msg.content.strip())
            if not text:
                return

            # Check cache first
            cache_key = f"{voice_id}:{text[:100]}"
            audio = None

            async with self.cache_lock:
                if cache_key in self.audio_cache:
                    audio = self.audio_cache[cache_key]
                    self.logger.info(f"Using cached audio for: {text[:50]}...")

            # Generate audio if not in cache
            if not audio:
                audio = await self._retry_tts(text, voice_id)
                if not audio:
                    return

                # Add to cache
                async with self.cache_lock:
                    if len(self.audio_cache) >= self.cache_size:
                        # Remove oldest item using LRU strategy
                        oldest_key = next(iter(self.audio_cache))
                        self.audio_cache.pop(oldest_key)
                    self.audio_cache[cache_key] = audio

            # Add to history
            add_to_history(member.id, text, voice_id)

            # Get voice client
            vc = await self._ensure_voice_client(guild_id, channel)
            if not vc:
                self.logger.error("Failed to get voice client")
                return

            # Play audio
            await self._play_audio(vc, audio)

        except Exception as e:
            self.logger.error(f"Message handling failed: {e}")

    async def _play_audio(self, vc: disnake.VoiceClient, audio: bytes):
        """Play audio with in-memory streaming"""
        if not vc or not vc.is_connected():
            self.logger.error("Voice client not valid for playback")
            return

        self.logger.info(f"Starting playback. Audio size: {len(audio)} bytes")

        try:
            # Create temporary file to avoid pipe issues
            with tempfile.NamedTemporaryFile(suffix=f'.{self.audio_format}', delete=False) as tmp_file:
                tmp_file.write(audio)
                tmp_file.flush()

                # Create audio source from file
                source = disnake.FFmpegPCMAudio(
                    source=tmp_file.name,
                    **self.ffmpeg_options
                )

            playback_done = asyncio.Future()

            def after_playback(error):
                # Clean up temp file
                try:
                    os.unlink(tmp_file.name)
                except:
                    pass

                if error:
                    self.logger.error(f"Playback error: {error}")
                if not playback_done.done():
                    playback_done.set_result(None)

            # Check connection before playing
            if not await self._validate_voice_client(vc):
                self.logger.error("Voice client disconnected right before playback")
                return

            vc.play(source, after=after_playback)

            try:
                await asyncio.wait_for(playback_done, timeout=PLAYBACK_TIMEOUT)
            except asyncio.TimeoutError:
                self.logger.warning("Playback timed out")
                vc.stop()

            # Additional delay between messages
            if self.delay_between:
                await asyncio.sleep(self.delay_between)

        except Exception as e:
            self.logger.error(f"Playback failed: {e}")
            # Clean up temp file on error
            try:
                os.unlink(tmp_file.name)
            except:
                pass

                # Remove from tracked voice clients
                for guild_id, client in list(self.voice_clients.items()):
                    if client == vc:
                        del self.voice_clients[guild_id]
                        break

    async def _call_tts(self, text: str, voice_id: str) -> Optional[bytes]:
        """Call TTS API with retries"""
        headers = {
            "Authorization": f"Bearer {self.tts_token}",
            "Content-Type": "application/json"
        }

        payload = {
            "voice": voice_id,
            "input": text,
            "model": self.tts_model
        }

        self.logger.info(f"Making TTS request to {self.tts_url} with voice {voice_id}")

        http = await self._get_http()
        try:
            # Increase timeout for TTS requests
            timeout = aiohttp.ClientTimeout(total=30)
            async with http.post(self.tts_url, json=payload, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    self.logger.info("TTS request successful")
                    return await resp.read()
                else:
                    error_text = await resp.text()
                    self.logger.error(f"TTS API error {resp.status}: {error_text}")
        except asyncio.TimeoutError:
            self.logger.error("TTS request timed out")
        except aiohttp.ClientError as e:
            self.logger.error(f"TTS network error: {e}")
        except Exception as e:
            self.logger.error(f"TTS unexpected error: {e}", exc_info=True)
        return None

    async def _retry_tts(self, text: str, voice_id: str) -> Optional[bytes]:
        """Retry TTS with backoff"""
        for attempt in range(self.retry_limit + 1):
            self.logger.info(f"TTS attempt {attempt + 1}/{self.retry_limit + 1}")
            data = await self._call_tts(text, voice_id)
            if data:
                return data
            if attempt < self.retry_limit:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
        self.logger.error("All TTS attempts failed")
        return None

    # Listeners
    @commands.Cog.listener()
    async def on_message(self, msg: disnake.Message):
        """Add messages to processing"""
        if (msg.author.bot or not msg.guild or
                msg.guild.id != self.guild_id or
                msg.channel.id != self.text_channel_id):
            return

        try:
            asyncio.create_task(self._process_message(msg))
        except Exception as e:
            self.logger.error(f"Message processing failed: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member,
                                    before: disnake.VoiceState,
                                    after: disnake.VoiceState):
        """Handle voice state changes"""
        # Only handle the bot's own voice state changes
        if member.id != self.bot.user.id:
            return

        guild_id = member.guild.id

        # Bot was disconnected
        if before.channel and not after.channel:
            self.logger.info(f"Bot was disconnected from voice in guild {guild_id}")
            # Clean up queue if bot is disconnected
            if guild_id in self.guild_queues:
                self.guild_queues[guild_id].clear()
            # Remove from tracked voice clients
            if guild_id in self.voice_clients:
                del self.voice_clients[guild_id]

                # Add this to the cog class
                # Move the debug command outside of the on_voice_state_update method
                @commands.slash_command(name="voice_debug", description="Debug voice connection")
                async def voice_debug(self, inter: disnake.ApplicationCommandInteraction):
                    """Debug voice connection status"""
                    if not inter.guild:
                        return await inter.response.send_message("This command only works in servers")

                    vc = self.voice_clients.get(inter.guild.id)
                    if vc:
                        status = (
                            f"Connected: {vc.is_connected()}\n"
                            f"Playing: {vc.is_playing()}\n"
                            f"Channel: {vc.channel.name if vc.channel else 'None'}\n"
                            f"Queue size: {len(self.guild_queues.get(inter.guild.id, []))}"
                        )
                    else:
                        status = "No voice client"

                    await inter.response.send_message(f"Voice status:\n{status}")

                # Add a test TTS command
                @commands.slash_command(name="test_tts", description="Test TTS API connection")
                async def test_tts(self, inter: disnake.ApplicationCommandInteraction, text: str = "Test message"):
                    """Test TTS API connection"""
                    await inter.response.defer()

                    # Get voice ID
                    voice_id = self.user_voice_map.get(str(inter.author.id)) or self.default_voice

                    # Test TTS call
                    audio = await self._call_tts(text, voice_id)
                    if audio:
                        await inter.followup.send("✅ TTS API is working!")
                    else:
                        await inter.followup.send("❌ TTS API failed. Check your API key and configuration.")
    # Cleanup
    def cog_unload(self):
        """Clean up resources"""
        self._shutdown_event.set()

        # Disconnect all voice clients
        for vc in self.bot.voice_clients:
            asyncio.create_task(self._safe_disconnect(vc))

        # Close HTTP session
        if self.http and not self.http.closed:
            asyncio.create_task(self._safe_close_http())

        self.logger.info("VoiceProcessingCog unloaded.")

    async def _safe_disconnect(self, vc: disnake.VoiceClient):
        """Safely disconnect voice client"""
        try:
            if vc.is_connected():
                await vc.disconnect(force=True)
        except Exception as e:
            self.logger.error(f"Disconnect error: {e}")

    async def _safe_close_http(self):
        """Safely close HTTP session"""
        try:
            await self.http.close()
        except Exception as e:
            self.logger.error(f"HTTP close error: {e}")


def setup(bot: commands.Bot):
    bot.add_cog(VoiceProcessingCog(bot))