"""
Voice Processing Cog - Text-to-Speech with Smart Features

FEATURES:
- 🎤 Automatic TTS for messages from users in voice channels
- 🎭 Session-based voice assignment (13 voices, assigned per-guild session)
- 🤖 AI pronunciation improvement for acronyms and usernames
- 📝 Smart grammar corrections (contractions, etc.)
- 👤 Name announcement (first message per session, 2-hour cooldown)
- ⚡ LRU caching for TTS audio and pronunciations
- 🔧 Circuit breaker for API failure protection
- 🚦 Rate limiting
- 🔄 API retry with exponential backoff (429, 5xx) and Retry-After support
- 🛡️ Defensive validation (voice, UTF-8, empty responses)
- 🔌 Robust voice connection with edge-case handling

COMMANDS:
- /tts stats - View performance metrics
- /tts disconnect - Force disconnect (admin)
- /tts clear - Clear TTS queue (admin)
- /tts status - Check voice channel status

DESIGN DECISIONS:
- Unlimited message length: Messages are split at sentence boundaries to handle any length
- MP3 from API + FFmpegPCMAudio + PCMVolumeTransformer: single decode, single encode (no Opus→Opus re-encode that caused slow/double-voice)
- Dynamic timeouts: API and playback timeouts scale with text/audio length
- Sequential processing: Chunks are processed one at a time for reliability (not parallel)
- Session-based voice assignment: Voices assigned per-guild session, cleared when user leaves voice channel
"""

import asyncio
import hashlib
import os
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

import aiohttp
import disnake
from disnake.ext import commands

from . import utils
from .secret_santa_checks import manage_guild_check


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
# MP3 from API; FFmpeg decodes to PCM, library encodes to Opus once (avoids Opus re-encode artifacts)
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

# Name Announcement Configuration
NAME_ANNOUNCEMENT_COOLDOWN = 7200  # 2 hours - cooldown before announcing username again

# Queue and State Configuration
QUEUE_PROCESSOR_TIMEOUT = 300  # 5 minutes - timeout for queue processor wait
GUILD_IDLE_TIMEOUT = 600  # 10 minutes - guild considered idle after this time
MESSAGE_EXPIRY_TIME = 60  # 1 minute - TTS items expire after this time

# Circuit Breaker Configuration
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5  # Open circuit after this many failures
CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 60  # Try recovery after this many seconds
CIRCUIT_BREAKER_SUCCESS_THRESHOLD = 2  # Close circuit after this many successes

# API Retry Configuration (professional: exponential backoff, Retry-After support)
TTS_API_RETRY_MAX_ATTEMPTS = 3  # Total attempts (1 initial + 2 retries)
TTS_API_RETRY_BASE_DELAY = 1.0  # Base delay in seconds
TTS_API_RETRY_MAX_DELAY = 30.0  # Cap delay (e.g. from Retry-After)
MIN_VALID_AUDIO_SIZE = 100  # Reject API responses smaller than this (likely errors)

# Audio Processing - MP3 + FFmpegPCMAudio + PCMVolumeTransformer (avoids Opus re-encode)
AUDIO_VOLUME_MULTIPLIER = 0.7  # 70% volume for clarity without clipping
TTS_SPEED = 1.0  # Natural speed (no artificial slowdown)
AUDIO_PLAYBACK_START_DELAY = 0.3  # Delay after creating audio source before starting playback
MP3_BYTES_PER_SECOND = 16000  # MP3 ~128kbps ≈ 16000 bytes/second (OpenAI TTS default)

