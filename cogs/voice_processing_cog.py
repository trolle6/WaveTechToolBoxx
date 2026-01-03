"""
Voice Processing Cog - Text-to-Speech with Smart Features

FEATURES:
- 🎤 Automatic TTS for messages from users in voice channels
- 🎭 Session-based voice assignment (13 voices, assigned per-guild session)
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

DESIGN DECISIONS:
- Unlimited message length: Messages are split at sentence boundaries to handle any length
- Opus format: Used instead of MP3 for better compression and Discord-native support
- Dynamic timeouts: API and playback timeouts scale with text/audio length
- Sequential processing: Chunks are processed one at a time for reliability (not parallel)
- Session-based voice assignment: Voices assigned per-guild session, cleared when user leaves voice channel
"""

import asyncio
import os
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, Optional, Set

import aiohttp
import disnake
from disnake.ext import commands

from . import utils


# ============ CONSTANTS ============
# These constants define API limits and configuration values to make the code self-documenting

# OpenAI TTS API Limits
OPENAI_TTS_MAX_CHARS_PER_REQUEST = 4096  # Maximum characters per TTS API request
TTS_CHUNK_SIZE = 4000  # Characters per chunk when splitting (leaves buffer for API limit)

# Timeout Configuration (in seconds)
TTS_API_TIMEOUT_BASE = 60  # Base timeout for TTS API requests
TTS_API_TIMEOUT_PER_100_CHARS = 0.15  # Additional seconds per 100 characters
TTS_API_TIMEOUT_MAX = 180  # Maximum timeout (3 minutes)

# Audio Playback Configuration
OPUS_BYTES_PER_SECOND = 8000  # Opus at 64kbps ≈ 8000 bytes/second
AUDIO_PLAYBACK_TIMEOUT_BASE = 120  # Base timeout (2 minutes)
AUDIO_PLAYBACK_TIMEOUT_MULTIPLIER = 2.0  # Multiplier for estimated duration
AUDIO_PLAYBACK_TIMEOUT_BUFFER = 30  # Additional buffer seconds
AUDIO_PLAYBACK_TIMEOUT_MAX = 600  # Maximum timeout (10 minutes)

# Text Processing Configuration
PRONUNCIATION_IMPROVEMENT_MAX_CHARS = 3500  # Skip pronunciation improvement for longer texts (will be split anyway)
SENTENCE_BOUNDARY_MIN_PERCENT = 0.8  # Minimum 80% of text must be kept when truncating at sentence boundary

# Cache Configuration (in seconds)
CACHE_TTL_AUDIO = 3600  # 1 hour - audio cache TTL
CACHE_TTL_PRONUNCIATION = 7200  # 2 hours - pronunciation improvement cache TTL

# Queue and State Configuration
QUEUE_PROCESSOR_TIMEOUT = 300  # 5 minutes - timeout for queue processor wait
GUILD_IDLE_TIMEOUT = 600  # 10 minutes - guild considered idle after this time
MESSAGE_EXPIRY_TIME = 60  # 1 minute - TTS items expire after this time

# Circuit Breaker Configuration
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5  # Open circuit after this many failures
CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 60  # Try recovery after this many seconds
CIRCUIT_BREAKER_SUCCESS_THRESHOLD = 2  # Close circuit after this many successes

