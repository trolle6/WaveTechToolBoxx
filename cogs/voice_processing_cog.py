"""
Voice Processing Cog - Text-to-Speech with Smart Features

FEATURES:
- 🎤 Automatic TTS for messages from users in voice channels
- 🎭 Pronoun-based voice assignment (6 voices: alloy, echo, fable, onyx, nova, shimmer)
- 🤖 AI pronunciation improvement for acronyms and usernames
- 📝 Smart grammar corrections
- 👤 Name announcement (first message per session)
- ⚡ LRU caching for TTS audio and pronunciations
- 🔧 Circuit breaker for API failure protection
- 🚦 Rate limiting

COMMANDS:
- /tts stats - View performance metrics
- /tts disconnect - Force disconnect (admin)
- /tts clear - Clear TTS queue (admin)
- /tts status - Check voice channel status
"""

import asyncio
import os
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, Optional, Set

import disnake
from disnake.ext import commands

from . import utils


@dataclass
class TTSQueueItem:
    """TTS queue item"""
    user_id: int
    channel_id: int
    text: str
    voice: str
    audio_data: Optional[bytes] = None
    timestamp: float = 0.0

    def is_expired(self, max_age: int = 60) -> bool:
        return (time.time() - self.timestamp) > max_age


class GuildVoiceState:
    """Voice state manager for a guild"""
    
    def __init__(self, guild_id: int, logger, max_queue_size: int = 20):
        self.guild_id = guild_id
        self.logger = logger
        self.queue = asyncio.Queue(maxsize=max_queue_size)
        self.processor_task: Optional[asyncio.Task] = None
        self.is_processing = False
        self.last_activity = time.time()
        self.stats = {"processed": 0, "dropped": 0, "errors": 0}

    def mark_active(self):
        self.last_activity = time.time()

    def is_idle(self, timeout: int = 600) -> bool:
        return (time.time() - self.last_activity) > timeout

    async def stop(self):
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

        # Check API key
        if not hasattr(bot.config, 'OPENAI_API_KEY') or not bot.config.OPENAI_API_KEY:
            self.logger.warning("OPENAI_API_KEY not configured - TTS disabled")
            self.enabled = False
            return

        self.enabled = True
        self.logger.info("TTS enabled")
        
        # Pre-compile regex patterns
        self._compiled_corrections = self._compile_correction_patterns()
        self._discord_cleanup_pattern = re.compile(
            r'<a?:\w+:\d+>|<@!?\d+>|<@&\d+>|<#\d+>|https?://\S+'
        )

        # Initialize components
        rate_limit = getattr(bot.config, 'RATE_LIMIT_REQUESTS', 15)
        rate_window = getattr(bot.config, 'RATE_LIMIT_WINDOW', 60)
        max_cache = getattr(bot.config, 'MAX_TTS_CACHE', 100)
        
        self.rate_limiter = utils.RateLimiter(limit=rate_limit, window=rate_window)
        self.circuit_breaker = utils.CircuitBreaker(
            failure_threshold=5, recovery_timeout=60, success_threshold=2
        )
        self.cache = utils.LRUCache[bytes](max_size=max_cache, ttl=3600)
        self.pronunciation_cache = utils.LRUCache[str](max_size=200, ttl=7200)

        # Guild states
        self.guild_states: Dict[int, GuildVoiceState] = {}
        self._state_lock = asyncio.Lock()
        self.max_queue_size = getattr(bot.config, 'MAX_QUEUE_SIZE', 20)
        
        # Message deduplication
        self._processed_messages: Set[str] = set()
        self._processed_messages_lock = asyncio.Lock()
        
        # Name announcement tracking
        self._announced_users: Dict[int, Set[int]] = {}
        self._announcement_lock = asyncio.Lock()

        # TTS config
        self.tts_url = "https://api.openai.com/v1/audio/speech"
        self.default_voice = "alloy"
        self.available_voices = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
        
        # Voice assignments (in-memory, session-based)
        self._voice_assignments: Dict[str, str] = {}
        self._voice_lock = asyncio.Lock()
        
        # TTS role requirement (optional)
        tts_role_id = getattr(bot.config, 'TTS_ROLE_ID', None)
        # Convert to int to match role.id type
        self.tts_role_id = int(tts_role_id) if tts_role_id else None
        if self.tts_role_id:
            self.logger.info(f"TTS role requirement enabled: {self.tts_role_id}")

        # Statistics
        self.total_requests = 0
        self.total_cached = 0
        self.total_failed = 0

        # Cleanup tasks
        self._cleanup_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()
        self._unloaded = False

        # Convert to int to match message.channel.id type
        # Config loads DISCORD_CHANNEL_ID as string from env, convert to int
        channel_id_raw = bot.config.DISCORD_CHANNEL_ID
        if channel_id_raw:
            try:
                # Ensure it's a string, strip whitespace, then convert to int
                channel_id_str = str(channel_id_raw).strip()
                self.allowed_channel = int(channel_id_str)
                self.logger.info(
                    f"Allowed channel configured: {self.allowed_channel} "
                    f"(from config: {repr(channel_id_raw)}, type: {type(self.allowed_channel).__name__})"
                )
            except (ValueError, TypeError) as e:
                self.logger.error(f"Failed to convert DISCORD_CHANNEL_ID to int: {repr(channel_id_raw)} - {e}")
                self.allowed_channel = None
        else:
            self.allowed_channel = None
            self.logger.warning("DISCORD_CHANNEL_ID not set - TTS will work in all channels")

    # ============ PRONOUN DETECTION ============
    async def _detect_pronouns(self, member: disnake.Member) -> Optional[str]:
        """Detect pronouns from display name/username"""
        text = f"{member.display_name} {member.name}".lower()
        
        # Formal patterns: (he/him), she/her, [they/them]
        if any(p in text for p in ['he/him', 'he / him', '(he)', '[he]']):
            return 'he'
        if any(p in text for p in ['she/her', 'she / her', '(she)', '[she]']):
            return 'she'
        if any(p in text for p in ['they/them', 'they / them', '(they)', '[they]']):
            return 'they'
        
        # Casual patterns: | he, - she, • they
        match = re.search(r'[|\-•\[\]\(\)]\s*(he|she|they)', text)
        if match:
            return match.group(1)
        
        # Descriptive terms
        if re.search(r'\b(man|guy|dude|male|boy|bro|mr|king)\b', text):
            return 'he'
        if re.search(r'\b(woman|girl|gal|female|lady|sis|ms|queen)\b', text):
            return 'she'
        
        # Standalone pronouns at end
        match = re.search(r'\s+(he|she|they)\s*$', text)
        if match:
            return match.group(1)
        
        return None

    def _get_voice_for_pronouns(self, pronouns: Optional[str]) -> str:
        """Map pronouns to voice"""
        if pronouns == 'he':
            return 'echo' if hash(time.time()) % 2 == 0 else 'onyx'
        elif pronouns == 'she':
            return 'nova' if hash(time.time()) % 2 == 0 else 'shimmer'
        elif pronouns == 'they':
            return 'alloy'
        return 'alloy'  # Default

    async def _get_voice_for_user(self, member: disnake.Member) -> str:
        """Get or assign voice for user"""
        user_key = str(member.id)
        
        # Check role requirement
        if self.tts_role_id:
            if not any(role.id == self.tts_role_id for role in member.roles):
                return self.default_voice
        
        # Return existing assignment
        async with self._voice_lock:
            if user_key in self._voice_assignments:
                voice = self._voice_assignments[user_key]
                if voice in self.available_voices:
                    return voice
        
        # Detect and assign new voice
        pronouns = await self._detect_pronouns(member)
        new_voice = self._get_voice_for_pronouns(pronouns)
        
        async with self._voice_lock:
            self._voice_assignments[user_key] = new_voice
        
        return new_voice

    # ============ TEXT PROCESSING ============
    def _compile_correction_patterns(self) -> list[tuple[re.Pattern, str]]:
        """Pre-compile correction patterns"""
        corrections = {
            r'\bim\b': "I'm", r'\byoure\b': "you're", r'\btheyre\b': "they're",
            r'\bwere\b': "we're", r'\bitsnt\b': "isn't", r'\bdoesnt\b': "doesn't",
            r'\bdidnt\b': "didn't", r'\bwont\b': "won't", r'\bcant\b': "can't",
            r'\bshouldnt\b': "shouldn't", r'\bcouldnt\b': "couldn't",
            r'\bwouldnt\b': "wouldn't", r'\bhavent\b': "haven't",
            r'\bhasnt\b': "hasn't", r'\bhadnt\b': "hadn't", r'\barent\b': "aren't",
            r'\bwerent\b': "weren't", r'\bwasnt\b': "wasn't",
            r'\bmustnt\b': "mustn't", r'\bneednt\b': "needn't",
            r'\boughtnt\b': "oughtn't", r'\bshant\b': "shan't",
        }
        return [(re.compile(p, re.IGNORECASE), r) for p, r in corrections.items()]

    def _detect_needs_pronunciation_help(self, text: str) -> bool:
        """Check if text needs AI pronunciation help"""
        return bool(
            re.findall(r'\b[A-Z]{2,4}\b', text) or  # Acronyms
            re.findall(r'\b[a-z]+[A-Z]+[a-z]*\b|\b[A-Z]+[a-z]+[A-Z]+\b', text) or  # Mixed case
            re.findall(r'\b[A-Za-z]+\d+\b|\b\d+[A-Za-z]+\b', text)  # Alphanumeric
        )

    async def _improve_pronunciation(self, text: str) -> str:
        """Use AI to improve pronunciation"""
        if not hasattr(self.bot.config, 'OPENAI_API_KEY') or not self.bot.config.OPENAI_API_KEY:
            return text
        
        # Check cache
        cached = await self.pronunciation_cache.get(text)
        if cached:
            return cached
        
        try:
            prompt = (
                "Rewrite this text ONLY to improve pronunciation for text-to-speech. "
                "Only expand very short acronyms (2-4 letters) into their letter names (e.g., 'JKM' → 'Jay Kay Em'). "
                "Convert complex usernames/gamertags to speakable form (e.g., 'xXDarkLordXx' → 'Dark Lord'). "
                "DO NOT expand normal capitalized words or sentences - leave them as-is. "
                "Keep all other words exactly the same. Don't change grammar, meaning, or add extra words.\n\n"
                f"Text: {text}\n\nImproved:"
            )
            
            headers = {
                "Authorization": f"Bearer {self.bot.config.OPENAI_API_KEY}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.1
            }
            
            session = await self.bot.http_mgr.get_session(timeout=10)
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers=headers
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    improved = result["choices"][0]["message"]["content"].strip()
                    improved = improved.replace("Improved:", "").strip()
                    final_text = improved if improved else text
                    await self.pronunciation_cache.set(text, final_text)
                    return final_text
        except Exception:
            pass
        
        return text

    def _apply_corrections(self, text: str) -> str:
        """Apply grammar corrections"""
        for pattern, replacement in self._compiled_corrections:
            text = pattern.sub(replacement, text)
        return re.sub(r'\s+', ' ', text)

    async def _clean_text(self, text: str, max_length: Optional[int] = None) -> str:
        """Clean and process text for TTS
        
        Args:
            text: Text to clean
            max_length: Optional max length (None = no truncation, used for splitting)
        
        OpenAI TTS API supports up to 4096 characters per request.
        """
        original_length = len(text)
        text = re.sub(r'\s+', ' ', text.strip())
        after_whitespace = len(text)
        text = self._discord_cleanup_pattern.sub('', text)
        after_discord_cleanup = len(text)
        text = self._apply_corrections(text)
        after_corrections = len(text)
        self.logger.debug(f"Text cleaning: {original_length} → {after_whitespace} (whitespace) → {after_discord_cleanup} (discord) → {after_corrections} (corrections)")

        if self._detect_needs_pronunciation_help(text):
            text = await self._improve_pronunciation(text)

        # Truncate if max_length specified and exceeds limit
        if max_length and len(text) > max_length:
            # Try to truncate at sentence boundary for natural breaks
            truncated = text[:max_length]
            last_period = truncated.rfind('.')
            last_exclamation = truncated.rfind('!')
            last_question = truncated.rfind('?')
            last_break = max(last_period, last_exclamation, last_question)
            
            # Only use sentence break if found and keeps at least 80% of text
            if last_break >= 0 and last_break > max_length * 0.8:
                text = truncated[:last_break + 1]
            else:
                # No good sentence break found, truncate cleanly
                text = truncated.rstrip() + "..."

        # Only add period if we're not splitting (max_length=None means we'll split)
        if max_length is not None and text and text[-1] not in '.!?,;:':
            text += '.'

        final_text = text.strip()
        self.logger.debug(f"_clean_text result: length={len(final_text)}, preview={final_text[:100]}...")
        return final_text
    
    def _ensure_text_length(self, text: str, max_length: int = 4096) -> str:
        """Ensure text doesn't exceed max_length, truncating if needed"""
        if len(text) <= max_length:
            return text
        
        # Try to truncate at sentence boundary
        truncated = text[:max_length]
        last_period = truncated.rfind('.')
        last_exclamation = truncated.rfind('!')
        last_question = truncated.rfind('?')
        last_break = max(last_period, last_exclamation, last_question)
        
        if last_break >= 0 and last_break > max_length * 0.8:
            return truncated[:last_break + 1]
        else:
            return truncated.rstrip() + "..."
    
    def _split_text_into_chunks(self, text: str, max_chunk_size: int = 4000) -> list[str]:
        """Split long text into chunks at sentence boundaries
        
        Args:
            text: Text to split
            max_chunk_size: Maximum size per chunk (default 4000 to leave buffer for API)
        
        Returns:
            List of text chunks, each <= max_chunk_size
        """
        original_length = len(text)
        self.logger.debug(f"Splitting text: original length={original_length}, max_chunk_size={max_chunk_size}")
        
        if len(text) <= max_chunk_size:
            self.logger.debug(f"Text fits in single chunk, no splitting needed")
            return [text]
        
        chunks = []
        remaining = text
        min_chunk_size = max_chunk_size * 0.5  # Don't create tiny chunks
        chunk_num = 1
        
        while len(remaining) > max_chunk_size:
            # Try to find a good sentence break
            chunk = remaining[:max_chunk_size]
            last_period = chunk.rfind('.')
            last_exclamation = chunk.rfind('!')
            last_question = chunk.rfind('?')
            last_newline = chunk.rfind('\n')
            last_break = max(last_period, last_exclamation, last_question, last_newline)
            
            # Use sentence break if found and keeps reasonable chunk size
            if last_break >= min_chunk_size:
                split_point = last_break + 1
                chunk_text = remaining[:split_point].strip()
                chunks.append(chunk_text)
                self.logger.debug(f"Chunk {chunk_num}: length={len(chunk_text)}, split at sentence boundary (pos {split_point})")
                remaining = remaining[split_point:].strip()
            else:
                # No good break found, split at max_chunk_size
                chunk_text = chunk.rstrip()
                chunks.append(chunk_text)
                self.logger.debug(f"Chunk {chunk_num}: length={len(chunk_text)}, hard split (no good boundary)")
                remaining = remaining[max_chunk_size:].strip()
            
            chunk_num += 1
        
        # Add remaining text
        if remaining:
            chunks.append(remaining)
            self.logger.debug(f"Final chunk {chunk_num}: length={len(remaining)}")
        
        self.logger.info(f"Split {original_length} chars into {len(chunks)} chunks: {[len(c) for c in chunks]}")
        return chunks

    # ============ TTS GENERATION ============
    def _cache_key(self, text: str, voice: str) -> str:
        """Generate cache key"""
        return str(hash(f"{voice}:{text}"))

    async def _generate_tts(self, text: str, voice: str = None) -> Optional[bytes]:
        """Generate TTS audio"""
        if not await self.circuit_breaker.can_attempt():
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
        
        # Log what we're sending to API (first 200 chars for debugging)
        self.logger.debug(f"Sending to TTS API: length={len(text)}, preview={text[:200]}...")

        try:
            tts_timeout = getattr(self.bot.config, 'TTS_TIMEOUT', 15)
            session = await self.bot.http_mgr.get_session(timeout=tts_timeout)

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

    # ============ AUDIO PLAYBACK ============
    async def _play_audio(self, vc: disnake.VoiceClient, audio_data: bytes) -> bool:
        """Play audio through voice client"""
        temp_file = None

        try:
            if not vc.is_connected():
                return False

            # Wait for current audio to finish
            if vc.is_playing():
                for _ in range(50):
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
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except Exception:
                        pass

            if not vc.is_connected():
                if temp_file and os.path.exists(temp_file):
                    os.unlink(temp_file)
                return False

            vc.play(audio, after=after)

            # Wait for playback to start
            for _ in range(30):
                if vc.is_playing():
                    break
                await asyncio.sleep(0.1)
            else:
                vc.stop()
                if temp_file and os.path.exists(temp_file):
                    os.unlink(temp_file)
                return False

            # Wait for playback to complete
            try:
                # Increase timeout for long messages
                # Estimate: MP3 at 128kbps ≈ 1MB per minute, so bytes/17500 ≈ seconds
                # But use a more conservative estimate: 60s base + 1s per 100KB
                estimated_duration = 60 + (len(audio_data) / 100000)
                timeout = min(300, estimated_duration)  # Max 5 minutes
                self.logger.debug(f"Waiting for playback completion, timeout={timeout:.1f}s (audio_size={len(audio_data)} bytes)")
                await asyncio.wait_for(play_done.wait(), timeout=timeout)
                self.logger.debug("Playback completed successfully")
                return True
            except asyncio.TimeoutError:
                self.logger.warning(f"Playback timeout after {timeout:.1f}s, stopping")
                vc.stop()
                return False

        except Exception as e:
            self.logger.error(f"Playback error: {e}", exc_info=True)
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except Exception:
                    pass
            return False

    # ============ VOICE CONNECTION ============
    async def _connect_to_voice(self, channel: disnake.VoiceChannel, timeout: int = 10) -> Optional[disnake.VoiceClient]:
        """Connect to voice channel with retry"""
        guild = channel.guild

        # Cleanup existing connection if needed
        if guild.voice_client:
            if guild.voice_client.is_connected():
                if guild.voice_client.channel.id == channel.id:
                    return guild.voice_client
                await guild.voice_client.disconnect()
            else:
                try:
                    guild.voice_client.cleanup()
                except Exception:
                    pass
                await asyncio.sleep(0.3)

        # Connect with retry
        try:
            for attempt in range(3):
                try:
                    vc = await asyncio.wait_for(
                        channel.connect(timeout=timeout, reconnect=False),
                        timeout=timeout + 5
                    )
                    self.logger.info(f"Connected to {channel.name}")
                    
                    # Self-deafen
                    try:
                        await guild.change_voice_state(channel=channel, self_deaf=True)
                    except Exception:
                        pass
                    
                    return vc
                except disnake.ClientException as e:
                    if "already connected" in str(e).lower():
                        if guild.voice_client and guild.voice_client.is_connected():
                            if guild.voice_client.channel.id == channel.id:
                                return guild.voice_client
                            await guild.voice_client.disconnect(force=True)
                            await asyncio.sleep(0.5)
                    if attempt < 2:
                        await asyncio.sleep(0.8)
                        continue
                    raise
                except Exception as e:
                    if attempt < 2:
                        await asyncio.sleep(0.8)
                        continue
                    raise
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            return None

    # ============ STATE MANAGEMENT ============
    async def _get_or_create_state(self, guild_id: int) -> GuildVoiceState:
        """Get or create guild voice state"""
        async with self._state_lock:
            if guild_id not in self.guild_states:
                self.guild_states[guild_id] = GuildVoiceState(
                    guild_id, self.logger, self.max_queue_size
                )
            return self.guild_states[guild_id]

    async def _remove_state(self, guild_id: int):
        """Remove guild state"""
        async with self._state_lock:
            if guild_id in self.guild_states:
                state = self.guild_states[guild_id]
                await state.stop()
                del self.guild_states[guild_id]

    # ============ QUEUE PROCESSING ============
    async def _process_queue(self, guild_id: int):
        """Process TTS queue for a guild"""
        state = await self._get_or_create_state(guild_id)
        guild = self.bot.get_guild(guild_id)

        if not guild:
            state.is_processing = False
            return

        state.is_processing = True

        try:
            while not self._shutdown.is_set():
                try:
                    item = await asyncio.wait_for(state.queue.get(), timeout=300)
                    
                    if self._shutdown.is_set():
                        break
                    
                    state.mark_active()
                    self.logger.debug(f"Processing TTS item: text_length={len(item.text)} chars, voice={item.voice}")

                    if item.is_expired():
                        state.stats["dropped"] += 1
                        self.logger.debug("TTS item expired, dropping")
                        continue

                    member = guild.get_member(item.user_id)
                    if not member or not member.voice or not member.voice.channel:
                        state.stats["dropped"] += 1
                        self.logger.debug("Member not in voice, dropping")
                        continue

                    channel = member.voice.channel

                    # Generate TTS if not already generated
                    if not item.audio_data:
                        self.logger.debug(f"Generating TTS for {len(item.text)} chars")
                        item.audio_data = await self._generate_tts(item.text, item.voice)
                        if not item.audio_data:
                            state.stats["errors"] += 1
                            self.logger.warning("TTS generation failed")
                            continue
                        self.logger.debug(f"TTS generated: {len(item.audio_data)} bytes")

                    # Connect to voice
                    vc = await self._connect_to_voice(channel)
                    if not vc:
                        state.stats["errors"] += 1
                        self.logger.warning("Failed to connect to voice")
                        continue

                    # Play audio
                    self.logger.debug(f"Playing audio: {len(item.audio_data)} bytes")
                    if await self._play_audio(vc, item.audio_data):
                        state.stats["processed"] += 1
                        self.logger.debug("Audio playback completed successfully")
                    else:
                        state.stats["errors"] += 1
                        self.logger.warning("Audio playback failed")

                except asyncio.TimeoutError:
                    break
                except Exception as e:
                    self.logger.error(f"Queue processing error: {e}", exc_info=True)
                    state.stats["errors"] += 1
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass
        finally:
            state.is_processing = False

            # Disconnect if no humans
            if guild.voice_client and guild.voice_client.is_connected():
                try:
                    channel = guild.voice_client.channel
                    if channel:
                        humans = [m for m in channel.members if not m.bot]
                        if not humans:
                            await guild.voice_client.disconnect()
                except Exception:
                    pass

            await self._remove_state(guild_id)

    # ============ MESSAGE PROCESSING ============
    async def _should_process_message(self, message: disnake.Message) -> bool:
        """Check if message should be processed"""
        if not self.enabled or message.author.bot or not message.guild:
            return False

        # Check duplicate
        message_key = f"{message.id}:{message.author.id}:{message.content[:50]}"
        async with self._processed_messages_lock:
            if message_key in self._processed_messages:
                return False
            self._processed_messages.add(message_key)

        # Check channel - ensure both are int for proper comparison
        if self.allowed_channel is not None:
            # Get message channel ID (should already be int, but ensure it)
            msg_channel_id = int(message.channel.id)
            
            # Compare directly - both should be int at this point
            if msg_channel_id != self.allowed_channel:
                self.logger.debug(
                    f"Channel check failed: message channel {msg_channel_id} != allowed {self.allowed_channel}"
                )
                return False
            # Channel matches - continue processing

        # Check voice
        if not message.author.voice or not message.author.voice.channel:
            return False

        # Check role
        if self.tts_role_id:
            if not any(role.id == self.tts_role_id for role in message.author.roles):
                return False

        return True

    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        """Handle incoming messages for TTS"""
        if not await self._should_process_message(message):
            return

        # Check rate limit
        if not await self.rate_limiter.check(str(message.author.id)):
            return

        # Name announcement (first message per session)
        guild_id = message.guild.id
        user_id = message.author.id
        
        async with self._announcement_lock:
            if guild_id not in self._announced_users:
                self._announced_users[guild_id] = set()
            
            is_first_message = user_id not in self._announced_users[guild_id]
            if is_first_message:
                self._announced_users[guild_id].add(user_id)

        # Log original message length
        original_content_length = len(message.content)
        self.logger.debug(f"Processing message from {message.author.display_name}: original length={original_content_length} chars")
        
        # Clean text - reserve space for name prefix if first message
        if is_first_message:
            display_name = message.author.display_name
            if self._detect_needs_pronunciation_help(display_name):
                pronounceable_name = await self._improve_pronunciation(display_name)
            else:
                pronounceable_name = display_name
            # Calculate actual prefix length
            prefix = f"{pronounceable_name} says: "
            prefix_len = len(prefix)
            cleaned_text = await self._clean_text(message.content, max_length=None)  # Don't truncate yet
            if not cleaned_text or len(cleaned_text) < 2:
                self.logger.debug("Cleaned text too short, skipping")
                return
            # Add prefix
            text = prefix + cleaned_text
            self.logger.debug(f"First message: added prefix '{prefix}' (len={prefix_len}), total length={len(text)}")
        else:
            text = await self._clean_text(message.content, max_length=None)  # Don't truncate yet
            if not text or len(text) < 2:
                self.logger.debug("Cleaned text too short, skipping")
                return
            self.logger.debug(f"Cleaned text length={len(text)}")

        # Get voice
        user_voice = await self._get_voice_for_user(message.author)

        # Split into chunks if needed (4000 chars per chunk to leave buffer)
        text_chunks = self._split_text_into_chunks(text, max_chunk_size=4000)
        
        # Ensure each chunk doesn't exceed 4096 (safety check)
        for i, chunk in enumerate(text_chunks):
            if len(chunk) > 4096:
                old_len = len(chunk)
                text_chunks[i] = self._ensure_text_length(chunk, max_length=4096)
                self.logger.warning(f"Chunk {i+1} exceeded 4096 chars ({old_len}), truncated to {len(text_chunks[i])}")

        # Queue all chunks sequentially
        state = await self._get_or_create_state(guild_id)
        chunks_queued = 0
        for i, chunk in enumerate(text_chunks):
            if not chunk or len(chunk) < 2:
                self.logger.debug(f"Skipping empty chunk {i+1}")
                continue
            item = TTSQueueItem(
                user_id=message.author.id,
                channel_id=message.author.voice.channel.id,
                text=chunk,
                voice=user_voice,
                timestamp=time.time()
            )
            try:
                state.queue.put_nowait(item)
                chunks_queued += 1
                self.logger.debug(f"Queued chunk {i+1}/{len(text_chunks)}: length={len(chunk)} chars")
            except asyncio.QueueFull:
                state.stats["dropped"] += 1
                self.logger.warning(f"Queue full, dropping TTS chunk {i+1} for user {message.author.id}")
                break

        self.logger.info(f"Message processing complete: {original_content_length} chars → {len(text_chunks)} chunks → {chunks_queued} queued")

        # Start processor if not already running and we queued at least one chunk
        if chunks_queued > 0:
            async with self._state_lock:
                if not state.is_processing:
                    state.is_processing = True
                    state.processor_task = asyncio.create_task(
                        self._process_queue(guild_id)
                    )
                    self.logger.debug("Started queue processor")

    # ============ VOICE STATE UPDATES ============
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice state changes"""
        if member.bot:
            return

        guild = member.guild
        
        # User left voice channel
        if before.channel and not after.channel:
            # Clear announcement status
            async with self._announcement_lock:
                if guild.id in self._announced_users:
                    self._announced_users[guild.id].discard(member.id)
            
            # Free voice assignment
            async with self._voice_lock:
                self._voice_assignments.pop(str(member.id), None)
            
            # Check if should disconnect
            if guild.voice_client and guild.voice_client.is_connected():
                channel = guild.voice_client.channel
                if channel:
                    humans = [m for m in channel.members if not m.bot]
                    if not humans and not guild.voice_client.is_playing():
                        await asyncio.sleep(3)
                        if guild.voice_client.is_connected():
                            final_humans = [m for m in channel.members if not m.bot] if channel else []
                            if not final_humans and not guild.voice_client.is_playing():
                                await guild.voice_client.disconnect()
                                await self._remove_state(guild.id)

    # ============ CLEANUP ============
    async def _cleanup_loop(self):
        """Periodic cleanup"""
        try:
            while not self._shutdown.is_set():
                await asyncio.sleep(300)  # Every 5 minutes

                # Cleanup cache
                if hasattr(self.cache, 'cleanup'):
                    await self.cache.cleanup()

                # Cleanup message deduplication
                async with self._processed_messages_lock:
                    if len(self._processed_messages) > 1000:
                        message_list = list(self._processed_messages)
                        self._processed_messages = set(message_list[-500:])

                # Cleanup voice assignments for users not in VC
                async with self._voice_lock:
                    user_ids = list(self._voice_assignments.keys())
                
                users_to_remove = []
                for user_id_str in user_ids:
                    user_id = int(user_id_str)
                    user_in_voice = False
                    for guild in self.bot.guilds:
                        member = guild.get_member(user_id)
                        if member and member.voice and member.voice.channel:
                            user_in_voice = True
                            break
                    if not user_in_voice:
                        users_to_remove.append(user_id_str)
                
                async with self._voice_lock:
                    for user_id_str in users_to_remove:
                        self._voice_assignments.pop(user_id_str, None)

                # Cleanup idle states
                async with self._state_lock:
                    idle_guilds = [
                        gid for gid, state in self.guild_states.items()
                        if state.is_idle()
                    ]

                for guild_id in idle_guilds:
                    guild = self.bot.get_guild(guild_id)
                    if guild and guild.voice_client:
                        try:
                            await guild.voice_client.disconnect()
                        except Exception:
                            pass
                    await self._remove_state(guild_id)

                # Health check - restart stuck processors
                async with self._state_lock:
                    for guild_id, state in list(self.guild_states.items()):
                        if state.processor_task and state.processor_task.done():
                            if state.queue.qsize() > 0:
                                self.logger.info(f"Restarting processor for guild {guild_id}")
                                state.processor_task = asyncio.create_task(
                                    self._process_queue(guild_id)
                                )
                        elif state.queue.qsize() > 0 and not state.is_processing:
                            if not state.processor_task or state.processor_task.done():
                                self.logger.warning(f"Queue stuck for guild {guild_id}, restarting")
                                state.processor_task = asyncio.create_task(
                                    self._process_queue(guild_id)
                                )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Cleanup loop error: {e}", exc_info=True)

    # ============ COG LIFECYCLE ============
    async def cog_load(self):
        """Initialize cog"""
        if not self.enabled:
            return

        # Cleanup stale connections
        for vc in list(self.bot.voice_clients):
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass

        # Cleanup orphaned states
        async with self._state_lock:
            orphaned = [
                gid for gid in self.guild_states
                if not self.bot.get_guild(gid) or not self.bot.get_guild(gid).voice_client
            ]
            for gid in orphaned:
                await self._remove_state(gid)

        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self.logger.info("Voice cog loaded")

    def cog_unload(self):
        """Cleanup cog"""
        if not self.enabled or self._unloaded:
            return
        
        self._unloaded = True
        self.logger.info("Unloading voice cog...")
        
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

            if self._cleanup_task:
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task
                except asyncio.CancelledError:
                    pass

            # Stop all processors
            async with self._state_lock:
                for state in self.guild_states.values():
                    await state.stop()
                self.guild_states.clear()

            # Disconnect all voice clients
            for vc in list(self.bot.voice_clients):
                try:
                    if vc.is_playing():
                        vc.stop()
                    await asyncio.wait_for(vc.disconnect(force=True), timeout=5.0)
                except Exception:
                    try:
                        vc.cleanup()
                    except Exception:
                        pass

            self.logger.info("Voice cog unloaded")
        except Exception as e:
            self.logger.error(f"Async unload error: {e}")

    # ============ COMMANDS ============
    @commands.slash_command(name="tts")
    async def tts_cmd(self, inter: disnake.ApplicationCommandInteraction):
        """TTS commands"""
        pass

    def _create_progress_bar(self, percentage: float, length: int = 10) -> str:
        """Create progress bar"""
        filled = int(percentage / 10)
        return "▓" * filled + "░" * (length - filled)

    @tts_cmd.sub_command(name="stats", description="View TTS statistics")
    async def tts_stats(self, inter: disnake.ApplicationCommandInteraction):
        """Show TTS stats"""
        await inter.response.defer(ephemeral=True)

        if not self.enabled:
            embed = disnake.Embed(
                title="❌ TTS Disabled",
                description="Text-to-Speech is currently disabled.",
                color=disnake.Color.red()
            )
            await inter.edit_original_response(embed=embed)
            return

        cache_stats = await self.cache.get_stats()
        breaker_stats = await self.circuit_breaker.get_metrics()

        total_attempts = self.total_requests + self.total_failed
        success_rate = (self.total_requests / max(1, total_attempts)) * 100

        embed = disnake.Embed(
            title="🎤 TTS Performance Dashboard",
            description="Real-time statistics",
            color=disnake.Color.green() if success_rate > 90 else disnake.Color.yellow()
        )

        embed.add_field(
            name="🚀 API Performance",
            value=f"📊 **Requests:** `{self.total_requests:,}`\n"
                  f"✅ **Success Rate:** `{success_rate:.1f}%`\n"
                  f"❌ **Failed:** `{self.total_failed:,}`",
            inline=True
        )

        embed.add_field(
            name="⚡ Cache Performance",
            value=f"💾 **Size:** `{cache_stats['size']}/{cache_stats['max_size']}`\n"
                  f"🎯 **Hit Rate:** `{cache_stats['hit_rate']:.1f}%`\n"
                  f"⚡ **Cached:** `{self.total_cached:,}`",
            inline=True
        )

        breaker_emoji = {"CLOSED": "✅", "OPEN": "🚨", "HALF_OPEN": "⚠️"}.get(breaker_stats['state'], "❓")
        embed.add_field(
            name="🏥 System Health",
            value=f"{breaker_emoji} **Status:** `{breaker_stats['state']}`\n"
                  f"⚠️ **Failures:** `{breaker_stats['current_failures']}/5`\n"
                  f"📈 **Uptime:** `{breaker_stats['uptime_percentage']:.1f}%`",
            inline=True
        )

        active_guilds = len([s for s in self.guild_states.values() if time.time() - s.last_activity < 600])
        processing_guilds = sum(1 for s in self.guild_states.values() if s.is_processing)
        
        embed.add_field(
            name="🌐 Activity Status",
            value=f"🏠 **Active Guilds:** `{active_guilds}`\n"
                  f"📊 **Total Guilds:** `{len(self.guild_states)}`\n"
                  f"🔄 **Processing:** `{processing_guilds}`",
            inline=True
        )

        cache_bar = self._create_progress_bar(cache_stats['hit_rate'])
        success_bar = self._create_progress_bar(success_rate)
        
        embed.add_field(
            name="📊 Visual Metrics",
            value=f"**Cache Hit Rate:** `{cache_bar}` {cache_stats['hit_rate']:.1f}%\n"
                  f"**Success Rate:** `{success_bar}` {success_rate:.1f}%",
            inline=False
        )

        embed.set_footer(text="🎵 TTS powered by OpenAI")
        embed.timestamp = disnake.utils.utcnow()

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

        embed = disnake.Embed(title="🎵 Voice Channel Status", color=disnake.Color.blue())
        
        embed.add_field(name="Channel", value=f"**{channel.name}**\nID: {channel.id}", inline=True)
        embed.add_field(name="Members", value=f"👥 Humans: {len(humans)}\n🤖 Bots: {len(bots)}", inline=True)
        embed.add_field(
            name="Status",
            value=f"🔊 Playing: {'Yes' if vc.is_playing() else 'No'}\n📋 Queue: {queue_size}\n⚙️ Processing: {'Yes' if is_processing else 'No'}",
            inline=True
        )
        
        if humans:
            human_names = [m.display_name for m in humans[:5]]
            if len(humans) > 5:
                human_names.append(f"... and {len(humans) - 5} more")
            embed.add_field(name="👥 Humans in Channel", value="\n".join(human_names), inline=False)

        await inter.edit_original_response(embed=embed)


def setup(bot):
    """Setup the cog"""
    bot.add_cog(VoiceProcessingCog(bot))