# Playback wait configuration
AUDIO_FINISH_WAIT_MAX_ATTEMPTS = 50  # Maximum attempts to wait for current audio to finish (5 seconds total)
AUDIO_FINISH_WAIT_INTERVAL = 0.1  # Seconds between checks for audio finish
AUDIO_START_CHECK_MAX_ATTEMPTS = 30  # Maximum attempts to check if playback started (3 seconds total)
AUDIO_START_CHECK_INITIAL_DELAYS = 3  # Number of initial attempts with longer delay for initialization
VOICE_DISCONNECT_DELAY = 3.0  # Seconds to wait before checking voice channel again (avoids race conditions)
VOICE_CLEANUP_DELAY = 0.3  # Seconds to wait after cleanup before reconnecting
VOICE_CONNECTION_RETRY_DELAY = 0.8  # Seconds between connection retry attempts


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
        
        # Check FFmpeg availability and log diagnostics
        self._check_ffmpeg_availability()
        
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
        # Pronunciation detection pattern: pre-compile for efficiency
        self._pronunciation_pattern = re.compile(
            r'\b[A-Z]{2,4}\b|'  # Acronyms (2-4 uppercase letters)
            r'\b[a-z]+[A-Z]+[a-z]*\b|\b[A-Z]+[a-z]+[A-Z]+\b|'  # Mixed case
            r'\b[A-Za-z]+\d+\b|\b\d+[A-Za-z]+\b',  # Alphanumeric
            re.IGNORECASE
        )

        # Initialize components
        rate_limit = bot.config.RATE_LIMIT_REQUESTS
        rate_window = bot.config.RATE_LIMIT_WINDOW
        max_cache = bot.config.MAX_TTS_CACHE
        
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
        self.max_queue_size = bot.config.MAX_QUEUE_SIZE
        
        # Message deduplication
        self._processed_messages: Set[str] = set()
        self._processed_messages_lock = asyncio.Lock()
        
        # Name announcement tracking (guild_id -> {user_id: last_announcement_timestamp})
        self._announced_users: Dict[int, Dict[int, float]] = {}
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
        # Structure: guild_id -> {user_id: {"voice": voice_name, "timestamp": timestamp}}
        self._voice_assignments: Dict[int, Dict[int, Dict[str, Any]]] = {}
        self._voice_lock = asyncio.Lock()
        
        # TTS role requirement (optional)
        tts_role_id = bot.config.TTS_ROLE_ID
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

        # Convert channel ID to int (config loads as string from env)
        channel_id_raw = bot.config.DISCORD_CHANNEL_ID
        try:
            self.allowed_channel = int(channel_id_raw) if channel_id_raw else None
            if self.allowed_channel:
                self.logger.info(f"Allowed channel configured: {self.allowed_channel}")
            else:
                self.logger.warning("DISCORD_CHANNEL_ID not set - TTS will work in all channels")
        except (ValueError, TypeError) as e:
            self.logger.error(f"Failed to convert DISCORD_CHANNEL_ID to int: {repr(channel_id_raw)} - {e}")
            self.allowed_channel = None

    # ============ VOICE ASSIGNMENT ============
    async def _get_voice_for_user(self, member: disnake.Member) -> str:
        """
        Get or assign voice for user (session-based, per-guild).
        
        DESIGN DECISIONS:
        - Session-based: Voice assignments are per-guild and cleared when user leaves voice channel
        - Deterministic: Uses user_id hash to consistently assign same voice within a session
        - All voices: Uses all 13 available voices for better variety
        - Timestamp tracking: Tracks when voice was assigned to help debug issues and ensure persistence
        
        Args:
            member: Discord member to get voice for
            
        Returns:
            Voice name to use for TTS
        """
        if not member.guild:
            return self.default_voice
        
        guild_id = member.guild.id
        user_id = member.id
        
        # Check role requirement (guard None/empty roles)
        roles = getattr(member, "roles", None) or []
        if self.tts_role_id and not any(getattr(r, "id", None) == self.tts_role_id for r in roles):
            return self.default_voice
        
        # Get or assign voice for this user in this guild (session-based)
        async with self._voice_lock:
            # Initialize guild dict if needed
            if guild_id not in self._voice_assignments:
                self._voice_assignments[guild_id] = {}
            
            guild_assignments = self._voice_assignments[guild_id]
            current_time = time.time()
            
            # Check for existing assignment with proper validation
            if user_id in guild_assignments:
                assignment = guild_assignments[user_id]
                
                # Handle both old format (str) and new format (dict) for backward compatibility
                if isinstance(assignment, str):
                    voice = assignment
                    # Upgrade old format to new format
                    guild_assignments[user_id] = {
                        "voice": voice,
                        "timestamp": current_time
                    }
                    self.logger.info(f"Upgraded old voice assignment format for user {user_id} (display_name: {member.display_name}) in guild {guild_id}: '{voice}'")
                elif isinstance(assignment, dict):
                    voice = assignment.get("voice")
                    old_timestamp = assignment.get("timestamp", 0)
                    # Update timestamp to track activity
                    assignment["timestamp"] = current_time
                    self.logger.debug(f"Found existing voice assignment for user {user_id} (display_name: {member.display_name}): '{voice}' (assigned {current_time - old_timestamp:.1f}s ago)")
                else:
                    voice = None
                    self.logger.warning(f"Invalid assignment type for user {user_id}: {type(assignment)}")
                
                # Validate voice is still available
                if voice and voice in self.available_voices:
                    self.logger.info(f"Returning existing voice '{voice}' for user {user_id} (display_name: {member.display_name}) in guild {guild_id}")
                    return voice
                else:
                    # Invalid assignment, will reassign below
                    self.logger.warning(f"Invalid voice assignment for user {user_id}: {assignment}, reassigning")
                    # Remove invalid assignment
                    guild_assignments.pop(user_id, None)
            
            # Assign new voice for this session (deterministic based on user_id)
            # Use modulo to ensure consistent assignment per user
            voice_index = user_id % len(self.available_voices)
            new_voice = self.available_voices[voice_index]
            
            # Store assignment with timestamp
            guild_assignments[user_id] = {
                "voice": new_voice,
                "timestamp": current_time
            }
            
            # Log assignment with detailed info for debugging
            self.logger.info(
                f"Assigned voice '{new_voice}' (index {voice_index} of {len(self.available_voices)}) "
                f"to user {user_id} (display_name: {member.display_name}) in guild {guild_id} "
                f"at timestamp {current_time}. Calculation: {user_id} % {len(self.available_voices)} = {voice_index}"
            )
            
            # Log all current assignments for debugging (at INFO level to help diagnose live server issues)
            active_assignments = {
                uid: (data.get("voice") if isinstance(data, dict) else data)
                for uid, data in guild_assignments.items()
            }
            self.logger.info(f"Current voice assignments for guild {guild_id}: {active_assignments}")
            
            return new_voice

    # ============ SYSTEM DIAGNOSTICS ============
    def _check_ffmpeg_availability(self):
        """
        Check if FFmpeg is available and log diagnostic information.
        This helps identify system-level issues that could affect audio quality.
        """
        try:
            import subprocess
            import shutil
            
            # Check if FFmpeg is in PATH
            ffmpeg_path = shutil.which('ffmpeg')
            if not ffmpeg_path:
                self.logger.error(
                    "⚠️ FFmpeg not found in PATH! Audio playback will fail.\n"
                    "Install FFmpeg:\n"
                    "  Windows: Download from https://ffmpeg.org/download.html or use: choco install ffmpeg\n"
                    "  Linux: sudo apt-get install ffmpeg\n"
                    "  macOS: brew install ffmpeg"
                )
                return False
            
            self.logger.info(f"FFmpeg found at: {ffmpeg_path}")
            
            # Try to get FFmpeg version and codec information
            try:
                result = subprocess.run(
                    ['ffmpeg', '-version'],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    errors='ignore'
                )
                if result.returncode == 0:
                    version_line = result.stdout.split('\n')[0] if result.stdout else "Unknown"
                    self.logger.info(f"FFmpeg version: {version_line}")
                else:
                    self.logger.warning(f"FFmpeg version check failed: {result.stderr}")
            except subprocess.TimeoutExpired:
                self.logger.warning("FFmpeg version check timed out")
            except Exception as e:
                self.logger.warning(f"Could not check FFmpeg version: {e}")
            
            # Check for MP3 decode and Opus encode support
            try:
                result = subprocess.run(
                    ['ffmpeg', '-codecs'],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    errors='ignore'
                )
                if result.returncode == 0:
                    has_mp3 = 'mp3' in result.stdout.lower() or 'libmp3lame' in result.stdout.lower()
                    has_opus = 'opus' in result.stdout.lower() or 'libopus' in result.stdout.lower()
                    if not has_mp3:
                        self.logger.warning("⚠️ MP3 codec not found in FFmpeg - TTS playback may fail")
                    if not has_opus:
                        self.logger.warning("⚠️ Opus codec not found in FFmpeg - Discord voice may fail")
                    if has_mp3 and has_opus:
                        self.logger.info("FFmpeg has required codecs (MP3, Opus)")
            except Exception as e:
                self.logger.debug(f"Could not check FFmpeg codecs: {e}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error checking FFmpeg availability: {e}", exc_info=True)
            return False

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
        """Pre-compile correction patterns for efficient replacement"""
        # Note: Only include contractions that are unambiguous
        # Excluded "were" because it's a valid past tense word (not always a typo for "we're")
        corrections = {
            r'\bim\b': "I'm", r'\byoure\b': "you're", r'\btheyre\b': "they're",
            r'\bitsnt\b': "isn't", r'\bdoesnt\b': "doesn't",
            r'\bdidnt\b': "didn't", r'\bwont\b': "won't", r'\bcant\b': "can't",
            r'\bshouldnt\b': "shouldn't", r'\bcouldnt\b': "couldn't",
            r'\bwouldnt\b': "wouldn't", r'\bhavent\b': "haven't",
            r'\bhasnt\b': "hasn't", r'\bhadnt\b': "hadn't", r'\barent\b': "aren't",
            r'\bwerent\b': "weren't", r'\bwasnt\b': "wasn't",
            r'\bmustnt\b': "mustn't", r'\bneednt\b': "needn't",
            r'\boughtnt\b': "oughtn't", r'\bshant\b': "shan't",
        }
        # Pre-compile all patterns once at initialization (more efficient than compiling on each use)
        return [(re.compile(p, re.IGNORECASE), r) for p, r in corrections.items()]
    
    def _detect_needs_pronunciation_help(self, text: str) -> bool:
        """Check if text needs AI pronunciation help using pre-compiled pattern"""
        return bool(self._pronunciation_pattern.search(text))

    async def _improve_pronunciation(self, text: str) -> str:
        """Use AI to improve pronunciation"""
        # Check cache
        cached = await self.pronunciation_cache.get(text)
        if cached:
            return cached

        prompt = (
            "Rewrite this text ONLY to improve pronunciation for text-to-speech. "
            "Only expand very short acronyms (2-4 letters) into their letter names (e.g., 'JKM' → 'Jay Kay Em'). "
            "Convert complex usernames/gamertags to speakable form (e.g., 'xXDarkLordXx' → 'Dark Lord'). "
            "DO NOT expand normal capitalized words or sentences - leave them as-is. "
            "Keep all other words exactly the same. Don't change grammar, meaning, or add extra words.\n\n"
            f"Text: {text}\n\nImproved:"
        )
        headers = self._get_openai_headers()
        estimated_tokens = int(len(text) / 4 * 1.5)
        max_tokens = min(2000, max(200, estimated_tokens))
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.1
        }
        self.logger.debug(f"Pronunciation improvement API: input_length={len(text)}, max_tokens={max_tokens}")

        for attempt in range(2):  # Retry once on "Event loop is closed"
            try:
                session = await self.bot.http_mgr.get_session(timeout=10)
                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
                    json=payload,
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        try:
                            improved = result["choices"][0]["message"]["content"].strip()
                        except (KeyError, TypeError, IndexError):
                            return text
                        improved = improved.replace("Improved:", "").strip()
                        final_text = improved if improved else text
                        await self.pronunciation_cache.set(text, final_text)
                        return final_text
                    else:
                        self.logger.warning(f"Pronunciation improvement API returned {resp.status}")
                        return text
            except RuntimeError as e:
                if "Event loop is closed" in str(e) and attempt == 0:
                    self.logger.debug("Pronunciation improvement: session tied to closed loop, invalidating and retrying")
                    try:
                        await self.bot.http_mgr.invalidate_session()
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
                    continue
                self.logger.debug(f"Pronunciation improvement failed: {e}")
                return text
            except asyncio.TimeoutError:
                self.logger.debug("Pronunciation improvement timed out, using original text")
                return text
            except Exception as e:
                self.logger.debug(f"Pronunciation improvement failed: {e}")
                return text
        return text

    def _apply_corrections(self, text: str) -> str:
        """Apply grammar corrections using pre-compiled patterns"""
        for pattern, replacement in self._compiled_corrections:
            text = pattern.sub(replacement, text)
        # Normalize whitespace after corrections
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
        if not text or max_length <= 0:
            return "" if not text else text
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
        Safe against None, invalid types, and malformed input.
        
        Args:
            text: Text to clean
            max_length: Optional max length (None = no truncation, used for splitting)
        
        OpenAI TTS API supports up to 4096 characters per request.
        """
        if text is None or not isinstance(text, str):
            return ""
        original_length = len(text)
        # Normalize excessive formatting: multiple newlines, dashes, etc.
        # Pre-compiled patterns for better performance (used in every message)
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
        if not text:
            return ""
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
        if not text or not isinstance(text, str):
            return []
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
        """Generate cache key using SHA256 to avoid collisions"""
        # Include format in key to avoid serving wrong format from cache after format changes
        key_str = f"opus:{voice}:{text}"
        return hashlib.sha256(key_str.encode('utf-8')).hexdigest()

    def _normalize_text_for_api(self, text: str) -> str:
        """
        Ensure text is valid for API: UTF-8 safe, no null bytes, trimmed.
        Prevents API rejections from malformed Unicode.
        """
        if not text or not isinstance(text, str):
            return ""
        # Replace null bytes and other problematic control chars
        text = text.replace("\x00", " ").replace("\r", "")
        # Ensure valid UTF-8 (replace invalid sequences with replacement char)
        try:
            text.encode("utf-8").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        return text.strip()

    async def _generate_tts(self, text: str, voice: str = None) -> Optional[bytes]:
        """
        Generate TTS audio with retry logic, defensive validation, and proper error handling.
        
        Professional patterns: exponential backoff for 429/5xx, Retry-After header support,
        voice validation, UTF-8 normalization, empty response validation.
        """
        text = self._normalize_text_for_api(text)
        if not text:
            return None
        if not await self.circuit_breaker.can_attempt():
            self.logger.debug("Circuit breaker open, skipping TTS request")
            return None

        voice = voice or self.default_voice
        if voice not in self.available_voices:
            self.logger.warning(f"Invalid voice '{voice}', using default")
            voice = self.default_voice
        cache_key = self._cache_key(text, voice)

        # Check cache
        cached = await self.cache.get(cache_key)
        if cached:
            self.total_cached += 1
            return cached

        headers = self._get_openai_headers()
        payload = {
            "model": "tts-1-hd",
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": TTS_SPEED
        }
        self.logger.debug(f"Sending to TTS API: length={len(text)}, voice={voice}")

        text_timeout = (len(text) / 100 * TTS_API_TIMEOUT_PER_100_CHARS) + TTS_API_TIMEOUT_BASE
        tts_timeout = max(TTS_API_TIMEOUT_BASE, min(TTS_API_TIMEOUT_MAX, text_timeout))

        last_error: Optional[Exception] = None
        for attempt in range(TTS_API_RETRY_MAX_ATTEMPTS):
            try:
                self.total_requests += 1
                session = await self.bot.http_mgr.get_session()
                request_timeout = aiohttp.ClientTimeout(total=tts_timeout)
                async with session.post(
                    self.tts_url, json=payload, headers=headers, timeout=request_timeout
                ) as resp:
                    if resp.status == 200:
                        audio = await resp.read()
                        if not audio or len(audio) < MIN_VALID_AUDIO_SIZE:
                            self.logger.error(
                                f"TTS API returned empty or too-small response: {len(audio) if audio else 0} bytes"
                            )
                            await self.circuit_breaker.record_failure()
                            self.total_failed += 1
                            return None
                        await self.cache.set(cache_key, audio)
                        await self.circuit_breaker.record_success()
                        return audio

                    # Retryable status codes
                    if resp.status in (429, 500, 502, 503):
                        error_body = await resp.text()
                        retry_after = None
                        if resp.status == 429 and "Retry-After" in resp.headers:
                            try:
                                retry_after = float(resp.headers["Retry-After"])
                                retry_after = min(retry_after, TTS_API_RETRY_MAX_DELAY)
                            except (ValueError, TypeError):
                                pass
                        delay = retry_after or (
                            TTS_API_RETRY_BASE_DELAY * (2 ** attempt)
                        )
                        if attempt < TTS_API_RETRY_MAX_ATTEMPTS - 1:
                            self.logger.warning(
                                f"TTS API {resp.status}: {error_body[:200]}. "
                                f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{TTS_API_RETRY_MAX_ATTEMPTS})"
                            )
                            await asyncio.sleep(delay)
                            continue
                        self.logger.error(f"TTS API error {resp.status} after retries: {error_body[:300]}")
                    else:
                        error_body = await resp.text()
                        self.logger.error(f"TTS API error {resp.status}: {error_body[:300]}")
                    await self.circuit_breaker.record_failure()
                    self.total_failed += 1
                    return None

            except asyncio.TimeoutError as e:
                last_error = e
                self.logger.warning(f"TTS request timeout (attempt {attempt + 1}/{TTS_API_RETRY_MAX_ATTEMPTS})")
                if attempt < TTS_API_RETRY_MAX_ATTEMPTS - 1:
                    delay = TTS_API_RETRY_BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                self.logger.error("TTS request timeout after retries")
            except (aiohttp.ClientError, aiohttp.ClientConnectorError, ConnectionError) as e:
                last_error = e
                self.logger.warning(
                    f"TTS connection error: {e} (attempt {attempt + 1}/{TTS_API_RETRY_MAX_ATTEMPTS})"
                )
                if attempt < TTS_API_RETRY_MAX_ATTEMPTS - 1:
                    delay = TTS_API_RETRY_BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                self.logger.error(f"TTS connection error after retries: {e}", exc_info=True)
            except RuntimeError as e:
                if "Event loop is closed" in str(e) and attempt < TTS_API_RETRY_MAX_ATTEMPTS - 1:
                    self.logger.warning(
                        f"TTS session tied to closed loop, invalidating and retrying (attempt {attempt + 1}/{TTS_API_RETRY_MAX_ATTEMPTS})"
                    )
                    try:
                        await self.bot.http_mgr.invalidate_session()
                    except Exception:
                        pass
                    await asyncio.sleep(TTS_API_RETRY_BASE_DELAY)
                    continue
                last_error = e
                self.logger.error(f"TTS request error: {e}", exc_info=True)
                break
            except Exception as e:
                last_error = e
                self.logger.error(f"TTS request error: {e}", exc_info=True)
                break

            await self.circuit_breaker.record_failure()
            self.total_failed += 1
            return None

        if last_error:
            await self.circuit_breaker.record_failure()
            self.total_failed += 1
        return None

    # ============ AUDIO PLAYBACK ============
    async def _play_audio(self, vc: disnake.VoiceClient, audio_data: bytes) -> bool:
        """
        Play audio through Discord voice client with proper cleanup and timeout handling.
        
        Uses MP3 from API → FFmpegPCMAudio (decode to 48kHz PCM) → PCMVolumeTransformer → library encodes to Opus.
        This avoids Opus→Opus re-encoding in FFmpegOpusAudio which caused slow playback and double-voice artifacts.
        """
        temp_file = None

        try:
            if not vc.is_connected():
                return False

            # Wait for current audio to finish
            if vc.is_playing():
                for _ in range(AUDIO_FINISH_WAIT_MAX_ATTEMPTS):
                    if not vc.is_playing():
                        break
                    await asyncio.sleep(AUDIO_FINISH_WAIT_INTERVAL)
                else:
                    vc.stop()
                    await asyncio.sleep(AUDIO_FINISH_WAIT_INTERVAL * 2)  # Brief pause after stopping

            # Create temp file (MP3) - delete=False so we control cleanup; always cleanup in finally/after
            try:
                with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                    f.write(audio_data)
                    temp_file = f.name
            except OSError as e:
                self.logger.error(f"Failed to create temp file for TTS playback: {e}")
                return False

            # MP3 → FFmpeg decodes to PCM 48kHz stereo → PCMVolumeTransformer applies volume → library encodes to Opus once.
            # No Opus re-encode = no slow/double-voice artifacts.
            try:
                pcm_source = disnake.FFmpegPCMAudio(
                    temp_file,
                    before_options='-nostdin',
                    options='-vn'
                )
                audio = disnake.PCMVolumeTransformer(pcm_source, volume=AUDIO_VOLUME_MULTIPLIER)
            except Exception as e:
                self.logger.error(
                    f"Failed to create audio source: {e}\n"
                    f"  - FFmpeg not installed or not in PATH\n"
                    f"  - Missing MP3 codec support\n"
                    f"  - Permission issues: {temp_file}"
                )
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except Exception:
                        pass
                return False

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

            # Start playback
            vc.play(audio, after=after)
            
            # Wait for playback to start (with small delay to allow initialization)
            # This prevents the "buffering then speedup" issue by giving FFmpeg time to initialize
            playback_started = False
            for attempt in range(AUDIO_START_CHECK_MAX_ATTEMPTS):
                if vc.is_playing():
                    playback_started = True
                    break
                # Small delay on first few attempts to allow audio source to initialize
                if attempt < AUDIO_START_CHECK_INITIAL_DELAYS:
                    await asyncio.sleep(AUDIO_PLAYBACK_START_DELAY / AUDIO_START_CHECK_INITIAL_DELAYS)
                else:
                    await asyncio.sleep(AUDIO_FINISH_WAIT_INTERVAL)
            
            if not playback_started:
                # Playback didn't start - stop and cleanup
                vc.stop()
                if temp_file and os.path.exists(temp_file):
                    os.unlink(temp_file)
                return False

            # Wait for playback to complete
            try:
                # Calculate dynamic timeout based on audio length
                # MP3: estimate duration from file size (~16000 bytes/sec at 128kbps)
                estimated_duration = len(audio_data) / MP3_BYTES_PER_SECOND
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
                
                # Verify playback actually completed (callback was called, so assume success)
                # Log warning if disconnected, but still return success (callback was called)
                if not vc.is_connected():
                    self.logger.warning("Voice client disconnected during playback")
                return True
            except asyncio.TimeoutError:
                self.logger.warning(f"Playback timeout after {timeout:.1f}s (estimated {estimated_duration:.1f}s), stopping")
                vc.stop()
                # Clean up temp file on timeout (callback might not run)
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except Exception:
                        pass
                return False

        except Exception as e:
            self.logger.error(f"Playback error: {e}", exc_info=True)
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except Exception:
                    pass
            return False

    # ============ VOICE CHANNEL HELPERS ============
    def _has_humans_in_voice(self, channel: Optional[disnake.VoiceChannel]) -> bool:
        """
        Check if voice channel has any human (non-bot) members.
        
        Args:
            channel: Voice channel to check, or None
            
        Returns:
            True if channel exists and has at least one human member
        """
        return bool(channel and any(not m.bot for m in channel.members))

    # ============ VOICE CONNECTION ============
    async def _connect_to_voice(self, channel: disnake.VoiceChannel, timeout: int = 10) -> Optional[disnake.VoiceClient]:
        """
        Connect to voice channel with retries and robust edge-case handling.
        
        Handles: already-connected, stale clients, ClientException, OSError.
        Professional pattern: verify channel still exists and has humans before connecting.
        """
        guild = channel.guild
        if not guild:
            return None

        vc = guild.voice_client

        # Already connected to this exact channel - verify and return
        if vc and vc.is_connected():
            ch = getattr(vc, "channel", None)
            if ch and ch.id == channel.id:
                return vc
            # Connected elsewhere - disconnect first
            try:
                await vc.disconnect()
            except Exception as e:
                self.logger.debug(f"Disconnect during reconnect: {e}")
            await asyncio.sleep(VOICE_CLEANUP_DELAY)

        # Cleanup stale/invalid voice client (e.g. not connected but not cleaned)
        if vc and not vc.is_connected():
            try:
                vc.cleanup()
            except Exception:
                pass
            vc = None
            await asyncio.sleep(VOICE_CLEANUP_DELAY)

        max_attempts = 4  # Extra attempt for stubborn "already connected" cases
        for attempt in range(max_attempts):
            try:
                vc = await asyncio.wait_for(
                    channel.connect(timeout=timeout, reconnect=False),
                    timeout=timeout + 5
                )
                self.logger.info(f"Connected to {channel.name} (attempt {attempt + 1})")
                try:
                    await guild.change_voice_state(channel=channel, self_deaf=True)
                except Exception:
                    pass
                return vc
            except disnake.ClientException as e:
                err_lower = str(e).lower()
                if "already connected" in err_lower:
                    vc = guild.voice_client
                    if vc and vc.is_connected():
                        ch = getattr(vc, "channel", None)
                        if ch and ch.id == channel.id:
                            return vc
                    try:
                        if vc:
                            await vc.disconnect(force=True)
                    except Exception:
                        pass
                    await asyncio.sleep(VOICE_CLEANUP_DELAY * 2)
                else:
                    self.logger.warning(f"Voice ClientException: {e}")
                    if attempt == max_attempts - 1:
                        return None
            except (OSError, asyncio.TimeoutError) as e:
                self.logger.warning(f"Voice connection error: {e}")
                if attempt == max_attempts - 1:
                    return None
            except Exception as e:
                self.logger.error(f"Voice connection failed: {e}", exc_info=True)
                if attempt == max_attempts - 1:
                    return None
            await asyncio.sleep(VOICE_CONNECTION_RETRY_DELAY)
        return None

    # ============ STATE MANAGEMENT ============
    async def _get_or_create_state(self, guild_id: int) -> GuildVoiceState:
        """Get or create guild voice state"""
        async with self._state_lock:
            return self.guild_states.setdefault(
                guild_id, GuildVoiceState(guild_id, self.logger, self.max_queue_size)
            )

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
                    estimated_duration = len(item.audio_data) / MP3_BYTES_PER_SECOND
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
                        # Re-queue prepared item so it isn't lost (pipeline had removed it from queue)
                        if prepared_item:
                            try:
                                state.queue.put_nowait(prepared_item)
                            except asyncio.QueueFull:
                                state.stats["dropped"] += 1
                            prepared_item = None
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
                            if prepared_item:
                                try:
                                    state.queue.put_nowait(prepared_item)
                                except asyncio.QueueFull:
                                    state.stats["dropped"] += 1
                            prepared_item = None  # Clear on error
                    
                    if playback_success:
                        state.stats["processed"] += 1
                        self.logger.debug("Audio playback completed successfully")
                    else:
                        state.stats["errors"] += 1
                        self.logger.warning("Audio playback failed")
                    
                    # Check if anyone is still in voice channel after playback
                    # (users might have left during playback)
                    if (vc := guild.voice_client) and vc.is_connected() and (ch := vc.channel):
                        if not self._has_humans_in_voice(ch):
                            self.logger.info(f"No humans left in voice channel after playback, disconnecting from {ch.name}")
                            await vc.disconnect()
                            break  # Exit queue processing loop

                except asyncio.TimeoutError:
                    break
                except Exception as e:
                    self.logger.error(f"Queue processing error: {e}", exc_info=True)
                    state.stats["errors"] += 1
                    if prepared_item:
                        try:
                            state.queue.put_nowait(prepared_item)
                        except asyncio.QueueFull:
                            state.stats["dropped"] += 1
                    prepared_item = None  # Clear prepared item on error
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass
        finally:
            state.is_processing = False

            # Disconnect if no humans
            if (vc := guild.voice_client) and vc.is_connected() and (ch := vc.channel):
                if not self._has_humans_in_voice(ch):
                    await vc.disconnect()

            await self._remove_state(guild_id)

    # ============ MESSAGE PROCESSING ============
    async def _should_process_message(self, message: disnake.Message) -> bool:
        """Check if message should be processed"""
        if not self.enabled or message.author.bot or not message.guild:
            return False

        # Check duplicate (defensive: content can be None for embed-only messages)
        content_slice = (message.content or "")[:50]
        message_key = f"{message.id}:{message.author.id}:{content_slice}"
        async with self._processed_messages_lock:
            if message_key in self._processed_messages:
                return False
            self._processed_messages.add(message_key)

        # Check channel restriction
        if self.allowed_channel is not None and message.channel.id != self.allowed_channel:
            return False

        # Check voice
        if not message.author.voice or not message.author.voice.channel:
            return False

        # Check role (guard None/empty roles)
        author_roles = getattr(message.author, "roles", None) or []
        if self.tts_role_id:
            if not any(getattr(r, "id", None) == self.tts_role_id for r in author_roles):
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

        # Name announcement: check if this session warrants "X says:" prefix (2-hour cooldown)
        # We only record the announcement AFTER we successfully queue, so empty/filtered messages don't consume it
        guild_id = message.guild.id
        user_id = message.author.id
        current_time = time.time()
        
        async with self._announcement_lock:
            if guild_id not in self._announced_users:
                self._announced_users[guild_id] = {}
            last_announcement = self._announced_users[guild_id].get(user_id, 0)
            time_since_announcement = current_time - last_announcement
            is_first_message = (last_announcement == 0) or (time_since_announcement >= NAME_ANNOUNCEMENT_COOLDOWN)

        # Log original message length (content can be None for embed-only messages)
        raw_content = message.content or ""
        original_content_length = len(raw_content)
        self.logger.debug(f"Processing message from {message.author.display_name}: original length={original_content_length} chars")
        
        # Clean text: emoji extraction, Discord formatting removal, whitespace normalization,
        # grammar corrections, and pronunciation improvement for acronyms/usernames in body
        cleaned_text = await self._clean_text(raw_content, max_length=None)
        if not cleaned_text or not cleaned_text.strip():
            self.logger.debug("Cleaned text is empty, skipping")
            return
        
        if is_first_message:
            display_name = message.author.display_name
            pronounceable_name = await self._improve_pronunciation(display_name) if self._detect_needs_pronunciation_help(display_name) else display_name
            prefix = f"{pronounceable_name} says: "
            text = prefix + cleaned_text
            self.logger.debug(f"First message: added prefix '{prefix}' (len={len(prefix)}), total length={len(text)}")
        else:
            text = cleaned_text
            self.logger.debug(f"Cleaned text length={len(text)}")

        # Get voice
        user_voice = await self._get_voice_for_user(message.author)

        # Split into chunks if needed (4000 chars per chunk to leave buffer)
        text_chunks = self._split_text_into_chunks(text, max_chunk_size=4000)
        
        # Safety check: Ensure each chunk doesn't exceed API limit
        # (Split should already handle this, but double-check for safety)
        text_chunks = [
            self._ensure_text_length(chunk, max_length=OPENAI_TTS_MAX_CHARS_PER_REQUEST)
            for chunk in text_chunks
        ]

        # Queue all chunks sequentially
        state = await self._get_or_create_state(guild_id)
        chunks_queued = 0
        channel_id = message.author.voice.channel.id
        
        for i, chunk in enumerate(text_chunks, 1):
            if not chunk or len(chunk) < 2:
                continue
            
            try:
                state.queue.put_nowait(TTSQueueItem(
                    user_id=message.author.id,
                    channel_id=channel_id,
                    text=chunk,
                    voice=user_voice,
                    timestamp=time.time()
                ))
                chunks_queued += 1
                self.logger.debug(f"Queued chunk {i}/{len(text_chunks)}: length={len(chunk)} chars")
            except asyncio.QueueFull:
                state.stats["dropped"] += 1
                self.logger.warning(f"Queue full, dropping TTS chunk {i} for user {message.author.id}")
                break

        self.logger.info(f"Message processing complete: {original_content_length} chars → {len(text_chunks)} chunks → {chunks_queued} queued")

        # Record name announcement only when we actually queued (so empty/filtered messages don't consume it)
        if chunks_queued > 0 and is_first_message:
            async with self._announcement_lock:
                if guild_id not in self._announced_users:
                    self._announced_users[guild_id] = {}
                self._announced_users[guild_id][user_id] = current_time

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
        guild = getattr(member, "guild", None)
        if not guild:
            return
        
        # User left voice channel
        if before.channel and not after.channel:
            # Clear announcement status so they get announced again when they rejoin
            # The 2-hour cooldown only applies within the same VC session
            async with self._announcement_lock:
                if guild.id in self._announced_users:
                    self._announced_users[guild.id].pop(member.id, None)
            
            # Clear voice assignment for this session (user left voice channel)
            # This ensures they get a fresh assignment when they rejoin, similar to announcement reset
            async with self._voice_lock:
                if guild.id in self._voice_assignments:
                    old_assignment = self._voice_assignments[guild.id].pop(member.id, None)
                    if old_assignment:
                        voice_name = old_assignment.get("voice") if isinstance(old_assignment, dict) else old_assignment
                        timestamp = old_assignment.get("timestamp") if isinstance(old_assignment, dict) else None
                        self.logger.info(
                            f"Cleared voice assignment '{voice_name}' for user {member.id} "
                            f"(display_name: {member.display_name}) in guild {guild.id} "
                            f"(left VC, assignment was {time.time() - timestamp:.1f}s old)" if timestamp else f"(left VC)"
                        )
            
            # Check if should disconnect (wait to avoid race conditions)
            # Uses module-level VOICE_DISCONNECT_DELAY constant
            if (vc := guild.voice_client) and vc.is_connected() and (ch := vc.channel) and not vc.is_playing():
                if not self._has_humans_in_voice(ch):
                    await asyncio.sleep(VOICE_DISCONNECT_DELAY)
                    if vc.is_connected() and (ch := vc.channel) and not vc.is_playing():
                        if not self._has_humans_in_voice(ch):
                            await vc.disconnect()
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

                # Cleanup message deduplication (keep most recent 500 entries)
                async with self._processed_messages_lock:
                    if len(self._processed_messages) > 1000:
                        # Keep only the most recent 500 entries (simple approach: clear and let it rebuild)
                        # More efficient than trying to track insertion order
                        self._processed_messages.clear()

                # Cleanup voice assignments for users not in VC (per-guild)
                async with self._voice_lock:
                    for guild_id, guild_assignments in list(self._voice_assignments.items()):
                        if not (guild := self.bot.get_guild(guild_id)):
                            del self._voice_assignments[guild_id]
                            continue
                        
                        # Remove assignments for users not in voice channel
                        users_to_remove = [
                            user_id for user_id in guild_assignments
                            if not (m := guild.get_member(user_id)) or not m.voice or not m.voice.channel
                        ]
                        for user_id in users_to_remove:
                            guild_assignments.pop(user_id, None)
                        
                        # Remove empty guild dicts
                        if not guild_assignments:
                            self._voice_assignments.pop(guild_id, None)

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

    async def daily_maintenance(self):
        """Called by main at midnight UTC — clear expired cache entries."""
        if not self.enabled:
            return
        if hasattr(self, "cache") and hasattr(self.cache, "cleanup"):
            await self.cache.cleanup()
        if hasattr(self, "pronunciation_cache") and hasattr(self.pronunciation_cache, "cleanup"):
            await self.pronunciation_cache.cleanup()
        self.logger.debug("Voice: daily cache cleanup done")

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

        # Cleanup orphaned states (release lock before _remove_state to avoid deadlock)
        async with self._state_lock:
            orphaned = [
                gid for gid in list(self.guild_states.keys())
                if not (guild := self.bot.get_guild(gid)) or not guild.voice_client
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
    @manage_guild_check()
    async def tts_disconnect(self, inter: disnake.ApplicationCommandInteraction):
        """Force disconnect"""
        await inter.response.defer(ephemeral=True)
        if not self.enabled:
            await inter.edit_original_response(content="❌ TTS is disabled")
            return
        if not inter.guild:
            await inter.edit_original_response(content="❌ Use this in a server")
            return
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
    @manage_guild_check()
    async def tts_clear(self, inter: disnake.ApplicationCommandInteraction):
        """Clear queue - drains until empty (reliable for async queues)"""
        await inter.response.defer(ephemeral=True)
        if not self.enabled:
            await inter.edit_original_response(content="❌ TTS is disabled")
            return
        if not inter.guild:
            await inter.edit_original_response(content="❌ Use this in a server")
            return
        async with self._state_lock:
            if inter.guild.id in self.guild_states:
                state = self.guild_states[inter.guild.id]
                cleared = 0
                while True:
                    try:
                        state.queue.get_nowait()
                        cleared += 1
                    except asyncio.QueueEmpty:
                        break
                await inter.edit_original_response(
                    content=f"✅ Queue cleared ({cleared} item{'s' if cleared != 1 else ''} removed)"
                )
            else:
                await inter.edit_original_response(content="❌ No active queue")

    @tts_cmd.sub_command(name="status", description="Check voice channel status")
    async def tts_status(self, inter: disnake.ApplicationCommandInteraction):
        """Check voice status"""
        await inter.response.defer(ephemeral=True)
        if not self.enabled:
            await inter.edit_original_response(content="❌ TTS is disabled")
            return
        if not inter.guild:
            await inter.edit_original_response(content="❌ Use this in a server")
            return
        if not inter.guild.voice_client:
            await inter.edit_original_response(content="❌ Bot not connected to voice")
            return

        vc = inter.guild.voice_client
        channel = vc.channel
        
        if not channel:
            await inter.edit_original_response(content="❌ No voice channel found")
            return

        humans = [m for m in channel.members if not m.bot]
        bots = [m for m in channel.members if m.bot]  # Note: bots list kept for display purposes
        
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

    @tts_cmd.sub_command(name="diagnostics", description="Check system diagnostics (FFmpeg, codecs, etc.)")
    async def tts_diagnostics(self, inter: disnake.ApplicationCommandInteraction):
        """Check system diagnostics"""
        await inter.response.defer(ephemeral=True)
        if not self.enabled:
            await inter.edit_original_response(content="❌ TTS is disabled")
            return
        embed = disnake.Embed(title="🔍 TTS System Diagnostics", color=disnake.Color.blue())
        
        # Check FFmpeg
        try:
            import subprocess
            import shutil
            
            ffmpeg_path = shutil.which('ffmpeg')
            if ffmpeg_path:
                embed.add_field(
                    name="✅ FFmpeg",
                    value=f"Found at: `{ffmpeg_path}`",
                    inline=False
                )
                
                # Get version
                try:
                    result = subprocess.run(
                        ['ffmpeg', '-version'],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        errors='ignore'
                    )
                    if result.returncode == 0:
                        version_line = result.stdout.split('\n')[0] if result.stdout else "Unknown"
                        embed.add_field(
                            name="FFmpeg Version",
                            value=f"`{version_line[:100]}`",
                            inline=False
                        )
                except Exception as e:
                    embed.add_field(
                        name="⚠️ FFmpeg Version",
                        value=f"Could not check: {e}",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="❌ FFmpeg",
                    value="**NOT FOUND**\nInstall FFmpeg:\n• Windows: `choco install ffmpeg`\n• Linux: `sudo apt-get install ffmpeg`\n• macOS: `brew install ffmpeg`",
                    inline=False
                )
        except Exception as e:
            embed.add_field(
                name="❌ FFmpeg Check Failed",
                value=f"Error: {e}",
                inline=False
            )
        
        # Check codecs
        try:
            import subprocess
            result = subprocess.run(
                ['ffmpeg', '-codecs'],
                capture_output=True,
                text=True,
                timeout=5,
                errors='ignore'
            )
            if result.returncode == 0:
                has_mp3 = 'mp3' in result.stdout.lower() or 'libmp3lame' in result.stdout.lower()
                has_opus = 'opus' in result.stdout.lower() or 'libopus' in result.stdout.lower()
                codec_status = []
                codec_status.append(f"{'✅' if has_mp3 else '❌'} MP3 codec: {'Available' if has_mp3 else 'Missing'}")
                codec_status.append(f"{'✅' if has_opus else '❌'} Opus codec: {'Available' if has_opus else 'Missing'}")
                
                embed.add_field(
                    name="Audio Codecs",
                    value="\n".join(codec_status),
                    inline=False
                )
        except Exception as e:
            embed.add_field(
                name="⚠️ Codec Check",
                value=f"Could not check: {e}",
                inline=False
            )
        
        # TTS Status
        embed.add_field(
            name="TTS Status",
            value=f"Enabled: {'✅ Yes' if self.enabled else '❌ No'}\n"
                  f"Total Requests: {self.total_requests:,}\n"
                  f"Failed: {self.total_failed:,}\n"
                  f"Cached: {self.total_cached:,}",
            inline=False
        )
        
        embed.set_footer(text="If you see errors, check logs for 'policy.d denied' or missing dependencies")
        
        await inter.edit_original_response(embed=embed)


def setup(bot):
    """Setup the cog"""
    bot.add_cog(VoiceProcessingCog(bot))