# Audio Processing
AUDIO_VOLUME_MULTIPLIER = 0.6  # Reduce volume to 60% for voice channel playback


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
    """
    Manages voice processing state for a single Discord guild.
    
    Each guild has its own queue and processor to allow parallel processing
    across multiple servers without blocking.
    
    Attributes:
        guild_id: Discord guild ID this state belongs to
        logger: Logger instance for this guild's operations
        queue: Async queue for TTS items (FIFO processing)
        processor_task: Background task that processes the queue
        is_processing: Flag indicating if queue is currently being processed
        last_activity: Timestamp of last queue activity (for cleanup)
        stats: Statistics dictionary tracking processed/dropped/error counts
    """
    
    def __init__(self, guild_id: int, logger, max_queue_size: int = 20):
        self.guild_id = guild_id
        self.logger = logger
        self.queue = asyncio.Queue(maxsize=max_queue_size)
        self.processor_task: Optional[asyncio.Task] = None
        self.is_processing = False
        self.last_activity = time.time()
        self.stats = {"processed": 0, "dropped": 0, "errors": 0}

    def mark_active(self):
        """Update last activity timestamp to prevent idle cleanup."""
        self.last_activity = time.time()

    def is_idle(self, timeout: int = GUILD_IDLE_TIMEOUT) -> bool:
        """
        Check if this guild state has been idle for too long.
        
        Args:
            timeout: Seconds of inactivity before considered idle
            
        Returns:
            True if last activity was more than timeout seconds ago
        """
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
        # Discord cleanup patterns
        # Emoji pattern: matches rendered custom emojis like <:saul-1:123456> or <a:animated:123456>
        # We'll extract the emoji name and replace with it (e.g., "saul-1")
        # Using [\w-]+ to match emoji names with hyphens, underscores, and numbers
        self._emoji_pattern = re.compile(r'<(a?):([\w-]+):\d+>')
        # Other Discord formatting: mentions, channels, URLs (remove completely)
        self._discord_cleanup_pattern = re.compile(
            r'<@!?\d+>|<@&\d+>|<#\d+>|https?://\S+'
        )

        # Initialize components
        rate_limit = getattr(bot.config, 'RATE_LIMIT_REQUESTS', 15)
        rate_window = getattr(bot.config, 'RATE_LIMIT_WINDOW', 60)
        max_cache = getattr(bot.config, 'MAX_TTS_CACHE', 100)
        
        self.rate_limiter = utils.RateLimiter(limit=rate_limit, window=rate_window)
        self.circuit_breaker = utils.CircuitBreaker(
            failure_threshold=CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            recovery_timeout=CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
            success_threshold=CIRCUIT_BREAKER_SUCCESS_THRESHOLD
        )
        self.cache = utils.LRUCache[bytes](max_size=max_cache, ttl=CACHE_TTL_AUDIO)
        self.pronunciation_cache = utils.LRUCache[str](max_size=200, ttl=CACHE_TTL_PRONUNCIATION)

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
        # All available OpenAI TTS voices (13 total)
        self.available_voices = [
            "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", 
            "onyx", "sage", "shimmer", "verse", "marin", "cedar"
        ]
        
        # Voice assignments (per-guild, session-based - cleared when user leaves voice)
        self._voice_assignments: Dict[int, Dict[int, str]] = {}  # guild_id -> {user_id: voice}
        self._voice_lock = asyncio.Lock()
        
        # TTS role requirement (optional)
        tts_role_id = getattr(bot.config, 'TTS_ROLE_ID', None)
        self.tts_role_id = None
        if tts_role_id:
            try:
                self.tts_role_id = int(str(tts_role_id).strip())
                self.logger.info(f"TTS role requirement enabled: {self.tts_role_id}")
            except (ValueError, TypeError) as e:
                self.logger.error(f"Failed to convert TTS_ROLE_ID to int: {tts_role_id} - {e}")

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

    # ============ VOICE ASSIGNMENT ============
    async def _get_voice_for_user(self, member: disnake.Member) -> str:
        """
        Get or assign voice for user (session-based, per-guild).
        
        DESIGN DECISIONS:
        - Session-based: Voice assignments are per-guild and cleared when user leaves voice channel
        - Deterministic: Uses user_id hash to consistently assign same voice within a session
        - All voices: Uses all 13 available voices for better variety
        - No pronoun detection: Simplified to just hash-based assignment for consistency
        
        Args:
            member: Discord member to get voice for
            
        Returns:
            Voice name to use for TTS
        """
        if not member.guild:
            return self.default_voice
        
        guild_id = member.guild.id
        user_id = member.id
        
        # Check role requirement
        if self.tts_role_id:
            if not any(role.id == self.tts_role_id for role in member.roles):
                return self.default_voice
        
        # Get or assign voice for this user in this guild (session-based)
        async with self._voice_lock:
            # Initialize guild dict if needed
            if guild_id not in self._voice_assignments:
                self._voice_assignments[guild_id] = {}
            
            guild_assignments = self._voice_assignments[guild_id]
            
            # Return existing assignment if present
            if user_id in guild_assignments:
                voice = guild_assignments[user_id]
                if voice in self.available_voices:
                    return voice
            
            # Assign new voice for this session (deterministic based on user_id)
            # Use modulo to distribute across all available voices
            voice_index = user_id % len(self.available_voices)
            new_voice = self.available_voices[voice_index]
            guild_assignments[user_id] = new_voice
            return new_voice

    # ============ API HELPERS ============
    def _get_openai_headers(self) -> Dict[str, str]:
        """
        Get common OpenAI API headers for HTTP requests.
        
        Returns:
            Dictionary with Authorization and Content-Type headers
        """
        return {
            "Authorization": f"Bearer {self.bot.config.OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

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
        # Combined pattern for efficiency: acronyms, mixed case, alphanumeric
        pattern = re.compile(
            r'\b[A-Z]{2,4}\b|'  # Acronyms (2-4 uppercase letters)
            r'\b[a-z]+[A-Z]+[a-z]*\b|\b[A-Z]+[a-z]+[A-Z]+\b|'  # Mixed case
            r'\b[A-Za-z]+\d+\b|\b\d+[A-Za-z]+\b'  # Alphanumeric
        )
        return bool(pattern.search(text))

    async def _improve_pronunciation(self, text: str) -> str:
        """Use AI to improve pronunciation"""
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
            
            headers = self._get_openai_headers()
            
            # Calculate max_tokens: estimate ~4 chars per token, add 50% buffer
            # Cap at reasonable limit (2000 tokens ≈ 8000 chars output)
            estimated_tokens = int(len(text) / 4 * 1.5)
            max_tokens = min(2000, max(200, estimated_tokens))
            
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.1
            }
            
            self.logger.debug(f"Pronunciation improvement API: input_length={len(text)}, max_tokens={max_tokens}")
            
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

    def _truncate_at_sentence_boundary(self, text: str, max_length: int) -> str:
        """
        Truncate text at sentence boundary if possible.
        
        This provides natural breaks in speech when text must be truncated,
        avoiding mid-sentence cuts that sound unnatural in TTS.
        
        Args:
            text: Text to truncate
            max_length: Maximum allowed length
            
        Returns:
            Truncated text ending at sentence boundary if possible, otherwise truncated with "..."
        """
        truncated = text[:max_length]
        last_period = truncated.rfind('.')
        last_exclamation = truncated.rfind('!')
        last_question = truncated.rfind('?')
        last_break = max(last_period, last_exclamation, last_question)
        
        # Only use sentence break if it keeps at least 80% of text (avoids tiny fragments)
        if last_break >= 0 and last_break > max_length * SENTENCE_BOUNDARY_MIN_PERCENT:
            return truncated[:last_break + 1]
        else:
            return truncated.rstrip() + "..."

    def _extract_emoji_name(self, match: re.Match) -> str:
        """
        Extract emoji name from rendered Discord emoji format.
        
        Converts <:saul-1:123456> or <a:animated:123456> to "saul-1" or "animated".
        The name is what the user typed, and we want TTS to speak it.
        
        Args:
            match: Regex match object from _emoji_pattern
        
        Returns:
            Emoji name (e.g., "saul-1")
        """
        emoji_name = match.group(2)  # Group 2 is the emoji name
        return emoji_name

    async def _clean_text(self, text: str, max_length: Optional[int] = None) -> str:
        """
        Clean and process text for TTS.
        
        Converts rendered Discord emojis (like <:saul-1:123456>) to their names (like "saul-1")
        so TTS can speak them. Removes other Discord formatting (mentions, URLs, etc.).
        
        Args:
            text: Text to clean
            max_length: Optional max length (None = no truncation, used for splitting)
        
        OpenAI TTS API supports up to 4096 characters per request.
        """
        original_length = len(text)
        # Normalize excessive formatting: multiple newlines, dashes, etc.
        text = re.sub(r'-{3,}', ' ', text)  # Replace 3+ dashes with space
        text = re.sub(r'\n{3,}', '\n\n', text)  # Replace 3+ newlines with 2
        
        # Convert rendered emojis to their names (e.g., <:saul-1:123456> → "saul-1")
        # This preserves the emoji name so TTS can speak it
        text = self._emoji_pattern.sub(self._extract_emoji_name, text)
        after_emoji_extraction = len(text)
        
        # Remove other Discord formatting (mentions, channels, URLs)
        text = self._discord_cleanup_pattern.sub('', text)
        after_discord_cleanup = len(text)
        
        # Normalize whitespace after Discord cleanup
        text = re.sub(r'\s+', ' ', text.strip())  # Normalize all whitespace to single spaces
        after_whitespace = len(text)
        
        text = self._apply_corrections(text)
        after_corrections = len(text)
        
        self.logger.debug(
            f"Text cleaning: {original_length} → {after_emoji_extraction} (emoji extraction) → "
            f"{after_discord_cleanup} (discord) → {after_whitespace} (whitespace) → {after_corrections} (corrections)"
        )

        if self._detect_needs_pronunciation_help(text):
            # Skip pronunciation improvement for very long texts (will be split anyway)
            # Only improve pronunciation for texts that won't be split (< 3500 chars to leave buffer)
            if len(text) < 3500:
                before_pronunciation = len(text)
                text = await self._improve_pronunciation(text)
                after_pronunciation = len(text)
                if before_pronunciation != after_pronunciation:
                    self.logger.debug(f"Pronunciation improvement: {before_pronunciation} → {after_pronunciation} chars")

        # Truncate if max_length specified and exceeds limit
        if max_length and len(text) > max_length:
            text = self._truncate_at_sentence_boundary(text, max_length)

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
        return self._truncate_at_sentence_boundary(text, max_length)
    
    def _split_text_into_chunks(self, text: str, max_chunk_size: int = TTS_CHUNK_SIZE) -> list[str]:
        """
        Split long text into chunks at sentence boundaries for sequential TTS processing.
        
        This enables unlimited message length by splitting at natural breaks (sentences, newlines).
        Chunks are processed sequentially (not in parallel) for reliability and proper ordering.
        
        Design decision: Sequential processing ensures chunks play in order and handles errors gracefully.
        Parallel processing would require complex queue management and error handling.
        
        Args:
            text: Text to split (can be any length)
            max_chunk_size: Maximum characters per chunk (default: TTS_CHUNK_SIZE)
        
        Returns:
            List of text chunks, each <= max_chunk_size, split at sentence boundaries when possible
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

        headers = self._get_openai_headers()

        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice,
            "response_format": "opus",  # Opus: better compression, smaller files, Discord-native
            "speed": 1.0
        }
        
        # Log what we're sending to API (first 200 chars for debugging)
        self.logger.debug(f"Sending to TTS API: length={len(text)}, preview={text[:200]}...")

        try:
            # Dynamic timeout calculation: scales with text length to handle long messages
            # Base timeout + proportional time per 100 chars, clamped to reasonable bounds
            text_timeout = (len(text) / 100 * TTS_API_TIMEOUT_PER_100_CHARS) + TTS_API_TIMEOUT_BASE
            tts_timeout = max(TTS_API_TIMEOUT_BASE, min(TTS_API_TIMEOUT_MAX, text_timeout))
            self.logger.debug(f"TTS API timeout: {tts_timeout:.1f}s (text_length={len(text)}, calculated={text_timeout:.1f})")
            session = await self.bot.http_mgr.get_session()
            
            # Use request-level timeout to override session timeout
            request_timeout = aiohttp.ClientTimeout(total=tts_timeout)
            async with session.post(self.tts_url, json=payload, headers=headers, timeout=request_timeout) as resp:
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
        """
        Play audio through Discord voice client with proper cleanup and timeout handling.
        
        DESIGN DECISIONS:
        - Temp file: FFmpegOpusAudio requires file path (can't use bytes directly)
        - Callback cleanup: Automatically deletes temp file after playback completes
        - Dynamic timeout: Scales with audio length to handle long messages
        - Volume reduction: 60% volume prevents audio clipping in voice channels
        - Wait for completion: Uses asyncio.Event to properly wait for playback finish
        
        Args:
            vc: Discord voice client to play audio through
            audio_data: Audio bytes in Opus format to play
            
        Returns:
            True if playback completed successfully, False if failed or timed out
        """
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
            with tempfile.NamedTemporaryFile(suffix='.opus', delete=False) as f:
                f.write(audio_data)
                temp_file = f.name

            # Prepare audio source (Opus format - Discord-native, efficient)
            audio = disnake.FFmpegOpusAudio(
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
                # Calculate dynamic timeout based on audio length
                # Opus format: estimate duration from file size (8000 bytes/sec at 64kbps)
                # Use 2x multiplier + buffer to handle network/system delays
                estimated_duration = len(audio_data) / OPUS_BYTES_PER_SECOND
                timeout = max(
                    AUDIO_PLAYBACK_TIMEOUT_BASE,
                    min(
                        AUDIO_PLAYBACK_TIMEOUT_MAX,
                        estimated_duration * AUDIO_PLAYBACK_TIMEOUT_MULTIPLIER + AUDIO_PLAYBACK_TIMEOUT_BUFFER
                    )
                )
                self.logger.debug(f"Waiting for playback completion, timeout={timeout:.1f}s (audio_size={len(audio_data)} bytes, estimated_duration={estimated_duration:.1f}s)")
                await asyncio.wait_for(play_done.wait(), timeout=timeout)
                self.logger.debug("Playback completed successfully")
                return True
            except asyncio.TimeoutError:
                self.logger.warning(f"Playback timeout after {timeout:.1f}s (estimated {estimated_duration:.1f}s), stopping")
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
        """
        Process TTS queue with smart pipeline generation.
        
        DESIGN DECISIONS:
        - FIFO processing: Items are always processed and played in order
        - Hybrid generation: For long playback (>30s), generate next item during playback (pipeline).
          For short playback, process normally (efficiency priority).
        - Expiration prevention: Pipeline generation ensures next item is ready before current finishes,
          preventing expiration during long playback times.
        
        This balances efficiency (short messages process quickly) with reliability (long messages
        don't cause queue items to expire).
        """
        state = await self._get_or_create_state(guild_id)
        guild = self.bot.get_guild(guild_id)

        if not guild:
            state.is_processing = False
            return

        state.is_processing = True
        PIPELINE_THRESHOLD = 30.0  # Start pipeline generation if playback will be >30 seconds
        prepared_item = None  # Next item prepared by pipeline generation

        try:
            while not self._shutdown.is_set():
                try:
                    # Get next item (FIFO) - either from queue or from pipeline
                    if prepared_item:
                        item = prepared_item
                        prepared_item = None
                    else:
                        try:
                            item = await asyncio.wait_for(state.queue.get(), timeout=300)
                        except asyncio.TimeoutError:
                            break
                    
                    if self._shutdown.is_set():
                        break
                    
                    state.mark_active()
                    self.logger.debug(f"Processing TTS item: text_length={len(item.text)} chars, voice={item.voice}")

                    # Check expiration before generation
                    if item.is_expired():
                        state.stats["dropped"] += 1
                        self.logger.debug("TTS item expired, dropping")
                        continue

                    # Verify member is still in voice
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

                    # Estimate playback duration to decide on pipeline generation
                    estimated_duration = len(item.audio_data) / OPUS_BYTES_PER_SECOND
                    will_pipeline = estimated_duration > PIPELINE_THRESHOLD

                    # If playback will be long, start generating next item in background (pipeline)
                    next_gen_task = None
                    if will_pipeline:
                        try:
                            next_item = state.queue.get_nowait()
                            # Only pipeline if next item exists and isn't expired
                            if next_item.is_expired():
                                state.stats["dropped"] += 1
                                self.logger.debug("Pipeline: Next item expired, skipping pipeline")
                            else:
                                next_member = guild.get_member(next_item.user_id)
                                if not next_member or not next_member.voice or not next_member.voice.channel:
                                    state.stats["dropped"] += 1
                                    self.logger.debug("Pipeline: Next item member not in voice, skipping pipeline")
                                elif not next_item.audio_data:
                                    # Start generating next item in background
                                    async def generate_next():
                                        try:
                                            self.logger.debug(f"Pipeline: Generating next TTS for {len(next_item.text)} chars")
                                            next_item.audio_data = await self._generate_tts(next_item.text, next_item.voice)
                                            if next_item.audio_data:
                                                self.logger.debug(f"Pipeline: Next TTS generated: {len(next_item.audio_data)} bytes")
                                        except Exception as e:
                                            self.logger.error(f"Pipeline generation error: {e}", exc_info=True)
                                    
                                    next_gen_task = asyncio.create_task(generate_next())
                                    prepared_item = next_item  # Store for next iteration
                                    self.logger.debug(f"Pipeline: Started generating next item during playback (estimated {estimated_duration:.1f}s playback)")
                        except asyncio.QueueEmpty:
                            pass  # No next item, no pipeline needed

                    # Connect to voice
                    vc = await self._connect_to_voice(channel)
                    if not vc:
                        state.stats["errors"] += 1
                        self.logger.warning("Failed to connect to voice")
                        if next_gen_task:
                            next_gen_task.cancel()
                        prepared_item = None  # Clear prepared item on error
                        continue

                    # Play audio
                    self.logger.debug(f"Playing audio: {len(item.audio_data)} bytes")
                    playback_success = await self._play_audio(vc, item.audio_data)
                    
                    # Wait for pipeline generation to complete if it was started
                    if next_gen_task and not next_gen_task.done():
                        self.logger.debug("Pipeline: Waiting for next item generation to complete")
                        try:
                            await next_gen_task
                        except Exception as e:
                            self.logger.error(f"Pipeline generation error: {e}", exc_info=True)
                            prepared_item = None  # Clear on error
                    
                    if playback_success:
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
                    prepared_item = None  # Clear prepared item on error
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
            # Allow even very short messages (single character, emoji names like :saul-1:, etc.)
            # Only skip if completely empty after cleaning
            if not cleaned_text or len(cleaned_text.strip()) == 0:
                self.logger.debug("Cleaned text is empty, skipping")
                return
            # Add prefix
            text = prefix + cleaned_text
            self.logger.debug(f"First message: added prefix '{prefix}' (len={prefix_len}), total length={len(text)}")
        else:
            text = await self._clean_text(message.content, max_length=None)  # Don't truncate yet
            # Allow even very short messages (single character, emoji names like :saul-1:, etc.)
            # Only skip if completely empty after cleaning
            if not text or len(text.strip()) == 0:
                self.logger.debug("Cleaned text is empty, skipping")
                return
            self.logger.debug(f"Cleaned text length={len(text)}")

        # Get voice
        user_voice = await self._get_voice_for_user(message.author)

        # Split into chunks if needed (4000 chars per chunk to leave buffer)
        text_chunks = self._split_text_into_chunks(text, max_chunk_size=4000)
        
        # Safety check: Ensure each chunk doesn't exceed API limit
        for i, chunk in enumerate(text_chunks):
            if len(chunk) > OPENAI_TTS_MAX_CHARS_PER_REQUEST:
                old_len = len(chunk)
                text_chunks[i] = self._ensure_text_length(chunk, max_length=OPENAI_TTS_MAX_CHARS_PER_REQUEST)
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
            
            # Clear voice assignment for this session (user left voice channel)
            async with self._voice_lock:
                if guild.id in self._voice_assignments:
                    self._voice_assignments[guild.id].pop(member.id, None)
            
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
