"""
Voice Processing Cog for Discord Bot - Complete Rewrite
Handles TTS (Text-to-Speech) with robust connection handling
"""

import asyncio
import hashlib
import os
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, Optional, List

import disnake
from disnake.ext import commands

from . import utils


@dataclass
class TTSQueueItem:
    """Item in the TTS queue"""
    user_id: int
    channel_id: int
    text: str
    audio_data: bytes
    timestamp: float

    def is_expired(self, max_age: int = 30) -> bool:
        """Check if item is too old"""
        return (time.time() - self.timestamp) > max_age


class GuildVoiceState:
    """Voice state manager for a single guild"""

    def __init__(self, guild_id: int, logger):
        self.guild_id = guild_id
        self.logger = logger
        self.queue = asyncio.Queue(maxsize=20)
        self.processor_task: Optional[asyncio.Task] = None
        self.is_processing = False
        self.last_activity = time.time()
        self.stats = {"processed": 0, "dropped": 0, "errors": 0}

    def mark_active(self):
        """Update last activity time"""
        self.last_activity = time.time()

    def is_idle(self, timeout: int = 600) -> bool:
        """Check if state has been idle too long"""
        return (time.time() - self.last_activity) > timeout

    async def stop(self):
        """Stop the processor task"""
        if self.processor_task and not self.processor_task.done():
            self.processor_task.cancel()
            try:
                await asyncio.wait_for(self.processor_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            finally:
                self.processor_task = None


class VoiceProcessingCog(commands.Cog):
    """TTS voice processing cog"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("voice")

        # Check TTS configuration
        if not hasattr(bot.config, 'OPENAI_API_KEY') or not bot.config.OPENAI_API_KEY:
            self.logger.warning("OPENAI_API_KEY not configured - TTS disabled")
            self.enabled = False
            return

        self.enabled = True
        self.logger.info("TTS enabled")

        # Initialize components
        self.rate_limiter = utils.RateLimiter(limit=15, window=60)
        self.circuit_breaker = utils.CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60,
            success_threshold=2
        )
        self.cache = utils.LRUCache[bytes](max_size=100, ttl=3600)

        # Guild states
        self.guild_states: Dict[int, GuildVoiceState] = {}
        self._state_lock = asyncio.Lock()
        
        # Message deduplication
        self._processed_messages = set()
        self._message_cleanup_task = None

        # TTS configuration
        self.tts_url = "https://api.openai.com/v1/audio/speech"
        self.default_voice = "alloy"

        # Statistics
        self.total_requests = 0
        self.total_cached = 0
        self.total_failed = 0

        # Cleanup task
        self._cleanup_task = None
        self._shutdown = asyncio.Event()

        self.allowed_channel = bot.config.DISCORD_CHANNEL_ID

    async def cog_load(self):
        """Initialize cog"""
        if not self.enabled:
            return

        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._message_cleanup_task = asyncio.create_task(self._message_cleanup_loop())
        self.logger.info("Voice cog loaded")

    async def cog_unload(self):
        """Cleanup cog"""
        if not self.enabled:
            return

        self.logger.info("Unloading voice cog...")
        self._shutdown.set()

        # Stop cleanup tasks
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
                
        if self._message_cleanup_task:
            self._message_cleanup_task.cancel()
            try:
                await self._message_cleanup_task
            except asyncio.CancelledError:
                pass

        # Stop all guild processors
        async with self._state_lock:
            for state in self.guild_states.values():
                await state.stop()
            self.guild_states.clear()

        # Disconnect all voice clients
        for vc in list(self.bot.voice_clients):
            try:
                # Stop any playing audio first
                if vc.is_playing():
                    vc.stop()
                # Disconnect with force
                await asyncio.wait_for(vc.disconnect(force=True), timeout=5.0)
            except Exception as e:
                self.logger.error(f"Error disconnecting: {e}")
                # Force cleanup if normal disconnect fails
                try:
                    vc.cleanup()
                except Exception:
                    pass

        self.logger.info("Voice cog unloaded")

    async def _cleanup_loop(self):
        """Periodic cleanup of idle states"""
        try:
            while not self._shutdown.is_set():
                await asyncio.sleep(300)  # Every 5 minutes

                # Cleanup cache
                if hasattr(self.cache, 'cleanup'):
                    await self.cache.cleanup()

                # Check for empty voice channels
                for guild in self.bot.guilds:
                    if guild.voice_client and guild.voice_client.is_connected():
                        try:
                            channel = guild.voice_client.channel
                            if channel:
                                humans = [m for m in channel.members if not m.bot]
                                # If no humans and not playing, disconnect after longer timeout
                                if not humans and not guild.voice_client.is_playing():
                                    # Check if this guild has been idle for a while
                                    async with self._state_lock:
                                        state = self.guild_states.get(guild.id)
                                        if state and state.is_idle(timeout=300):  # 5 minutes
                                            self.logger.info(f"Auto-disconnecting from empty channel {channel.name}")
                                            await guild.voice_client.disconnect()
                                            await self._remove_state(guild.id)
                        except Exception as e:
                            self.logger.debug(f"Error checking voice channel for {guild.name}: {e}")

                # Find idle guild states
                async with self._state_lock:
                    idle_guilds = [
                        gid for gid, state in self.guild_states.items()
                        if state.is_idle()
                    ]

                # Cleanup idle guilds
                for guild_id in idle_guilds:
                    guild = self.bot.get_guild(guild_id)
                    if guild and guild.voice_client:
                        try:
                            await guild.voice_client.disconnect()
                        except Exception:
                            pass

                    async with self._state_lock:
                        if guild_id in self.guild_states:
                            await self.guild_states[guild_id].stop()
                            del self.guild_states[guild_id]

                if idle_guilds:
                    self.logger.info(f"Cleaned up {len(idle_guilds)} idle states")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Cleanup loop error: {e}", exc_info=True)

    async def _message_cleanup_loop(self):
        """Clean up old message IDs to prevent memory leaks"""
        try:
            while not self._shutdown.is_set():
                await asyncio.sleep(300)  # Every 5 minutes
                
                # Keep only recent message IDs (last 1000)
                if len(self._processed_messages) > 1000:
                    # Convert to list, sort by timestamp, keep newest 500
                    message_list = list(self._processed_messages)
                    self._processed_messages = set(message_list[-500:])
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Message cleanup loop error: {e}", exc_info=True)

    async def _cleanup_temp_file(self, temp_file: str):
        """Clean up temporary file with retry logic for Windows"""
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
                return  # Success
            except PermissionError:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(0.5)  # Wait before retry
                    continue
                else:
                    self.logger.warning(f"Could not delete temp file after {max_attempts} attempts: {temp_file}")
            except Exception as e:
                self.logger.error(f"Temp file cleanup error: {e}")
                break

    async def _get_or_create_state(self, guild_id: int) -> GuildVoiceState:
        """Get or create guild state"""
        async with self._state_lock:
            if guild_id not in self.guild_states:
                self.guild_states[guild_id] = GuildVoiceState(guild_id, self.logger)
            return self.guild_states[guild_id]

    async def _remove_state(self, guild_id: int):
        """Remove guild state"""
        async with self._state_lock:
            if guild_id in self.guild_states:
                await self.guild_states[guild_id].stop()
                del self.guild_states[guild_id]

    async def _connect_to_voice(
        self,
        channel: disnake.VoiceChannel,
        timeout: int = 10
    ) -> Optional[disnake.VoiceClient]:
        """Connect to voice channel with proper error handling"""

        guild = channel.guild

        # Check if already connected to the right channel
        if guild.voice_client:
            if guild.voice_client.channel and guild.voice_client.channel.id == channel.id:
                if guild.voice_client.is_connected():
                    return guild.voice_client

            # Disconnect from wrong channel or if not properly connected
            try:
                if guild.voice_client.is_connected():
                    await asyncio.wait_for(
                        guild.voice_client.disconnect(force=True),
                        timeout=3.0
                    )
                else:
                    # Force cleanup if not properly connected
                    guild.voice_client.cleanup()
                await asyncio.sleep(1.0)  # Wait longer for cleanup
            except Exception as e:
                self.logger.warning(f"Disconnect error: {e}")
                # Force cleanup even if disconnect fails
                try:
                    guild.voice_client.cleanup()
                except Exception:
                    pass

        # Try to connect with retry logic
        try:
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    vc = await asyncio.wait_for(
                        channel.connect(timeout=timeout, reconnect=False),
                        timeout=timeout + 5
                    )
                    break  # Success, exit retry loop
                except Exception as e:
                    if attempt < max_attempts - 1:
                        self.logger.warning(f"Connection attempt {attempt + 1} failed: {e}, retrying...")
                        await asyncio.sleep(1)
                        continue
                    else:
                        raise  # Last attempt failed, re-raise exception

            # Self-deafen
            try:
                await guild.change_voice_state(channel=channel, self_deaf=True)
            except Exception as e:
                self.logger.warning(f"Could not self-deafen: {e}")

            return vc

        except asyncio.TimeoutError:
            self.logger.error(f"Connection timeout to {channel.name}")
            return None
        except disnake.ClientException as e:
            error_msg = str(e)
            if "Already connected" in error_msg:
                self.logger.warning(f"Already connected to voice channel: {e}")
                # Try to get the existing connection
                if guild.voice_client and guild.voice_client.is_connected():
                    return guild.voice_client
            else:
                self.logger.error(f"Connection failed to {channel.name}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected connection error: {e}", exc_info=True)
            return None

    def _clean_text(self, text: str, max_length: int = 400) -> str:
        """Clean and process text for TTS"""
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text.strip())

        # Remove Discord emojis and mentions
        text = re.sub(r'<a?:\w+:\d+>', '', text)
        text = re.sub(r'<@!?\d+>', '', text)
        text = re.sub(r'<@&\d+>', '', text)
        text = re.sub(r'<#\d+>', '', text)

        # Remove URLs
        text = re.sub(r'https?://\S+', '', text)

        # Truncate
        if len(text) > max_length:
            text = text[:max_length - 3] + "..."

        # Ensure punctuation
        if text and text[-1] not in '.!?,;:':
            text += '.'

        return text.strip()

    def _cache_key(self, text: str, voice: str) -> str:
        """Generate cache key"""
        return hashlib.sha256(f"{voice}:{text}".encode()).hexdigest()[:16]

    async def _generate_tts(self, text: str, voice: str = None) -> Optional[bytes]:
        """Generate TTS audio"""

        # Check circuit breaker
        if not await self.circuit_breaker.can_attempt():
            self.logger.warning("Circuit breaker open")
            return None

        voice = voice or self.default_voice
        cache_key = self._cache_key(text, voice)

        # Check cache
        cached = await self.cache.get(cache_key)
        if cached:
            self.total_cached += 1
            return cached

        # Make API request
        self.total_requests += 1

        headers = {
            "Authorization": f"Bearer {self.bot.config.OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": 1.0
        }

        try:
            session = await self.bot.http_mgr.get_session(timeout=15)

            async with session.post(self.tts_url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    audio = await resp.read()
                    await self.cache.set(cache_key, audio)
                    await self.circuit_breaker.record_success()
                    return audio

                elif resp.status == 429:
                    self.logger.warning("Rate limited by TTS API")
                    await self.circuit_breaker.record_failure()
                    self.total_failed += 1
                    return None

                else:
                    error = await resp.text()
                    self.logger.error(f"TTS API error {resp.status}: {error}")
                    await self.circuit_breaker.record_failure()
                    self.total_failed += 1
                    return None

        except asyncio.TimeoutError:
            self.logger.error("TTS request timeout")
            await self.circuit_breaker.record_failure()
            self.total_failed += 1
            return None
        except Exception as e:
            self.logger.error(f"TTS request error: {e}", exc_info=True)
            await self.circuit_breaker.record_failure()
            self.total_failed += 1
            return None

    async def _play_audio(
        self,
        vc: disnake.VoiceClient,
        audio_data: bytes
    ) -> bool:
        """Play audio through voice client"""

        temp_file = None

        try:
            # Verify connection
            if not vc.is_connected():
                self.logger.error("Voice client not connected")
                return False

            # Wait for current audio to finish
            if vc.is_playing():
                for _ in range(50):  # Max 5 seconds
                    if not vc.is_playing():
                        break
                    await asyncio.sleep(0.1)
                else:
                    vc.stop()
                    await asyncio.sleep(0.2)

            # Create temp file
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                f.write(audio_data)
                temp_file = f.name

            # Prepare audio source
            audio = disnake.FFmpegPCMAudio(
                temp_file,
                before_options='-nostdin',
                options='-vn -af volume=0.6'
            )

            # Play with callback
            play_done = asyncio.Event()

            def after(error):
                if error:
                    self.logger.error(f"Playback error: {error}")
                play_done.set()

                # Cleanup temp file synchronously to avoid event loop issues
                if temp_file and os.path.exists(temp_file):
                    try:
                        # Try to delete immediately
                        os.unlink(temp_file)
                    except PermissionError:
                        # If fails, schedule for later cleanup
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                loop.create_task(self._cleanup_temp_file(temp_file))
                        except RuntimeError:
                            # No event loop, just log the issue
                            self.logger.warning(f"Could not schedule cleanup for {temp_file}")
                    except Exception as e:
                        self.logger.error(f"Temp file cleanup error: {e}")

            vc.play(audio, after=after)

            # Wait for playback to start
            for _ in range(30):  # Max 3 seconds
                if vc.is_playing():
                    break
                await asyncio.sleep(0.1)
            else:
                self.logger.error("Playback failed to start")
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except Exception as e:
                        self.logger.error(f"Temp file cleanup error: {e}")
                return False

            # Wait for playback to complete
            try:
                await asyncio.wait_for(play_done.wait(), timeout=60)
                return True
            except asyncio.TimeoutError:
                self.logger.warning("Playback timeout")
                vc.stop()
                return False

        except Exception as e:
            self.logger.error(f"Playback error: {e}", exc_info=True)
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except Exception as cleanup_error:
                    self.logger.error(f"Temp file cleanup error: {cleanup_error}")
            return False

    async def _process_queue(self, guild_id: int):
        """Process TTS queue for a guild"""

        state = await self._get_or_create_state(guild_id)
        guild = self.bot.get_guild(guild_id)

        if not guild:
            return

        self.logger.debug(f"Starting queue processor for {guild.name}")
        state.is_processing = True

        try:
            while not self._shutdown.is_set():
                try:
                    # Get next item (5 min timeout)
                    item = await asyncio.wait_for(state.queue.get(), timeout=300)
                    state.mark_active()

                    # Check if expired
                    if item.is_expired():
                        self.logger.debug("Dropping expired item")
                        state.stats["dropped"] += 1
                        continue

                    # Get user's voice channel
                    member = guild.get_member(item.user_id)
                    if not member or not member.voice or not member.voice.channel:
                        self.logger.debug("User not in voice channel")
                        state.stats["dropped"] += 1
                        continue

                    channel = member.voice.channel

                    # Connect to voice
                    vc = await self._connect_to_voice(channel)
                    if not vc:
                        self.logger.error("Failed to connect to voice")
                        state.stats["errors"] += 1
                        continue

                    # Play audio
                    success = await self._play_audio(vc, item.audio_data)

                    if success:
                        state.stats["processed"] += 1
                    else:
                        state.stats["errors"] += 1

                except asyncio.TimeoutError:
                    # No items for 5 minutes, exit
                    self.logger.debug(f"Queue timeout for {guild.name}")
                    break

                except Exception as e:
                    self.logger.error(f"Queue processing error: {e}", exc_info=True)
                    state.stats["errors"] += 1
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            self.logger.debug(f"Queue processor cancelled for {guild.name}")

        finally:
            state.is_processing = False

            # Only disconnect if no humans are in the channel
            if guild.voice_client and guild.voice_client.is_connected():
                try:
                    channel = guild.voice_client.channel
                    if channel:
                        humans = [m for m in channel.members if not m.bot]
                        if not humans:
                            self.logger.info(f"Queue processor finished - disconnecting from {channel.name}")
                            await guild.voice_client.disconnect()
                        else:
                            self.logger.info(f"Queue processor finished - staying connected (humans: {len(humans)})")
                except Exception as e:
                    self.logger.error(f"Disconnect error: {e}")

            # Remove state
            await self._remove_state(guild_id)

    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        """Handle incoming messages for TTS"""

        # Skip if disabled or bot message
        if not self.enabled or message.author.bot:
            return

        # Skip if not in guild
        if not message.guild:
            return

        # Check for duplicate message processing
        message_key = f"{message.id}:{message.author.id}:{message.content[:50]}"
        if message_key in self._processed_messages:
            return
        self._processed_messages.add(message_key)

        # Check channel whitelist
        if self.allowed_channel and message.channel.id != self.allowed_channel:
            return

        # Check if user is in voice
        if not message.author.voice or not message.author.voice.channel:
            return

        # Check rate limit
        if not await self.rate_limiter.check(str(message.author.id)):
            return

        # Clean text
        text = self._clean_text(message.content)
        if not text or len(text) < 2:
            return

        # Generate TTS
        audio = await self._generate_tts(text)
        if not audio:
            return

        # Add to queue
        state = await self._get_or_create_state(message.guild.id)

        item = TTSQueueItem(
            user_id=message.author.id,
            channel_id=message.author.voice.channel.id,
            text=text,
            audio_data=audio,
            timestamp=time.time()
        )

        try:
            state.queue.put_nowait(item)

            # Start processor if not running
            if not state.is_processing:
                state.processor_task = asyncio.create_task(
                    self._process_queue(message.guild.id)
                )

        except asyncio.QueueFull:
            self.logger.warning(f"Queue full for {message.guild.name}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice state changes"""

        if not self.enabled:
            return

        # Bot was disconnected
        if member.id == self.bot.user.id and before.channel and not after.channel:
            await self._remove_state(member.guild.id)
            return

        # User left voice - wait longer and check more carefully
        if before.channel and not after.channel:
            # Wait longer to ensure all voice state updates are processed
            await asyncio.sleep(5)

            guild = member.guild
            if not guild.voice_client:
                return

            # Double-check the voice client is still connected
            if not guild.voice_client.is_connected():
                return

            # Check if any humans are still in the channel
            try:
                channel = guild.voice_client.channel
                if not channel:
                    return
                    
                humans = [m for m in channel.members if not m.bot]
                
                self.logger.debug(f"Voice channel check: {len(humans)} humans remaining in {channel.name}")

                # Only disconnect if no humans AND no audio is playing
                if not humans and not guild.voice_client.is_playing():
                    # Wait a bit more to make sure no new messages are coming
                    await asyncio.sleep(3)
                    
                    # Final check
                    if not guild.voice_client.is_connected():
                        return
                        
                    final_humans = [m for m in guild.voice_client.channel.members if not m.bot] if guild.voice_client.channel else []
                    
                    if not final_humans and not guild.voice_client.is_playing():
                        self.logger.info(f"Disconnecting from {channel.name} - no humans remaining")
                        try:
                            await guild.voice_client.disconnect()
                            await self._remove_state(guild.id)
                        except Exception as e:
                            self.logger.error(f"Disconnect error: {e}")
                    else:
                        self.logger.debug(f"Staying connected - humans: {len(final_humans)}, playing: {guild.voice_client.is_playing()}")
                        
            except Exception as e:
                self.logger.error(f"Error checking voice state: {e}")

    @commands.slash_command(name="tts")
    async def tts_cmd(self, inter: disnake.ApplicationCommandInteraction):
        """TTS commands"""
        pass

    @tts_cmd.sub_command(name="stats", description="View TTS statistics")
    async def tts_stats(self, inter: disnake.ApplicationCommandInteraction):
        """Show TTS stats"""
        await inter.response.defer(ephemeral=True)

        if not self.enabled:
            await inter.edit_original_response(content="❌ TTS is disabled")
            return

        cache_stats = await self.cache.get_stats()
        breaker_stats = await self.circuit_breaker.get_metrics()

        embed = disnake.Embed(title="🎵 TTS Statistics", color=disnake.Color.blue())

        embed.add_field(
            name="API",
            value=f"Requests: {self.total_requests}\n"
                  f"Cached: {self.total_cached}\n"
                  f"Failed: {self.total_failed}",
            inline=True
        )

        embed.add_field(
            name="Cache",
            value=f"Size: {cache_stats['size']}/{cache_stats['max_size']}\n"
                  f"Hit Rate: {cache_stats['hit_rate']:.1f}%",
            inline=True
        )

        embed.add_field(
            name="Circuit Breaker",
            value=f"State: {breaker_stats['state']}\n"
                  f"Failures: {breaker_stats['current_failures']}",
            inline=True
        )

        active = sum(1 for s in self.guild_states.values() if s.is_processing)

        embed.add_field(
            name="Guilds",
            value=f"Active: {active}\n"
                  f"Voice Clients: {len(self.bot.voice_clients)}",
            inline=True
        )

        await inter.edit_original_response(embed=embed)

    @tts_cmd.sub_command(name="disconnect", description="Disconnect bot from voice")
    @commands.has_permissions(manage_guild=True)
    async def tts_disconnect(self, inter: disnake.ApplicationCommandInteraction):
        """Force disconnect"""
        await inter.response.defer(ephemeral=True)

        if not inter.guild.voice_client:
            await inter.edit_original_response(content="❌ Not connected")
            return

        try:
            await inter.guild.voice_client.disconnect()
            await self._remove_state(inter.guild.id)
            await inter.edit_original_response(content="✅ Disconnected")
        except Exception as e:
            await inter.edit_original_response(content=f"❌ Error: {e}")

    @tts_cmd.sub_command(name="clear", description="Clear TTS queue")
    @commands.has_permissions(manage_guild=True)
    async def tts_clear(self, inter: disnake.ApplicationCommandInteraction):
        """Clear queue"""
        await inter.response.defer(ephemeral=True)

        async with self._state_lock:
            if inter.guild.id in self.guild_states:
                state = self.guild_states[inter.guild.id]

                # Clear queue
                while not state.queue.empty():
                    try:
                        state.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                await inter.edit_original_response(content="✅ Queue cleared")
            else:
                await inter.edit_original_response(content="❌ No active queue")

    @tts_cmd.sub_command(name="status", description="Check voice channel status")
    async def tts_status(self, inter: disnake.ApplicationCommandInteraction):
        """Check voice status"""
        await inter.response.defer(ephemeral=True)

        if not inter.guild.voice_client:
            await inter.edit_original_response(content="❌ Bot not connected to voice")
            return

        vc = inter.guild.voice_client
        channel = vc.channel
        
        if not channel:
            await inter.edit_original_response(content="❌ No voice channel found")
            return

        humans = [m for m in channel.members if not m.bot]
        bots = [m for m in channel.members if m.bot]
        
        async with self._state_lock:
            state = self.guild_states.get(inter.guild.id)
            queue_size = state.queue.qsize() if state else 0
            is_processing = state.is_processing if state else False

        embed = disnake.Embed(
            title="🎵 Voice Channel Status",
            color=disnake.Color.blue()
        )
        
        embed.add_field(
            name="Channel",
            value=f"**{channel.name}**\nID: {channel.id}",
            inline=True
        )
        
        embed.add_field(
            name="Members",
            value=f"👥 Humans: {len(humans)}\n🤖 Bots: {len(bots)}",
            inline=True
        )
        
        embed.add_field(
            name="Status",
            value=f"🔊 Playing: {'Yes' if vc.is_playing() else 'No'}\n📋 Queue: {queue_size}\n⚙️ Processing: {'Yes' if is_processing else 'No'}",
            inline=True
        )
        
        if humans:
            human_names = [m.display_name for m in humans[:5]]
            if len(humans) > 5:
                human_names.append(f"... and {len(humans) - 5} more")
            embed.add_field(
                name="👥 Humans in Channel",
                value="\n".join(human_names),
                inline=False
            )

        await inter.edit_original_response(embed=embed)


def setup(bot):
    """Setup the cog"""
    bot.add_cog(VoiceProcessingCog(bot))